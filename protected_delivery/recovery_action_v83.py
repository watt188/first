import dataclasses
import re
from typing import Protocol

from protected_delivery.recovery_v82 import AutonomousRecoveryV82, FailureSignal, RecoveryDecision

VERSION = "8.3"


class RecoveryGateway(Protocol):
    def rerun_failed_job(self, *, job_id: int, expected_head_sha: str) -> dict: ...
    def request_rework(self, *, pr_number: int, expected_head_sha: str, reason: str) -> dict: ...
    def escalate_human(self, *, pr_number: int, expected_head_sha: str, reason: str) -> dict: ...


@dataclasses.dataclass(frozen=True)
class RecoveryContextV83:
    pr_number: int
    expected_head_sha: str
    current_head_sha: str
    failed_job_id: int
    attempts: int = 0
    max_attempts: int = 2

    def validate(self) -> None:
        if self.pr_number < 1:
            raise ValueError("invalid_pr_number")
        for value in (self.expected_head_sha, self.current_head_sha):
            if not re.fullmatch(r"[0-9a-f]{40}", value):
                raise ValueError("invalid_head_sha")
        if self.expected_head_sha != self.current_head_sha:
            raise RuntimeError("stale_pr_head")
        if self.failed_job_id < 1:
            raise ValueError("invalid_failed_job_id")
        if self.attempts < 0 or self.max_attempts < 1:
            raise ValueError("invalid_attempt_budget")


class RecoveryActionBridgeV83:
    """Execute only recovery actions authorized by the V8.2 classifier.

    The bridge is exact-head bound and fail-closed. It never merges, bypasses
    server policy, or mutates main directly.
    """

    def __init__(self, gateway: RecoveryGateway):
        self.gateway = gateway

    def execute(self, signal: FailureSignal, context: RecoveryContextV83) -> dict:
        context.validate()
        decision: RecoveryDecision = AutonomousRecoveryV82.next_action(
            signal, context.attempts, context.max_attempts
        )

        if decision.action == "rerun_failed_job":
            receipt = self.gateway.rerun_failed_job(
                job_id=context.failed_job_id,
                expected_head_sha=context.expected_head_sha,
            )
            if receipt.get("accepted") is not True:
                raise RuntimeError("rerun_not_accepted")
        elif decision.action == "autonomous_rework":
            receipt = self.gateway.request_rework(
                pr_number=context.pr_number,
                expected_head_sha=context.expected_head_sha,
                reason=decision.category,
            )
            if receipt.get("accepted") is not True:
                raise RuntimeError("rework_not_accepted")
        elif decision.action == "escalate_human":
            receipt = self.gateway.escalate_human(
                pr_number=context.pr_number,
                expected_head_sha=context.expected_head_sha,
                reason=decision.category,
            )
            if receipt.get("accepted") is not True:
                raise RuntimeError("escalation_not_accepted")
        else:
            raise RuntimeError("unsupported_recovery_action")

        return {
            "status": "RECOVERY_ACTION_ACCEPTED",
            "version": VERSION,
            "pr_number": context.pr_number,
            "head_sha": context.expected_head_sha,
            "category": decision.category,
            "action": decision.action,
            "retryable": decision.retryable,
            "requires_human": decision.requires_human,
        }
