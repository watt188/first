import dataclasses
import hashlib
import json
import re

from protected_delivery.recovery_evidence_v85 import RecoveryEvidenceEventV85, RecoveryEvidenceLedgerV85

VERSION = "8.8"


def _key_payload(*, pr_number: int, head_sha: str, workflow: str, job_id: int, action: str, attempt: int) -> bytes:
    payload = {
        "pr_number": pr_number,
        "head_sha": head_sha,
        "workflow": workflow,
        "job_id": job_id,
        "action": action,
        "attempt": attempt,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def replay_key(**kwargs) -> str:
    return hashlib.sha256(_key_payload(**kwargs)).hexdigest()


@dataclasses.dataclass(frozen=True)
class RecoveryReplayIntentV88:
    pr_number: int
    expected_head_sha: str
    current_head_sha: str
    workflow: str
    job_id: int
    action: str
    attempt: int

    def validate(self) -> None:
        if self.pr_number < 1:
            raise ValueError("invalid_pr_number")
        for value in (self.expected_head_sha, self.current_head_sha):
            if not re.fullmatch(r"[0-9a-f]{40}", value):
                raise ValueError("invalid_head_sha")
        if self.expected_head_sha != self.current_head_sha:
            raise RuntimeError("stale_pr_head")
        if not self.workflow.strip() or self.job_id < 1 or not self.action.strip():
            raise ValueError("invalid_recovery_identity")
        if self.attempt < 0:
            raise ValueError("invalid_attempt")


class RecoveryReplayGuardV88:
    """Fail closed on duplicate or stale recovery action replay.

    The guard verifies the V8.5 evidence chain first, binds decisions to the
    exact PR head, and derives an idempotency key from PR/head/workflow/job/
    action/attempt. If the same logical action already exists in accepted
    evidence, the next mutation is rejected rather than executed twice.
    """

    @classmethod
    def authorize(
        cls,
        events: tuple[RecoveryEvidenceEventV85, ...],
        intent: RecoveryReplayIntentV88,
    ) -> dict:
        intent.validate()
        verified = RecoveryEvidenceLedgerV85.verify(
            events,
            expected_head_sha=intent.expected_head_sha,
            pr_number=intent.pr_number,
        )
        key = replay_key(
            pr_number=intent.pr_number,
            head_sha=intent.expected_head_sha,
            workflow=intent.workflow.strip(),
            job_id=intent.job_id,
            action=intent.action.strip(),
            attempt=intent.attempt,
        )
        for event in events:
            existing = replay_key(
                pr_number=event.pr_number,
                head_sha=event.head_sha,
                workflow=event.workflow,
                job_id=event.job_id,
                action=event.action,
                attempt=event.attempt,
            )
            if existing == key:
                raise RuntimeError("duplicate_recovery_replay")

        same_job = [event for event in events if event.job_id == intent.job_id]
        if same_job and intent.attempt <= max(event.attempt for event in same_job):
            raise RuntimeError("stale_recovery_attempt")

        return {
            "status": "AUTHORIZED",
            "version": VERSION,
            "pr_number": intent.pr_number,
            "head_sha": intent.expected_head_sha,
            "replay_key": key,
            "ledger_chain_head": verified["chain_head"],
            "ledger_event_count": verified["event_count"],
        }
