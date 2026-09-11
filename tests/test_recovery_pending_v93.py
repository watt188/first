import dataclasses

import pytest

from protected_delivery.recovery_pending_v93 import SQLitePendingActionJournalV93

HEAD = "a" * 40
REPLAY = "b" * 64
LEASE = "c" * 64


def prepare(journal, **overrides):
    args = dict(
        pr_number=70,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow="v71-autonomous-delivery",
        job_id=101,
        action="rerun_failed_job",
        attempt=0,
        replay_key=REPLAY,
        owner="worker-a",
        lease_key=LEASE,
    )
    args.update(overrides)
    return journal.prepare(**args)


def test_prepare_survives_reopen(tmp_path):
    path = tmp_path / "pending.db"
    journal = SQLitePendingActionJournalV93(path)
    pending = prepare(journal)
    reopened = SQLitePendingActionJournalV93(path)
    assert reopened.read(REPLAY) == pending
    assert reopened.prepared_for_head(pr_number=70, head_sha=HEAD) == pending


def test_identical_prepare_is_idempotent(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    first = prepare(journal)
    second = prepare(journal)
    assert second == first


def test_same_replay_key_with_different_payload_is_rejected(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    prepare(journal)
    with pytest.raises(RuntimeError, match="pending_replay_key_conflict"):
        prepare(journal, job_id=102)


def test_second_prepared_action_on_same_head_is_rejected(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    prepare(journal)
    with pytest.raises(RuntimeError, match="pending_action_already_exists"):
        prepare(journal, replay_key="d" * 64, job_id=102)


def test_commit_clears_active_prepared_slot(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    pending = prepare(journal)
    committed = journal.commit(pending, receipt_id="receipt-1")
    assert committed.status == "COMMITTED"
    assert committed.receipt_id == "receipt-1"
    assert journal.prepared_for_head(pr_number=70, head_sha=HEAD) is None
    replacement = prepare(journal, replay_key="d" * 64, job_id=102)
    assert replacement.status == "PREPARED"


def test_restart_detects_unresolved_prepared_action(tmp_path):
    path = tmp_path / "pending.db"
    journal = SQLitePendingActionJournalV93(path)
    pending = prepare(journal)
    del journal
    restarted = SQLitePendingActionJournalV93(path)
    unresolved = restarted.prepared_for_head(pr_number=70, head_sha=HEAD)
    assert unresolved == pending
    assert unresolved.status == "PREPARED"


def test_tampered_hash_fails_closed(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    pending = prepare(journal)
    tampered = dataclasses.replace(pending, action_hash="0" * 64)
    with pytest.raises(RuntimeError, match="pending_action_hash_mismatch"):
        journal.verify(tampered)


def test_stale_head_rejected_before_prepare(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    with pytest.raises(RuntimeError, match="stale_pr_head"):
        prepare(journal, current_head_sha="e" * 40)
    assert journal.read(REPLAY) is None


def test_abort_is_durable_and_not_active(tmp_path):
    path = tmp_path / "pending.db"
    journal = SQLitePendingActionJournalV93(path)
    pending = prepare(journal)
    aborted = journal.abort(pending, reason="manual_reconciliation_required")
    assert aborted.status == "ABORTED"
    reopened = SQLitePendingActionJournalV93(path)
    assert reopened.read(REPLAY) == aborted
    assert reopened.prepared_for_head(pr_number=70, head_sha=HEAD) is None
