import dataclasses
import hashlib
import json
import re
from typing import Iterable

VERSION = "8.5"
_ZERO = "0" * 64


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256(payload: dict) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


@dataclasses.dataclass(frozen=True)
class RecoveryEvidenceEventV85:
    sequence: int
    pr_number: int
    head_sha: str
    workflow: str
    job_id: int
    category: str
    action: str
    attempt: int
    accepted: bool
    previous_hash: str
    event_hash: str = ""

    def payload(self) -> dict:
        return {
            "sequence": self.sequence,
            "pr_number": self.pr_number,
            "head_sha": self.head_sha,
            "workflow": self.workflow,
            "job_id": self.job_id,
            "category": self.category,
            "action": self.action,
            "attempt": self.attempt,
            "accepted": self.accepted,
            "previous_hash": self.previous_hash,
        }

    def seal(self) -> "RecoveryEvidenceEventV85":
        return dataclasses.replace(self, event_hash=_sha256(self.payload()))


class RecoveryEvidenceLedgerV85:
    """Append-only exact-head recovery evidence chain.

    The ledger is deterministic and fail-closed. It does not execute recovery
    actions; it records their accepted receipts after the V8.4 orchestrator has
    acted. Each entry binds PR, exact head, failed workflow/job, decision,
    monotonic attempt number and the previous event hash.
    """

    @staticmethod
    def _validate_head(head_sha: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
            raise ValueError("invalid_head_sha")

    @classmethod
    def append(
        cls,
        events: Iterable[RecoveryEvidenceEventV85],
        *,
        pr_number: int,
        expected_head_sha: str,
        current_head_sha: str,
        workflow: str,
        job_id: int,
        category: str,
        action: str,
        attempt: int,
        accepted: bool,
    ) -> tuple[RecoveryEvidenceEventV85, ...]:
        existing = tuple(events)
        if pr_number < 1:
            raise ValueError("invalid_pr_number")
        cls._validate_head(expected_head_sha)
        cls._validate_head(current_head_sha)
        if expected_head_sha != current_head_sha:
            raise RuntimeError("stale_pr_head")
        if not workflow.strip() or job_id < 1:
            raise ValueError("invalid_failure_identity")
        if not category.strip() or not action.strip():
            raise ValueError("invalid_recovery_decision")
        if attempt < 0:
            raise ValueError("invalid_attempt")
        if accepted is not True:
            raise RuntimeError("unaccepted_recovery_receipt")

        cls.verify(existing, expected_head_sha=expected_head_sha, pr_number=pr_number)
        same_job_attempts = [event.attempt for event in existing if event.job_id == job_id]
        if same_job_attempts and attempt <= max(same_job_attempts):
            raise RuntimeError("non_monotonic_attempt")

        previous_hash = existing[-1].event_hash if existing else _ZERO
        event = RecoveryEvidenceEventV85(
            sequence=len(existing) + 1,
            pr_number=pr_number,
            head_sha=expected_head_sha,
            workflow=workflow.strip(),
            job_id=job_id,
            category=category.strip(),
            action=action.strip(),
            attempt=attempt,
            accepted=True,
            previous_hash=previous_hash,
        ).seal()
        return existing + (event,)

    @classmethod
    def verify(
        cls,
        events: Iterable[RecoveryEvidenceEventV85],
        *,
        expected_head_sha: str,
        pr_number: int,
    ) -> dict:
        cls._validate_head(expected_head_sha)
        if pr_number < 1:
            raise ValueError("invalid_pr_number")
        previous_hash = _ZERO
        count = 0
        attempts: dict[int, int] = {}
        for count, event in enumerate(tuple(events), start=1):
            if event.sequence != count:
                raise RuntimeError("ledger_sequence_mismatch")
            if event.pr_number != pr_number or event.head_sha != expected_head_sha:
                raise RuntimeError("ledger_binding_mismatch")
            if event.previous_hash != previous_hash:
                raise RuntimeError("ledger_chain_mismatch")
            if event.accepted is not True:
                raise RuntimeError("ledger_contains_unaccepted_receipt")
            if event.event_hash != _sha256(event.payload()):
                raise RuntimeError("ledger_event_hash_mismatch")
            prior_attempt = attempts.get(event.job_id)
            if prior_attempt is not None and event.attempt <= prior_attempt:
                raise RuntimeError("ledger_attempt_not_monotonic")
            attempts[event.job_id] = event.attempt
            previous_hash = event.event_hash
        return {
            "status": "VERIFIED",
            "version": VERSION,
            "pr_number": pr_number,
            "head_sha": expected_head_sha,
            "event_count": count,
            "chain_head": previous_hash,
        }
