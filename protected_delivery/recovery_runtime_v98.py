from protected_delivery.recovery_idempotent_v95 import IdempotentRecoveryExecutorV95, IdempotentRecoveryIntentV95
from protected_delivery.recovery_replay_v88 import replay_key
from protected_delivery.recovery_transaction_idempotent_v96 import IdempotentRecoveryTransactionV96
from protected_delivery.recovery_transaction_reconcile_v97 import RecoveryTransactionReconcilerV97
from protected_delivery.recovery_v82 import AutonomousRecoveryV82, FailureSignal

VERSION = "9.8"


def _next_attempt(ledger, job_id: int) -> int:
    prior = [event.attempt for event in ledger if event.job_id == job_id]
    return max(prior) + 1 if prior else 0


class RecoveryRuntimeSupervisorV98:
    """Route a recovery request into fresh execution or crash reconciliation.

    V9.8 is the restart-safe runtime entry point above V9.6 and V9.7. It
    derives the exact replay identity from durable state, inspects the V9.3
    journal, and never blindly replays a crash-left action:

    * no journal record -> execute a fresh V9.6 transaction;
    * PREPARED -> reconcile only through the V9.5 downstream lookup contract,
      then repair durable evidence/lease through V9.7;
    * COMMITTED -> repair durable evidence/lease through V9.7 without calling
      the external mutation;
    * ABORTED/identity mismatch/active foreign lease -> fail closed.
    """

    def __init__(self, *, store, journal, gateway, owner: str, nonce: str, lease_ttl: int = 120):
        if not owner.strip() or not nonce.strip():
            raise ValueError("invalid_runtime_identity")
        if lease_ttl < 1:
            raise ValueError("invalid_lease_ttl")
        self.store = store
        self.journal = journal
        self.gateway = gateway
        self.owner = owner.strip()
        self.nonce = nonce.strip()
        self.transaction = IdempotentRecoveryTransactionV96(
            store=store,
            journal=journal,
            gateway=gateway,
            owner=self.owner,
            nonce=self.nonce,
            lease_ttl=lease_ttl,
        )
        self.executor = IdempotentRecoveryExecutorV95(journal=journal, gateway=gateway)
        self.reconciler = RecoveryTransactionReconcilerV97(
            store=store,
            journal=journal,
            owner=self.owner,
            nonce=self.nonce,
        )

    @staticmethod
    def _validate_journal_identity(journaled, *, pr_number: int, head_sha: str,
                                   workflow: str, job_id: int, action: str, attempt: int, key: str) -> None:
        if journaled.replay_key != key:
            raise RuntimeError("runtime_replay_key_mismatch")
        if journaled.pr_number != pr_number or journaled.head_sha != head_sha:
            raise RuntimeError("runtime_journal_binding_mismatch")
        if (
            journaled.workflow != workflow.strip()
            or journaled.job_id != job_id
            or journaled.action != action.strip()
            or journaled.attempt != attempt
        ):
            raise RuntimeError("runtime_journal_identity_mismatch")

    def run(self, *, pr_number: int, expected_head_sha: str, current_head_sha: str,
            workflow: str, job_id: int, conclusion: str, log_excerpt: str = "",
            now: int, max_attempts: int = 2) -> dict:
        if expected_head_sha != current_head_sha:
            raise RuntimeError("stale_pr_head")
        if now < 0:
            raise ValueError("invalid_runtime_time")

        state = self.store.read(pr_number=pr_number, head_sha=expected_head_sha)
        if state is None:
            result = self.transaction.execute(
                pr_number=pr_number,
                expected_head_sha=expected_head_sha,
                current_head_sha=current_head_sha,
                workflow=workflow,
                job_id=job_id,
                conclusion=conclusion,
                log_excerpt=log_excerpt,
                now=now,
                max_attempts=max_attempts,
            )
            return {**result, "runtime_version": VERSION, "runtime_mode": "FRESH_TRANSACTION"}

        attempt = _next_attempt(state.ledger, job_id)
        decision = AutonomousRecoveryV82.next_action(
            FailureSignal(workflow, conclusion, log_excerpt), attempt, max_attempts
        )
        key = replay_key(
            pr_number=pr_number,
            head_sha=expected_head_sha,
            workflow=workflow.strip(),
            job_id=job_id,
            action=decision.action,
            attempt=attempt,
        )
        journaled = self.journal.read(key)

        if journaled is None:
            result = self.transaction.execute(
                pr_number=pr_number,
                expected_head_sha=expected_head_sha,
                current_head_sha=current_head_sha,
                workflow=workflow,
                job_id=job_id,
                conclusion=conclusion,
                log_excerpt=log_excerpt,
                now=now,
                max_attempts=max_attempts,
            )
            return {**result, "runtime_version": VERSION, "runtime_mode": "FRESH_TRANSACTION"}

        self._validate_journal_identity(
            journaled,
            pr_number=pr_number,
            head_sha=expected_head_sha,
            workflow=workflow,
            job_id=job_id,
            action=decision.action,
            attempt=attempt,
            key=key,
        )

        if state.lease is not None:
            owned = state.lease.owner == self.owner and state.lease.nonce == self.nonce
            expired = now >= state.lease.expires_at
            if not owned and not expired:
                raise RuntimeError("recovery_lease_held")

        if journaled.status == "ABORTED":
            raise RuntimeError("recovery_action_aborted")
        if journaled.status == "PREPARED":
            self.executor.execute(
                IdempotentRecoveryIntentV95(
                    pr_number=pr_number,
                    expected_head_sha=expected_head_sha,
                    current_head_sha=current_head_sha,
                    workflow=workflow,
                    job_id=job_id,
                    action=decision.action,
                    attempt=attempt,
                    replay_key=key,
                    owner=self.owner,
                    lease_key=journaled.lease_key,
                )
            )
            runtime_mode = "PREPARED_LOOKUP_RECONCILIATION"
        elif journaled.status == "COMMITTED":
            runtime_mode = "COMMITTED_STATE_RECONCILIATION"
        else:
            raise RuntimeError("invalid_pending_status")

        result = self.reconciler.reconcile(
            pr_number=pr_number,
            expected_head_sha=expected_head_sha,
            current_head_sha=current_head_sha,
            workflow=workflow,
            job_id=job_id,
            category=decision.category,
            action=decision.action,
            attempt=attempt,
            now=now,
        )
        return {**result, "runtime_version": VERSION, "runtime_mode": runtime_mode}
