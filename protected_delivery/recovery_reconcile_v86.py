import dataclasses
import re
from typing import Iterable

from protected_delivery.recovery_evidence_v85 import (
    RecoveryEvidenceEventV85,
    RecoveryEvidenceLedgerV85,
)

VERSION = "8.6"
_FAILURE_CONCLUSIONS = {"failure", "cancelled", "timed_out", "action_required"}


@dataclasses.dataclass(frozen=True)
class LiveFailedJobV86:
    workflow: str
    job_id: int
    conclusion: str

    def validate(self) -> None:
        if not self.workflow.strip():
            raise ValueError("empty_workflow")
        if self.job_id < 1:
            raise ValueError("invalid_job_id")
        if self.conclusion not in _FAILURE_CONCLUSIONS:
            raise ValueError("not_a_failure")


@dataclasses.dataclass(frozen=True)
class RecoveryLiveStateV86:
    pr_number: int
    expected_head_sha: str
    current_head_sha: str
    failed_jobs: tuple[LiveFailedJobV86, ...]

    def validate(self) -> None:
        if self.pr_number < 1:
            raise ValueError("invalid_pr_number")
        for value in (self.expected_head_sha, self.current_head_sha):
            if not re.fullmatch(r"[0-9a-f]{40}", value):
                raise ValueError("invalid_head_sha")
        if self.expected_head_sha != self.current_head_sha:
            raise RuntimeError("stale_pr_head")
        seen: set[int] = set()
        for job in self.failed_jobs:
            job.validate()
            if job.job_id in seen:
                raise ValueError("duplicate_live_failed_job")
            seen.add(job.job_id)


class RecoveryStateReconcilerV86:
    """Fail closed unless the recovery ledger still matches live GitHub state.

    This gate closes the time-of-check/time-of-use gap between an accepted
    recovery action and the next recovery decision. It never executes retries,
    rework, merges, policy changes, or writes to main.
    """

    @classmethod
    def reconcile(
        cls,
        events: Iterable[RecoveryEvidenceEventV85],
        live: RecoveryLiveStateV86,
    ) -> dict:
        live.validate()
        chain = tuple(events)
        ledger = RecoveryEvidenceLedgerV85.verify(
            chain,
            expected_head_sha=live.expected_head_sha,
            pr_number=live.pr_number,
        )

        live_by_job = {job.job_id: job for job in live.failed_jobs}
        latest_by_job: dict[int, RecoveryEvidenceEventV85] = {}
        for event in chain:
            latest_by_job[event.job_id] = event

        for job_id, event in latest_by_job.items():
            live_job = live_by_job.get(job_id)
            if live_job is None:
                raise RuntimeError("ledger_job_no_longer_failed")
            if live_job.workflow != event.workflow:
                raise RuntimeError("workflow_identity_mismatch")

        untracked = sorted(set(live_by_job) - set(latest_by_job))
        if untracked and chain:
            raise RuntimeError("new_untracked_failure")

        retry_attempts = {
            job_id: event.attempt
            for job_id, event in latest_by_job.items()
            if event.action == "rerun_failed_job"
        }
        return {
            "status": "RECONCILED",
            "version": VERSION,
            "pr_number": live.pr_number,
            "head_sha": live.expected_head_sha,
            "ledger_event_count": ledger["event_count"],
            "ledger_chain_head": ledger["chain_head"],
            "live_failure_count": len(live.failed_jobs),
            "tracked_failure_count": len(latest_by_job),
            "retry_attempts": retry_attempts,
        }
