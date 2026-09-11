import dataclasses
from typing import Callable

from protected_delivery.recovery_pending_v93 import SQLitePendingActionJournalV93

VERSION = "9.4"


@dataclasses.dataclass(frozen=True)
class JournaledRecoveryIntentV94:
    pr_number: int
    expected_head_sha: str
    current_head_sha: str
    workflow: str
    job_id: int
    action: str
    attempt: int
    replay_key: str
    owner: str
    lease_key: str


class JournaledRecoveryExecutorV94:
    """Persist PREPARED before one external recovery mutation.

    The executor closes the V9.2 write-ahead gap by forcing every external
    mutation through the V9.3 durable journal. A successful mutation must
    return accepted=True plus a stable receipt_id. Ambiguous exceptions leave
    the journal PREPARED so a later worker fails closed and reconciles instead
    of replaying blindly.
    """

    def __init__(self, journal: SQLitePendingActionJournalV93):
        self.journal = journal

    def execute(self, intent: JournaledRecoveryIntentV94, mutation: Callable[[], dict]) -> dict:
        if intent.expected_head_sha != intent.current_head_sha:
            raise RuntimeError("stale_pr_head")

        existing = self.journal.prepared_for_head(
            pr_number=intent.pr_number,
            head_sha=intent.expected_head_sha,
        )
        if existing is not None and existing.replay_key != intent.replay_key:
            raise RuntimeError("unresolved_pending_action")

        prepared = self.journal.prepare(
            pr_number=intent.pr_number,
            expected_head_sha=intent.expected_head_sha,
            current_head_sha=intent.current_head_sha,
            workflow=intent.workflow,
            job_id=intent.job_id,
            action=intent.action,
            attempt=intent.attempt,
            replay_key=intent.replay_key,
            owner=intent.owner,
            lease_key=intent.lease_key,
        )

        receipt = mutation()
        if receipt.get("accepted") is not True:
            raise RuntimeError("recovery_mutation_not_accepted")
        receipt_id = str(receipt.get("receipt_id", "")).strip()
        if not receipt_id:
            raise RuntimeError("recovery_receipt_missing")

        committed = self.journal.commit(prepared, receipt_id=receipt_id)
        return {
            "status": "JOURNALED_RECOVERY_COMMITTED",
            "version": VERSION,
            "pr_number": intent.pr_number,
            "head_sha": intent.expected_head_sha,
            "replay_key": intent.replay_key,
            "action": intent.action,
            "receipt_id": receipt_id,
            "journal_hash": committed.action_hash,
        }
