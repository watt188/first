import dataclasses
import re
from typing import Iterable

from protected_delivery.recovery_evidence_v85 import RecoveryEvidenceEventV85, RecoveryEvidenceLedgerV85
from protected_delivery.recovery_orchestrator_v84 import LiveRecoveryOrchestratorV84, RecoverySnapshotV84, WorkflowFailureV84
from protected_delivery.recovery_reconcile_v86 import RecoveryStateReconcilerV86

VERSION = "8.7"


@dataclasses.dataclass(frozen=True)
class RecoverySessionV87:
    pr_number: int
    expected_head_sha: str
    current_head_sha: str
    failures: tuple[WorkflowFailureV84, ...]
    ledger: tuple[RecoveryEvidenceEventV85, ...] = ()
    attempts_by_job: dict[int, int] = dataclasses.field(default_factory=dict)
    max_attempts: int = 2

    def validate(self) -> None:
        if self.pr_number < 1:
            raise ValueError("invalid_pr_number")
        for value in (self.expected_head_sha, self.current_head_sha):
            if not re.fullmatch(r"[0-9a-f]{40}", value):
                raise ValueError("invalid_head_sha")
        if self.expected_head_sha != self.current_head_sha:
            raise RuntimeError("stale_pr_head")
        if not self.failures:
            raise ValueError("no_failures")


class RecoverySessionCoordinatorV87:
    """Coordinate reconcile -> one recovery action -> append evidence.

    The coordinator is exact-head bound and fail-closed. It performs a state
    reconciliation immediately before recovery, delegates the single mutation
    to V8.4, and records only an accepted receipt in the V8.5 evidence chain.
    It never merges, bypasses policy, or writes to main.
    """

    def __init__(self, gateway):
        self.orchestrator = LiveRecoveryOrchestratorV84(gateway)

    def run(self, session: RecoverySessionV87) -> dict:
        session.validate()

        reconcile = RecoveryStateReconcilerV86.reconcile(
            session.ledger,
            pr_number=session.pr_number,
            expected_head_sha=session.expected_head_sha,
            current_head_sha=session.current_head_sha,
            failures=session.failures,
        )
        if reconcile.get("status") != "RECONCILED":
            raise RuntimeError("reconciliation_not_verified")

        snapshot = RecoverySnapshotV84(
            pr_number=session.pr_number,
            expected_head_sha=session.expected_head_sha,
            current_head_sha=session.current_head_sha,
            failures=session.failures,
            attempts_by_job=session.attempts_by_job,
            max_attempts=session.max_attempts,
        )
        receipt = self.orchestrator.recover(snapshot)
        if receipt.get("status") != "RECOVERY_ACTION_ACCEPTED":
            raise RuntimeError("recovery_not_accepted")

        if receipt.get("failure_count") != 1:
            return {
                **receipt,
                "version": VERSION,
                "ledger": session.ledger,
                "ledger_appended": False,
            }

        failure = session.failures[0]
        attempt = session.attempts_by_job.get(failure.job_id, 0)
        ledger = RecoveryEvidenceLedgerV85.append(
            session.ledger,
            pr_number=session.pr_number,
            expected_head_sha=session.expected_head_sha,
            current_head_sha=session.current_head_sha,
            workflow=failure.workflow,
            job_id=failure.job_id,
            category=str(receipt["category"]),
            action=str(receipt["action"]),
            attempt=attempt,
            accepted=True,
        )
        verified = RecoveryEvidenceLedgerV85.verify(
            ledger,
            expected_head_sha=session.expected_head_sha,
            pr_number=session.pr_number,
        )
        return {
            **receipt,
            "version": VERSION,
            "ledger": ledger,
            "ledger_appended": True,
            "ledger_chain_head": verified["chain_head"],
        }
