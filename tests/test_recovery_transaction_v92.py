import pytest

from protected_delivery.recovery_lease_v89 import RecoveryLeaseGuardV89
from protected_delivery.recovery_orchestrator_v84 import WorkflowFailureV84
from protected_delivery.recovery_state_sqlite_v91 import SQLiteRecoveryStateStoreV91
from protected_delivery.recovery_state_v90 import next_state
from protected_delivery.recovery_transaction_v92 import RecoveryTransactionEngineV92

HEAD = "f" * 40


class Gateway:
    def __init__(self):
        self.calls = []

    def rerun_failed_job(self, *, job_id, expected_head_sha):
        self.calls.append(("rerun", job_id, expected_head_sha))
        return {"accepted": True}

    def request_rework(self, *, pr_number, expected_head_sha, reason):
        self.calls.append(("rework", pr_number, expected_head_sha, reason))
        return {"accepted": True}

    def escalate_human(self, *, pr_number, expected_head_sha, reason):
        self.calls.append(("human", pr_number, expected_head_sha, reason))
        return {"accepted": True}


def test_provider_recovery_is_leased_evidenced_and_released(tmp_path):
    store = SQLiteRecoveryStateStoreV91(tmp_path / "recovery.db")
    gateway = Gateway()
    engine = RecoveryTransactionEngineV92(store=store, gateway=gateway, owner="worker-a", nonce="n1")
    result = engine.execute(
        pr_number=60,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        failure=WorkflowFailureV84("v71-autonomous-delivery", "failure", 101, "http_503"),
        now=100,
    )
    assert result["status"] == "RECOVERY_ACTION_ACCEPTED"
    assert result["action"] == "rerun_failed_job"
    assert result["lease_release"] == "RELEASED"
    state = store.read(pr_number=60, head_sha=HEAD)
    assert state.lease is None
    assert len(state.ledger) == 1
    assert state.generation == 3
    assert gateway.calls == [("rerun", 101, HEAD)]


def test_second_attempt_advances_evidence_monotonically(tmp_path):
    store = SQLiteRecoveryStateStoreV91(tmp_path / "recovery.db")
    gateway = Gateway()
    engine = RecoveryTransactionEngineV92(store=store, gateway=gateway, owner="worker-a", nonce="n1")
    failure = WorkflowFailureV84("v71-autonomous-delivery", "failure", 101, "http_503")
    first = engine.execute(pr_number=60, expected_head_sha=HEAD, current_head_sha=HEAD, failure=failure, now=100)
    second = engine.execute(pr_number=60, expected_head_sha=HEAD, current_head_sha=HEAD, failure=failure, now=200)
    assert first["attempt"] == 0
    assert second["attempt"] == 1
    state = store.read(pr_number=60, head_sha=HEAD)
    assert [event.attempt for event in state.ledger] == [0, 1]
    assert len(set(event.event_hash for event in state.ledger)) == 2


def test_active_competing_lease_blocks_transaction_before_gateway(tmp_path):
    store = SQLiteRecoveryStateStoreV91(tmp_path / "recovery.db")
    state = store.initialize(pr_number=60, head_sha=HEAD)
    lease = RecoveryLeaseGuardV89.acquire(
        None,
        pr_number=60,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        owner="worker-a",
        nonce="held",
        now=100,
        ttl=100,
    )
    leased = next_state(state, lease=lease)
    store.compare_and_swap(
        expected_generation=state.generation,
        expected_state_hash=state.state_hash,
        replacement=leased,
    )
    gateway = Gateway()
    engine = RecoveryTransactionEngineV92(store=store, gateway=gateway, owner="worker-b", nonce="other")
    with pytest.raises(RuntimeError, match="recovery_lease_held"):
        engine.execute(
            pr_number=60,
            expected_head_sha=HEAD,
            current_head_sha=HEAD,
            failure=WorkflowFailureV84("ci", "failure", 7, "assertion failed"),
            now=150,
        )
    assert gateway.calls == []


def test_stale_head_fails_before_state_or_gateway_mutation(tmp_path):
    store = SQLiteRecoveryStateStoreV91(tmp_path / "recovery.db")
    gateway = Gateway()
    engine = RecoveryTransactionEngineV92(store=store, gateway=gateway, owner="worker-a", nonce="n1")
    with pytest.raises(RuntimeError, match="stale_pr_head"):
        engine.execute(
            pr_number=60,
            expected_head_sha=HEAD,
            current_head_sha="a" * 40,
            failure=WorkflowFailureV84("ci", "failure", 7, "assertion failed"),
            now=100,
        )
    assert store.read(pr_number=60, head_sha=HEAD) is None
    assert gateway.calls == []


def test_code_failure_requests_rework_and_persists_receipt(tmp_path):
    store = SQLiteRecoveryStateStoreV91(tmp_path / "recovery.db")
    gateway = Gateway()
    engine = RecoveryTransactionEngineV92(store=store, gateway=gateway, owner="worker-a", nonce="n1")
    result = engine.execute(
        pr_number=60,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        failure=WorkflowFailureV84("ci", "failure", 7, "assertion failed"),
        now=100,
    )
    assert result["action"] == "autonomous_rework"
    assert gateway.calls[0][0] == "rework"
    assert store.read(pr_number=60, head_sha=HEAD).ledger[0].action == "autonomous_rework"


def test_rejected_gateway_leaves_lease_fail_closed(tmp_path):
    class RejectingGateway(Gateway):
        def rerun_failed_job(self, *, job_id, expected_head_sha):
            self.calls.append(("rerun", job_id, expected_head_sha))
            return {"accepted": False}

    store = SQLiteRecoveryStateStoreV91(tmp_path / "recovery.db")
    gateway = RejectingGateway()
    engine = RecoveryTransactionEngineV92(store=store, gateway=gateway, owner="worker-a", nonce="n1", lease_ttl=30)
    with pytest.raises(RuntimeError, match="rerun_not_accepted"):
        engine.execute(
            pr_number=60,
            expected_head_sha=HEAD,
            current_head_sha=HEAD,
            failure=WorkflowFailureV84("v71-autonomous-delivery", "failure", 101, "http_503"),
            now=100,
        )
    state = store.read(pr_number=60, head_sha=HEAD)
    assert state.lease is not None
    assert state.lease.expires_at == 130
    assert state.ledger == ()
