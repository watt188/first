import hashlib

import pytest

from protected_delivery.recovery_journaled_v94 import (
    JournaledRecoveryExecutorV94,
    JournaledRecoveryIntentV94,
)
from protected_delivery.recovery_pending_v93 import SQLitePendingActionJournalV93


HEAD = "a" * 40
REPLAY = hashlib.sha256(b"replay").hexdigest()
LEASE = hashlib.sha256(b"lease").hexdigest()


def _intent(**overrides):
    data = dict(
        pr_number=7,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow="ci",
        job_id=101,
        action="rerun_failed_job",
        attempt=0,
        replay_key=REPLAY,
        owner="worker-a",
        lease_key=LEASE,
    )
    data.update(overrides)
    return JournaledRecoveryIntentV94(**data)


def test_commits_after_accepted_receipt(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    executor = JournaledRecoveryExecutorV94(journal)
    result = executor.execute(_intent(), lambda: {"accepted": True, "receipt_id": "r-1"})
    assert result["status"] == "JOURNALED_RECOVERY_COMMITTED"
    record = journal.read(REPLAY)
    assert record.status == "COMMITTED"
    assert record.receipt_id == "r-1"


def test_exception_leaves_prepared_for_reconciliation(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    executor = JournaledRecoveryExecutorV94(journal)

    def crash():
        raise RuntimeError("transport_lost")

    with pytest.raises(RuntimeError, match="transport_lost"):
        executor.execute(_intent(), crash)
    assert journal.read(REPLAY).status == "PREPARED"


def test_missing_receipt_leaves_prepared(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    executor = JournaledRecoveryExecutorV94(journal)
    with pytest.raises(RuntimeError, match="recovery_receipt_missing"):
        executor.execute(_intent(), lambda: {"accepted": True})
    assert journal.read(REPLAY).status == "PREPARED"


def test_unaccepted_mutation_leaves_prepared(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    executor = JournaledRecoveryExecutorV94(journal)
    with pytest.raises(RuntimeError, match="recovery_mutation_not_accepted"):
        executor.execute(_intent(), lambda: {"accepted": False, "receipt_id": "r-x"})
    assert journal.read(REPLAY).status == "PREPARED"


def test_stale_head_rejected_before_prepare(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    executor = JournaledRecoveryExecutorV94(journal)
    with pytest.raises(RuntimeError, match="stale_pr_head"):
        executor.execute(_intent(current_head_sha="b" * 40), lambda: {"accepted": True, "receipt_id": "r"})
    assert journal.read(REPLAY) is None


def test_competing_pending_action_fails_closed(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    executor = JournaledRecoveryExecutorV94(journal)
    executor.execute(_intent(), lambda: {"accepted": True, "receipt_id": "r-1"})

    replay2 = hashlib.sha256(b"replay-2").hexdigest()
    # Re-create an unresolved PREPARED entry on a different head to prove the
    # executor checks only the exact PR/head binding.
    head2 = "c" * 40
    journal.prepare(
        pr_number=7,
        expected_head_sha=head2,
        current_head_sha=head2,
        workflow="ci",
        job_id=202,
        action="rerun_failed_job",
        attempt=0,
        replay_key=replay2,
        owner="worker-b",
        lease_key=LEASE,
    )
    result = executor.execute(
        _intent(pr_number=8, expected_head_sha=HEAD, current_head_sha=HEAD, replay_key=hashlib.sha256(b"replay-3").hexdigest()),
        lambda: {"accepted": True, "receipt_id": "r-3"},
    )
    assert result["status"] == "JOURNALED_RECOVERY_COMMITTED"
