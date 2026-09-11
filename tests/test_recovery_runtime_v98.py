import pytest

from protected_delivery.recovery_lease_v89 import RecoveryLeaseGuardV89
from protected_delivery.recovery_pending_v93 import SQLitePendingActionJournalV93
from protected_delivery.recovery_replay_v88 import replay_key
from protected_delivery.recovery_runtime_v98 import RecoveryRuntimeSupervisorV98
from protected_delivery.recovery_state_v90 import InMemoryRecoveryStateStoreV90, next_state

HEAD = "a" * 40


class Gateway:
    def __init__(self, *, found=True):
        self.execute_calls = 0
        self.lookup_calls = 0
        self.found = found

    def execute_recovery(self, **kwargs):
        self.execute_calls += 1
        return {"accepted": True, "receipt_id": "receipt-execute"}

    def lookup_recovery(self, *, idempotency_key):
        self.lookup_calls += 1
        if not self.found:
            return {"found": False}
        return {"found": True, "accepted": True, "receipt_id": "receipt-lookup"}


def _runtime(tmp_path, *, store=None, gateway=None, owner="worker-a", nonce="nonce-a"):
    return RecoveryRuntimeSupervisorV98(
        store=store or InMemoryRecoveryStateStoreV90(),
        journal=SQLitePendingActionJournalV93(tmp_path / "pending.sqlite"),
        gateway=gateway or Gateway(),
        owner=owner,
        nonce=nonce,
    )


def _seed_pending(runtime, *, owner="worker-a", nonce="nonce-a", now=10, commit=False):
    store = runtime.store
    state = store.initialize(pr_number=7, head_sha=HEAD)
    lease = RecoveryLeaseGuardV89.acquire(
        None,
        pr_number=7,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        owner=owner,
        nonce=nonce,
        now=now,
        ttl=120,
    )
    state = store.compare_and_swap(
        expected_generation=state.generation,
        expected_state_hash=state.state_hash,
        replacement=next_state(state, lease=lease),
    )
    key = replay_key(
        pr_number=7,
        head_sha=HEAD,
        workflow="v61-real-provider",
        job_id=91,
        action="rerun_failed_job",
        attempt=0,
    )
    pending = runtime.journal.prepare(
        pr_number=7,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow="v61-real-provider",
        job_id=91,
        action="rerun_failed_job",
        attempt=0,
        replay_key=key,
        owner=owner,
        lease_key=lease.lease_key,
    )
    if commit:
        pending = runtime.journal.commit(pending, receipt_id="receipt-existing")
    return state, lease, pending


def _run(runtime, *, now=20):
    return runtime.run(
        pr_number=7,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow="v61-real-provider",
        job_id=91,
        conclusion="failure",
        log_excerpt="transport_timeout_or_network",
        now=now,
    )


def test_fresh_request_executes_v96_transaction(tmp_path):
    gateway = Gateway()
    runtime = _runtime(tmp_path, gateway=gateway)
    result = _run(runtime)
    assert result["runtime_mode"] == "FRESH_TRANSACTION"
    assert result["status"] == "IDEMPOTENT_RECOVERY_TRANSACTION_COMMITTED"
    assert gateway.execute_calls == 1
    assert gateway.lookup_calls == 0
    state = runtime.store.read(pr_number=7, head_sha=HEAD)
    assert state.lease is None
    assert len(state.ledger) == 1


def test_committed_journal_repairs_state_without_external_call(tmp_path):
    gateway = Gateway()
    runtime = _runtime(tmp_path, gateway=gateway)
    _seed_pending(runtime, commit=True)
    result = _run(runtime)
    assert result["runtime_mode"] == "COMMITTED_STATE_RECONCILIATION"
    assert result["evidence_appended"] is True
    assert result["lease_cleared"] is True
    assert gateway.execute_calls == 0
    assert gateway.lookup_calls == 0


def test_prepared_journal_uses_lookup_then_repairs_state(tmp_path):
    gateway = Gateway(found=True)
    runtime = _runtime(tmp_path, gateway=gateway)
    _, _, pending = _seed_pending(runtime, commit=False)
    result = _run(runtime)
    assert result["runtime_mode"] == "PREPARED_LOOKUP_RECONCILIATION"
    assert gateway.execute_calls == 0
    assert gateway.lookup_calls == 1
    assert runtime.journal.read(pending.replay_key).status == "COMMITTED"
    state = runtime.store.read(pr_number=7, head_sha=HEAD)
    assert state.lease is None
    assert len(state.ledger) == 1


def test_unresolved_prepared_action_fails_closed_without_replay(tmp_path):
    gateway = Gateway(found=False)
    runtime = _runtime(tmp_path, gateway=gateway)
    _seed_pending(runtime, commit=False)
    with pytest.raises(RuntimeError, match="pending_recovery_unresolved"):
        _run(runtime)
    assert gateway.execute_calls == 0
    assert gateway.lookup_calls == 1


def test_active_foreign_lease_blocks_reconciliation(tmp_path):
    gateway = Gateway(found=True)
    store = InMemoryRecoveryStateStoreV90()
    runtime = _runtime(tmp_path, store=store, gateway=gateway, owner="worker-b", nonce="nonce-b")
    _seed_pending(runtime, owner="worker-a", nonce="nonce-a", now=10, commit=False)
    with pytest.raises(RuntimeError, match="recovery_lease_held"):
        _run(runtime, now=20)
    assert gateway.execute_calls == 0
    assert gateway.lookup_calls == 0


def test_expired_foreign_lease_can_be_reconciled_without_replay(tmp_path):
    gateway = Gateway(found=True)
    store = InMemoryRecoveryStateStoreV90()
    runtime = _runtime(tmp_path, store=store, gateway=gateway, owner="worker-b", nonce="nonce-b")
    _seed_pending(runtime, owner="worker-a", nonce="nonce-a", now=10, commit=True)
    result = _run(runtime, now=131)
    assert result["runtime_mode"] == "COMMITTED_STATE_RECONCILIATION"
    assert gateway.execute_calls == 0
    assert runtime.store.read(pr_number=7, head_sha=HEAD).lease is None


def test_stale_head_fails_before_any_runtime_action(tmp_path):
    gateway = Gateway()
    runtime = _runtime(tmp_path, gateway=gateway)
    with pytest.raises(RuntimeError, match="stale_pr_head"):
        runtime.run(
            pr_number=7,
            expected_head_sha=HEAD,
            current_head_sha="b" * 40,
            workflow="v61-real-provider",
            job_id=91,
            conclusion="failure",
            log_excerpt="timeout",
            now=20,
        )
    assert gateway.execute_calls == 0
    assert gateway.lookup_calls == 0
