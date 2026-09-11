import dataclasses

from protected_delivery.recovery_evidence_v85 import RecoveryEvidenceEventV85
from protected_delivery.recovery_lease_v89 import RecoveryLeaseGuardV89
from protected_delivery.recovery_orchestrator_v84 import WorkflowFailureV84
from protected_delivery.recovery_replay_v88 import RecoveryReplayGuardV88, RecoveryReplayIntentV88
from protected_delivery.recovery_session_v87 import RecoverySessionCoordinatorV87, RecoverySessionV87
from protected_delivery.recovery_state_v90 import RecoveryStateV90, next_state
from protected_delivery.recovery_v82 import AutonomousRecoveryV82, FailureSignal

VERSION = "9.2"


def _next_attempt(ledger: tuple[RecoveryEvidenceEventV85, ...], job_id: int) -> int:
    prior = [event.attempt for event in ledger if event.job_id == job_id]
    return (max(prior) + 1) if prior else 0


class RecoveryTransactionEngineV92:
    """Lease -> replay check -> recovery -> durable evidence -> lease release.

    The engine composes V8.2-V9.1 around a durable compare-and-swap store. It
    acquires an exact-head lease before any recovery mutation, authorizes the
    logical action against accepted evidence, executes one V8.7 recovery
    session, persists the resulting ledger with CAS, then releases the lease.

    The store must expose read(), initialize(), and compare_and_swap() with the
    V9.0 contract. Merge authority remains outside this engine.
    """

    def __init__(self, *, store, gateway, owner: str, nonce: str, lease_ttl: int = 120):
        if not owner.strip() or not nonce.strip():
            raise ValueError("invalid_transaction_identity")
        if lease_ttl < 1:
            raise ValueError("invalid_lease_ttl")
        self.store = store
        self.gateway = gateway
        self.owner = owner.strip()
        self.nonce = nonce.strip()
        self.lease_ttl = lease_ttl
        self.coordinator = RecoverySessionCoordinatorV87(gateway)

    def _load_or_initialize(self, *, pr_number: int, head_sha: str):
        state = self.store.read(pr_number=pr_number, head_sha=head_sha)
        if state is None:
            try:
                state = self.store.initialize(pr_number=pr_number, head_sha=head_sha)
            except RuntimeError as exc:
                if str(exc) != "state_already_initialized":
                    raise
                state = self.store.read(pr_number=pr_number, head_sha=head_sha)
        if state is None:
            raise RuntimeError("recovery_state_unavailable")
        return state

    def execute(
        self,
        *,
        pr_number: int,
        expected_head_sha: str,
        current_head_sha: str,
        failure: WorkflowFailureV84,
        now: int,
        max_attempts: int = 2,
    ) -> dict:
        failure.validate()
        if expected_head_sha != current_head_sha:
            raise RuntimeError("stale_pr_head")
        state = self._load_or_initialize(pr_number=pr_number, head_sha=expected_head_sha)
        if state.head_sha != expected_head_sha:
            raise RuntimeError("state_head_mismatch")

        lease = RecoveryLeaseGuardV89.acquire(
            state.lease,
            pr_number=pr_number,
            expected_head_sha=expected_head_sha,
            current_head_sha=current_head_sha,
            owner=self.owner,
            nonce=self.nonce,
            now=now,
            ttl=self.lease_ttl,
        )
        leased = next_state(state, lease=lease)
        leased = self.store.compare_and_swap(
            expected_generation=state.generation,
            expected_state_hash=state.state_hash,
            replacement=leased,
        )

        attempt = _next_attempt(leased.ledger, failure.job_id)
        signal = FailureSignal(failure.workflow, failure.conclusion, failure.log_excerpt)
        decision = AutonomousRecoveryV82.next_action(signal, attempt, max_attempts)
        replay = RecoveryReplayGuardV88.authorize(
            leased.ledger,
            RecoveryReplayIntentV88(
                pr_number=pr_number,
                expected_head_sha=expected_head_sha,
                current_head_sha=current_head_sha,
                workflow=failure.workflow,
                job_id=failure.job_id,
                action=decision.action,
                attempt=attempt,
            ),
        )

        result = self.coordinator.run(
            RecoverySessionV87(
                pr_number=pr_number,
                expected_head_sha=expected_head_sha,
                current_head_sha=current_head_sha,
                failures=(failure,),
                ledger=leased.ledger,
                attempts_by_job={failure.job_id: attempt},
                max_attempts=max_attempts,
            )
        )
        if result.get("status") != "RECOVERY_ACTION_ACCEPTED":
            raise RuntimeError("recovery_transaction_not_accepted")
        if result.get("action") != decision.action:
            raise RuntimeError("recovery_decision_drift")
        if result.get("ledger_appended") is not True:
            raise RuntimeError("recovery_evidence_not_appended")

        persisted_ledger = tuple(result["ledger"])
        evidenced = next_state(leased, lease=lease, ledger=persisted_ledger)
        evidenced = self.store.compare_and_swap(
            expected_generation=leased.generation,
            expected_state_hash=leased.state_hash,
            replacement=evidenced,
        )

        released = RecoveryLeaseGuardV89.release(
            lease,
            owner=self.owner,
            nonce=self.nonce,
            now=now,
        )
        final_state = RecoveryStateV90(
            pr_number=evidenced.pr_number,
            head_sha=evidenced.head_sha,
            generation=evidenced.generation + 1,
            lease=None,
            ledger=evidenced.ledger,
        ).seal()
        final_state = self.store.compare_and_swap(
            expected_generation=evidenced.generation,
            expected_state_hash=evidenced.state_hash,
            replacement=final_state,
        )
        return {
            **result,
            "version": VERSION,
            "attempt": attempt,
            "replay_key": replay["replay_key"],
            "lease_key": lease.lease_key,
            "lease_release": released["status"],
            "state_generation": final_state.generation,
            "state_hash": final_state.state_hash,
        }
