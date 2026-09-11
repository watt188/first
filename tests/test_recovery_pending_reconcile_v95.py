import hashlib

import pytest

from protected_delivery.recovery_pending_reconcile_v95 import PendingActionReconcilerV95
from protected_delivery.recovery_pending_v93 import SQLitePendingActionJournalV93

HEAD = "a" * 40


def replay():
    return hashlib.sha256(b"v95-replay").hexdigest()


def lease():
    return hashlib.sha256(b"v95-lease").hexdigest()


def prepared(journal):
    return journal.prepare(
        pr_number=51,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow="ci",
        job_id=501,
        action="rerun_failed_job",
        attempt=0,
        replay_key=replay(),
        owner="worker-a",
        lease_key=lease(),
    )


class Resolver:
    def __init__(self, evidence):
        self.evidence = evidence
        self.calls = 0

    def resolve(self, **kwargs):
        self.calls += 1
        return dict(self.evidence)


def test_confirmed_acceptance_commits(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    action = prepared(journal)
    resolver = Resolver({"replay_key": action.replay_key, "head_sha": HEAD, "outcome": "accepted", "receipt_id": "run-123"})
    result = PendingActionReconcilerV95(journal, resolver).reconcile(pr_number=51, expected_head_sha=HEAD, current_head_sha=HEAD)
    assert result.outcome == "committed"
    assert journal.read(action.replay_key).status == "COMMITTED"


def test_confirmed_not_executed_aborts(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    action = prepared(journal)
    resolver = Resolver({"replay_key": action.replay_key, "head_sha": HEAD, "outcome": "not_executed", "reason": "downstream_absent"})
    result = PendingActionReconcilerV95(journal, resolver).reconcile(pr_number=51, expected_head_sha=HEAD, current_head_sha=HEAD)
    assert result.outcome == "aborted"
    assert journal.read(action.replay_key).status == "ABORTED"


def test_unknown_fails_closed_and_preserves_prepared(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    action = prepared(journal)
    resolver = Resolver({"replay_key": action.replay_key, "head_sha": HEAD, "outcome": "unknown"})
    with pytest.raises(RuntimeError, match="recovery_outcome_unresolved"):
        PendingActionReconcilerV95(journal, resolver).reconcile(pr_number=51, expected_head_sha=HEAD, current_head_sha=HEAD)
    assert journal.read(action.replay_key).status == "PREPARED"


def test_wrong_replay_evidence_fails_closed(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    action = prepared(journal)
    resolver = Resolver({"replay_key": "0" * 64, "head_sha": HEAD, "outcome": "accepted", "receipt_id": "x"})
    with pytest.raises(RuntimeError, match="reconciliation_replay_key_mismatch"):
        PendingActionReconcilerV95(journal, resolver).reconcile(pr_number=51, expected_head_sha=HEAD, current_head_sha=HEAD)
    assert journal.read(action.replay_key).status == "PREPARED"


def test_stale_head_never_queries_resolver(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    prepared(journal)
    resolver = Resolver({})
    with pytest.raises(RuntimeError, match="stale_pr_head"):
        PendingActionReconcilerV95(journal, resolver).reconcile(pr_number=51, expected_head_sha=HEAD, current_head_sha="b" * 40)
    assert resolver.calls == 0


def test_no_pending_is_noop(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    resolver = Resolver({})
    result = PendingActionReconcilerV95(journal, resolver).reconcile(pr_number=51, expected_head_sha=HEAD, current_head_sha=HEAD)
    assert result.status == "NO_PENDING_ACTION"
    assert resolver.calls == 0
