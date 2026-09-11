from pathlib import Path

import pytest

from protected_delivery.recovery_pending_v93 import SQLitePendingActionJournalV93
from protected_delivery.recovery_state_sqlite_v91 import SQLiteRecoveryStateStoreV91
from protected_delivery.recovery_transaction_idempotent_v96 import IdempotentRecoveryTransactionV96

HEAD = "a" * 40


class Gateway:
    def __init__(self):
        self.calls = 0
        self.receipts = {}

    def execute_recovery(self, *, pr_number, expected_head_sha, action, job_id, idempotency_key):
        self.calls += 1
        receipt = {"accepted": True, "receipt_id": f"r-{idempotency_key[:12]}"}
        self.receipts[idempotency_key] = receipt
        return receipt

    def lookup_recovery(self, *, idempotency_key):
        receipt = self.receipts.get(idempotency_key)
        if receipt is None:
            return {"found": False}
        return {"found": True, **receipt}


def build(tmp_path: Path, gateway: Gateway):
    return IdempotentRecoveryTransactionV96(
        store=SQLiteRecoveryStateStoreV91(tmp_path / "state.db"),
        journal=SQLitePendingActionJournalV93(tmp_path / "pending.db"),
        gateway=gateway,
        owner="worker-1",
        nonce="n-1",
    )


def test_transaction_commits_state_evidence_and_journal(tmp_path):
    gateway = Gateway()
    tx = build(tmp_path, gateway)
    out = tx.execute(
        pr_number=7,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow="v63-strict-provider",
        job_id=101,
        conclusion="failure",
        log_excerpt="http_503",
        now=10,
    )
    assert out["status"] == "IDEMPOTENT_RECOVERY_TRANSACTION_COMMITTED"
    assert out["action"] == "rerun_failed_job"
    assert gateway.calls == 1
    state = tx.store.read(pr_number=7, head_sha=HEAD)
    assert state is not None and state.lease is None and len(state.ledger) == 1
    assert state.ledger[0].accepted is True
    pending = tx.executor.journal.read(out["idempotency_key"])
    assert pending is not None and pending.status == "COMMITTED"


def test_stale_head_fails_before_mutation(tmp_path):
    gateway = Gateway()
    tx = build(tmp_path, gateway)
    with pytest.raises(RuntimeError, match="stale_pr_head"):
        tx.execute(
            pr_number=7,
            expected_head_sha=HEAD,
            current_head_sha="b" * 40,
            workflow="ci",
            job_id=1,
            conclusion="failure",
            now=1,
        )
    assert gateway.calls == 0


def test_second_attempt_gets_new_idempotency_key(tmp_path):
    gateway = Gateway()
    tx = build(tmp_path, gateway)
    first = tx.execute(
        pr_number=7,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow="v63-strict-provider",
        job_id=101,
        conclusion="failure",
        log_excerpt="timeout",
        now=10,
    )
    second = tx.execute(
        pr_number=7,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow="v63-strict-provider",
        job_id=101,
        conclusion="failure",
        log_excerpt="timeout",
        now=20,
    )
    assert first["attempt"] == 0 and second["attempt"] == 1
    assert first["idempotency_key"] != second["idempotency_key"]
    assert gateway.calls == 2
    state = tx.store.read(pr_number=7, head_sha=HEAD)
    assert state is not None and len(state.ledger) == 2


def test_competing_worker_fails_closed_on_active_lease(tmp_path):
    gateway = Gateway()
    tx1 = build(tmp_path, gateway)
    state = tx1.store.initialize(pr_number=9, head_sha=HEAD)
    from protected_delivery.recovery_lease_v89 import RecoveryLeaseGuardV89
    from protected_delivery.recovery_state_v90 import next_state
    lease = RecoveryLeaseGuardV89.acquire(
        None,
        pr_number=9,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        owner="other",
        nonce="other-nonce",
        now=1,
        ttl=120,
    )
    tx1.store.compare_and_swap(
        expected_generation=state.generation,
        expected_state_hash=state.state_hash,
        replacement=next_state(state, lease=lease),
    )
    with pytest.raises(RuntimeError, match="recovery_lease_held"):
        tx1.execute(
            pr_number=9,
            expected_head_sha=HEAD,
            current_head_sha=HEAD,
            workflow="ci",
            job_id=2,
            conclusion="failure",
            now=2,
        )
    assert gateway.calls == 0
