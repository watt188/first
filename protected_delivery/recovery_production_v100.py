from protected_delivery.recovery_runtime_audit_v99 import RecoveryRuntimeAuditV99
from protected_delivery.recovery_runtime_v98 import RecoveryRuntimeSupervisorV98
from protected_delivery.recovery_v82 import AutonomousRecoveryV82, FailureSignal

VERSION = "10.0"


class ProductionRecoveryRuntimeV100:
    """Audit-before, execute/reconcile, audit-after production recovery facade.

    V10.0 exposes one exact-head entry point over V9.8/V9.9. Every recovery
    request is read-only audited first. Terminal or actively blocked states fail
    closed. Safe states are delegated to V9.8, then the exact logical action is
    audited again and must be CONVERGED before success is returned.
    """

    _BLOCKED = {"BLOCKED_ACTIVE_FOREIGN_LEASE", "TERMINAL_ABORTED"}

    def __init__(self, *, store, journal, gateway, owner: str, nonce: str, lease_ttl: int = 120):
        if not owner.strip() or not nonce.strip():
            raise ValueError("invalid_production_runtime_identity")
        self.store = store
        self.owner = owner.strip()
        self.nonce = nonce.strip()
        self.runtime = RecoveryRuntimeSupervisorV98(
            store=store,
            journal=journal,
            gateway=gateway,
            owner=self.owner,
            nonce=self.nonce,
            lease_ttl=lease_ttl,
        )
        self.audit = RecoveryRuntimeAuditV99(
            store=store,
            journal=journal,
            owner=self.owner,
            nonce=self.nonce,
        )

    def _intent(self, *, pr_number: int, head_sha: str, workflow: str, job_id: int,
                conclusion: str, log_excerpt: str, max_attempts: int) -> tuple[str, int, str]:
        state = self.store.read(pr_number=pr_number, head_sha=head_sha)
        prior = [] if state is None else [e.attempt for e in state.ledger if e.job_id == job_id]
        attempt = max(prior) + 1 if prior else 0
        decision = AutonomousRecoveryV82.next_action(
            FailureSignal(workflow, conclusion, log_excerpt), attempt, max_attempts
        )
        return decision.action, attempt, decision.category

    def run(self, *, pr_number: int, expected_head_sha: str, current_head_sha: str,
            workflow: str, job_id: int, conclusion: str, log_excerpt: str = "",
            now: int, max_attempts: int = 2) -> dict:
        if expected_head_sha != current_head_sha:
            raise RuntimeError("stale_pr_head")
        if now < 0:
            raise ValueError("invalid_runtime_time")

        action, attempt, category = self._intent(
            pr_number=pr_number,
            head_sha=expected_head_sha,
            workflow=workflow,
            job_id=job_id,
            conclusion=conclusion,
            log_excerpt=log_excerpt,
            max_attempts=max_attempts,
        )
        before = self.audit.audit(
            pr_number=pr_number,
            expected_head_sha=expected_head_sha,
            current_head_sha=current_head_sha,
            workflow=workflow,
            job_id=job_id,
            action=action,
            attempt=attempt,
            now=now,
        )
        if before["status"] in self._BLOCKED:
            raise RuntimeError(f"production_recovery_blocked:{before['status']}")

        result = self.runtime.run(
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
        if result.get("action") not in {None, action}:
            raise RuntimeError("runtime_action_identity_mismatch")
        if result.get("attempt") not in {None, attempt}:
            raise RuntimeError("runtime_attempt_identity_mismatch")

        after = self.audit.audit(
            pr_number=pr_number,
            expected_head_sha=expected_head_sha,
            current_head_sha=current_head_sha,
            workflow=workflow,
            job_id=job_id,
            action=action,
            attempt=attempt,
            now=now,
        )
        if after["status"] != "CONVERGED":
            raise RuntimeError(f"production_recovery_not_converged:{after['status']}")

        return {
            "status": "PRODUCTION_RECOVERY_CONVERGED",
            "version": VERSION,
            "pr_number": pr_number,
            "head_sha": expected_head_sha,
            "workflow": workflow,
            "job_id": job_id,
            "category": category,
            "action": action,
            "attempt": attempt,
            "runtime_mode": result["runtime_mode"],
            "receipt_id": after["receipt_id"],
            "replay_key": after["replay_key"],
            "ledger_chain_head": after["ledger_chain_head"],
            "pre_audit": before["status"],
            "post_audit": after["status"],
        }
