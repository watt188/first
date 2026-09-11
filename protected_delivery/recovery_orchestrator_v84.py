import dataclasses
import re

from protected_delivery.recovery_v82 import AutonomousRecoveryV82, FailureSignal
from protected_delivery.recovery_action_v83 import RecoveryActionBridgeV83, RecoveryContextV83

VERSION = "8.4"
_FAILURE_CONCLUSIONS = {"failure", "cancelled", "timed_out", "action_required"}


@dataclasses.dataclass(frozen=True)
class WorkflowFailureV84:
    workflow: str
    conclusion: str
    job_id: int
    log_excerpt: str = ""

    def validate(self) -> None:
        if not self.workflow.strip():
            raise ValueError("empty_workflow")
        if self.conclusion not in _FAILURE_CONCLUSIONS:
            raise ValueError("not_a_failure")
        if self.job_id < 1:
            raise ValueError("invalid_job_id")


@dataclasses.dataclass(frozen=True)
class RecoverySnapshotV84:
    pr_number: int
    expected_head_sha: str
    current_head_sha: str
    failures: tuple[WorkflowFailureV84, ...]
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
        if self.max_attempts < 1:
            raise ValueError("invalid_attempt_budget")
        seen: set[int] = set()
        for failure in self.failures:
            failure.validate()
            if failure.job_id in seen:
                raise ValueError("duplicate_failed_job")
            seen.add(failure.job_id)
            if self.attempts_by_job.get(failure.job_id, 0) < 0:
                raise ValueError("invalid_attempt_count")


class LiveRecoveryOrchestratorV84:
    """Turn an exact-head failure snapshot into one bounded recovery action."""

    def __init__(self, gateway):
        self.bridge = RecoveryActionBridgeV83(gateway)

    def recover(self, snapshot: RecoverySnapshotV84) -> dict:
        snapshot.validate()
        assessed = []
        for failure in snapshot.failures:
            attempts = snapshot.attempts_by_job.get(failure.job_id, 0)
            signal = FailureSignal(failure.workflow, failure.conclusion, failure.log_excerpt)
            decision = AutonomousRecoveryV82.next_action(signal, attempts, snapshot.max_attempts)
            assessed.append((failure, attempts, decision))

        actions = {decision.action for _, _, decision in assessed}
        if len(assessed) > 1 and (len(actions) != 1 or "rerun_failed_job" in actions):
            receipt = self.bridge.gateway.escalate_human(
                pr_number=snapshot.pr_number,
                expected_head_sha=snapshot.expected_head_sha,
                reason="multi_failure_ambiguous",
            )
            if receipt.get("accepted") is not True:
                raise RuntimeError("escalation_not_accepted")
            return {
                "status": "RECOVERY_ACTION_ACCEPTED",
                "version": VERSION,
                "pr_number": snapshot.pr_number,
                "head_sha": snapshot.expected_head_sha,
                "category": "multi_failure_ambiguous",
                "action": "escalate_human",
                "failure_count": len(assessed),
                "requires_human": True,
            }

        failure, attempts, _ = assessed[0]
        result = self.bridge.execute(
            FailureSignal(failure.workflow, failure.conclusion, failure.log_excerpt),
            RecoveryContextV83(
                pr_number=snapshot.pr_number,
                expected_head_sha=snapshot.expected_head_sha,
                current_head_sha=snapshot.current_head_sha,
                failed_job_id=failure.job_id,
                attempts=attempts,
                max_attempts=snapshot.max_attempts,
            ),
        )
        return {**result, "version": VERSION, "failure_count": 1}
