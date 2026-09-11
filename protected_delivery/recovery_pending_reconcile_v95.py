import dataclasses
from typing import Protocol

from protected_delivery.recovery_pending_v93 import PendingRecoveryActionV93, SQLitePendingActionJournalV93

VERSION = "9.5"


class RecoveryReceiptResolverV95(Protocol):
    def resolve(self, *, replay_key: str, expected_head_sha: str) -> dict: ...


@dataclasses.dataclass(frozen=True)
class PendingReconciliationResultV95:
    status: str
    replay_key: str
    outcome: str
    receipt_id: str = ""


class PendingActionReconcilerV95:
    """Resolve a durable PREPARED recovery action without blind replay.

    The resolver must query downstream action state using the stable replay key.
    Confirmed acceptance commits the journal; confirmed non-execution aborts it.
    Unknown, conflicting, stale-head, or malformed evidence fails closed and
    leaves PREPARED intact for later reconciliation/human review.
    """

    def __init__(self, journal: SQLitePendingActionJournalV93, resolver: RecoveryReceiptResolverV95):
        self.journal = journal
        self.resolver = resolver

    def reconcile(
        self,
        *,
        pr_number: int,
        expected_head_sha: str,
        current_head_sha: str,
    ) -> PendingReconciliationResultV95:
        if expected_head_sha != current_head_sha:
            raise RuntimeError("stale_pr_head")
        pending = self.journal.prepared_for_head(pr_number=pr_number, head_sha=expected_head_sha)
        if pending is None:
            return PendingReconciliationResultV95("NO_PENDING_ACTION", "", "none")
        SQLitePendingActionJournalV93.verify(pending)
        if pending.status != "PREPARED":
            raise RuntimeError("pending_action_not_prepared")

        evidence = self.resolver.resolve(
            replay_key=pending.replay_key,
            expected_head_sha=expected_head_sha,
        )
        if not isinstance(evidence, dict):
            raise RuntimeError("invalid_reconciliation_evidence")
        if evidence.get("replay_key") != pending.replay_key:
            raise RuntimeError("reconciliation_replay_key_mismatch")
        if evidence.get("head_sha") != expected_head_sha:
            raise RuntimeError("reconciliation_head_mismatch")

        outcome = str(evidence.get("outcome", "unknown"))
        if outcome == "accepted":
            receipt_id = str(evidence.get("receipt_id", "")).strip()
            if not receipt_id:
                raise RuntimeError("accepted_without_receipt")
            committed = self.journal.commit(pending, receipt_id=receipt_id)
            return PendingReconciliationResultV95("RECONCILED", committed.replay_key, "committed", receipt_id)
        if outcome == "not_executed":
            reason = str(evidence.get("reason", "confirmed_not_executed")).strip()
            if not reason:
                reason = "confirmed_not_executed"
            aborted = self.journal.abort(pending, reason=reason)
            return PendingReconciliationResultV95("RECONCILED", aborted.replay_key, "aborted", reason)
        if outcome in {"unknown", "pending", "ambiguous"}:
            raise RuntimeError("recovery_outcome_unresolved")
        raise RuntimeError("unsupported_reconciliation_outcome")
