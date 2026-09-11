from protected_delivery.recovery_evidence_v85 import RecoveryEvidenceLedgerV85
from protected_delivery.recovery_idempotent_v95 import IdempotentRecoveryExecutorV95, IdempotentRecoveryIntentV95
from protected_delivery.recovery_lease_v89 import RecoveryLeaseGuardV89
from protected_delivery.recovery_replay_v88 import RecoveryReplayGuardV88, RecoveryReplayIntentV88
from protected_delivery.recovery_state_v90 import RecoveryStateV90, next_state
from protected_delivery.recovery_v82 import AutonomousRecoveryV82, FailureSignal

VERSION = "9.6"


def _next_attempt(ledger, job_id: int) -> int:
    prior = [event.attempt for event in ledger if event.job_id == job_id]
    return max(prior) + 1 if prior else 0


class IdempotentRecoveryTransactionV96:
    """Durable lease + replay guard + idempotent mutation + evidence commit.

    V9.6 replaces the non-idempotent external mutation in V9.2 with the V9.5
    journaled/idempotent executor while keeping exact-head CAS state, lease and
    append-only evidence semantics. A committed downstream mutation can be
    reconciled after a crash without issuing another logical action.
    """

    def __init__(self, *, store, journal, gateway, owner: str, nonce: str, lease_ttl: int = 120):
        if not owner.strip() or not nonce.strip():
            raise ValueError("invalid_transaction_identity")
        if lease_ttl < 1:
            raise ValueError("invalid_lease_ttl")
        self.store = store
        self.owner = owner.strip()
        self.nonce = nonce.strip()
        self.lease_ttl = lease_ttl
        self.executor = IdempotentRecoveryExecutorV95(journal=journal, gateway=gateway)

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

    def execute(self, *, pr_number: int, expected_head_sha: str, current_head_sha: str,
                workflow: str, job_id: int, conclusion: str, log_excerpt: str = "",
                now: int, max_attempts: int = 2) -> dict:
        if expected_head_sha != current_head_sha:
            raise RuntimeError("stale_pr_head")
        state = self._load_or_initialize(pr_number=pr_number, head_sha=expected_head_sha)
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
        leased = self.store.compare_and_swap(
            expected_generation=state.generation,
            expected_state_hash=state.state_hash,
            replacement=next_state(state, lease=lease),
        )

        attempt = _next_attempt(leased.ledger, job_id)
        decision = AutonomousRecoveryV82.next_action(
            FailureSignal(workflow, conclusion, log_excerpt), attempt, max_attempts
        )
        replay = RecoveryReplayGuardV88.authorize(
            leased.ledger,
            RecoveryReplayIntentV88(
                pr_number=pr_number,
                expected_head_sha=expected_head_sha,
                current_head_sha=current_head_sha,
                workflow=workflow,
                job_id=job_id,
                action=decision.action,
                attempt=attempt,
            ),
        )
        mutation = self.executor.execute(
            IdempotentRecoveryIntentV95(
                pr_number=pr_number,
                expected_head_sha=expected_head_sha,
                current_head_sha=current_head_sha,
                workflow=workflow,
                job_id=job_id,
                action=decision.action,
                attempt=attempt,
                replay_key=replay["replay_key"],
                owner=self.owner,
                lease_key=lease.lease_key,
            )
        )

        ledger = RecoveryEvidenceLedgerV85.append(
            leased.ledger,
            pr_number=pr_number,
            expected_head_sha=expected_head_sha,
            current_head_sha=current_head_sha,
            workflow=workflow,
            job_id=job_id,
            category=decision.category,
            action=decision.action,
            attempt=attempt,
            accepted=True,
        )
        evidenced = self.store.compare_and_swap(
            expected_generation=leased.generation,
            expected_state_hash=leased.state_hash,
            replacement=next_state(leased, lease=lease, ledger=ledger),
        )
        release = RecoveryLeaseGuardV89.release(
            lease, owner=self.owner, nonce=self.nonce, now=now
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
            "status": "IDEMPOTENT_RECOVERY_TRANSACTION_COMMITTED",
            "version": VERSION,
            "pr_number": pr_number,
            "head_sha": expected_head_sha,
            "action": decision.action,
            "category": decision.category,
            "attempt": attempt,
            "receipt_id": mutation["receipt_id"],
            "idempotency_key": replay["replay_key"],
            "reconciled": mutation["reconciled"],
            "lease_release": release["status"],
            "state_generation": final_state.generation,
            "state_hash": final_state.state_hash,
        }
