import hashlib

import pytest

from protected_delivery.recovery_idempotent_v95 import (
    IdempotentRecoveryExecutorV95,
    IdempotentRecoveryIntentV95,
)
from protected_delivery.recovery_pending_v93 import SQLitePendingActionJournalV93


HEAD = "a" * 40
REPLAY = hashlib.sha256(b"replay").hexdigest()
LEASE = hashlib.sha256(b"lease").hexdigest()


class FakeIdempotentGateway:
    def __init__(self):
        self.calls = 0
        self.receipts = {}

    def execute_recovery(self, *, pr_number, expected_head_sha, action, job_id, idempotency_key):
        self.calls += 1
        receipt = self.receipts.setdefault(
            idempotency_key,
            {"found": True, "accepted": True, "receipt_id": f"receipt-{idempotency_key[:12]}"},
        )
        return dict(receipt)

    def lookup_recovery(self, *, idempotency_key):
        return dict(self.receipts.get(idempotency_key, {"found": False}))


def intent(**changes):
    values = dict(
        pr_number=7,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow="ci",
        job_id=91,
        action="rerun_failed_job",
        attempt=0,
        replay_key=REPLAY,
        owner="worker-a",
        lease_key=LEASE,
    )
    values.update(changes)
    return IdempotentRecoveryIntentV95(**values)


def test_new_recovery_executes_once_and_commits(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    gateway = FakeIdempotentGateway()
    executor = IdempotentRecoveryExecutorV95(journal=journal, gateway=gateway)

    result = executor.execute(intent())

    assert result["status"] == "IDEMPOTENT_RECOVERY_COMMITTED"
    assert result["reconciled"] is False
    assert gateway.calls == 1
    assert journal.read(REPLAY).status == "COMMITTED"


def test_committed_retry_returns_without_second_mutation(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    gateway = FakeIdempotentGateway()
    executor = IdempotentRecoveryExecutorV95(journal=journal, gateway=gateway)

    first = executor.execute(intent())
    second = executor.execute(intent())

    assert first["receipt_id"] == second["receipt_id"]
    assert second["reconciled"] is True
    assert gateway.calls == 1


def test_crash_window_reconciles_prepared_from_gateway_receipt(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    gateway = FakeIdempotentGateway()
    pending = journal.prepare(
        pr_number=7,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow="ci",
        job_id=91,
        action="rerun_failed_job",
        attempt=0,
        replay_key=REPLAY,
        owner="worker-a",
        lease_key=LEASE,
    )
    assert pending.status == "PREPARED"
    gateway.execute_recovery(
        pr_number=7,
        expected_head_sha=HEAD,
        action="rerun_failed_job",
        job_id=91,
        idempotency_key=REPLAY,
    )
    executor = IdempotentRecoveryExecutorV95(journal=journal, gateway=gateway)

    result = executor.execute(intent())

    assert result["reconciled"] is True
    assert result["receipt_id"].startswith("receipt-")
    assert gateway.calls == 1
    assert journal.read(REPLAY).status == "COMMITTED"


def test_unresolved_prepared_fails_closed_without_replay(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    gateway = FakeIdempotentGateway()
    journal.prepare(
        pr_number=7,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow="ci",
        job_id=91,
        action="rerun_failed_job",
        attempt=0,
        replay_key=REPLAY,
        owner="worker-a",
        lease_key=LEASE,
    )
    executor = IdempotentRecoveryExecutorV95(journal=journal, gateway=gateway)

    with pytest.raises(RuntimeError, match="pending_recovery_unresolved"):
        executor.execute(intent())

    assert gateway.calls == 0
    assert journal.read(REPLAY).status == "PREPARED"


def test_stale_head_fails_before_gateway(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    gateway = FakeIdempotentGateway()
    executor = IdempotentRecoveryExecutorV95(journal=journal, gateway=gateway)

    with pytest.raises(RuntimeError, match="stale_pr_head"):
        executor.execute(intent(current_head_sha="b" * 40))

    assert gateway.calls == 0


def test_same_replay_key_cannot_change_action_identity(tmp_path):
    journal = SQLitePendingActionJournalV93(tmp_path / "pending.db")
    gateway = FakeIdempotentGateway()
    executor = IdempotentRecoveryExecutorV95(journal=journal, gateway=gateway)
    executor.execute(intent())

    with pytest.raises(RuntimeError, match="pending_replay_identity_mismatch"):
        executor.execute(intent(action="autonomous_rework"))

    assert gateway.calls == 1
