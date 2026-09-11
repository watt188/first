import pytest

from protected_delivery.recovery_pending_v93 import SQLitePendingActionJournalV93
from protected_delivery.recovery_production_v100 import ProductionRecoveryRuntimeV100
from protected_delivery.recovery_state_v90 import InMemoryRecoveryStateStoreV90

HEAD = "a" * 40


class Gateway:
    def __init__(self):
        self.execute_calls = 0
        self.lookup_calls = 0

    def execute_recovery(self, **kwargs):
        self.execute_calls += 1
        return {"accepted": True, "receipt_id": "receipt-prod"}

    def lookup_recovery(self, *, idempotency_key):
        self.lookup_calls += 1
        return {"found": True, "accepted": True, "receipt_id": "receipt-prod"}


def _runtime(tmp_path, *, gateway=None):
    return ProductionRecoveryRuntimeV100(
        store=InMemoryRecoveryStateStoreV90(),
        journal=SQLitePendingActionJournalV93(tmp_path / "pending.sqlite"),
        gateway=gateway or Gateway(),
        owner="worker-a",
        nonce="nonce-a",
    )


def _run(runtime, **overrides):
    args = dict(
        pr_number=7,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow="v61-real-provider",
        job_id=91,
        conclusion="failure",
        log_excerpt="transport_timeout_or_network",
        now=20,
    )
    args.update(overrides)
    return runtime.run(**args)


def test_fresh_recovery_must_end_converged(tmp_path):
    gateway = Gateway()
    runtime = _runtime(tmp_path, gateway=gateway)
    result = _run(runtime)
    assert result["status"] == "PRODUCTION_RECOVERY_CONVERGED"
    assert result["pre_audit"] == "HEALTHY_IDLE"
    assert result["post_audit"] == "CONVERGED"
    assert result["runtime_mode"] == "FRESH_TRANSACTION"
    assert result["receipt_id"] == "receipt-prod"
    assert gateway.execute_calls == 1


def test_stale_head_fails_before_mutation(tmp_path):
    gateway = Gateway()
    runtime = _runtime(tmp_path, gateway=gateway)
    with pytest.raises(RuntimeError, match="stale_pr_head"):
        _run(runtime, current_head_sha="b" * 40)
    assert gateway.execute_calls == 0
    assert gateway.lookup_calls == 0


def test_invalid_time_fails_before_mutation(tmp_path):
    gateway = Gateway()
    runtime = _runtime(tmp_path, gateway=gateway)
    with pytest.raises(ValueError, match="invalid_runtime_time"):
        _run(runtime, now=-1)
    assert gateway.execute_calls == 0


def test_second_logical_attempt_is_distinct_and_converges(tmp_path):
    gateway = Gateway()
    runtime = _runtime(tmp_path, gateway=gateway)
    first = _run(runtime)
    second = _run(runtime, now=30)
    assert first["attempt"] == 0
    assert second["attempt"] == 1
    assert first["replay_key"] != second["replay_key"]
    assert second["post_audit"] == "CONVERGED"
    assert gateway.execute_calls == 2


def test_constructor_requires_identity(tmp_path):
    with pytest.raises(ValueError, match="invalid_production_runtime_identity"):
        ProductionRecoveryRuntimeV100(
            store=InMemoryRecoveryStateStoreV90(),
            journal=SQLitePendingActionJournalV93(tmp_path / "pending.sqlite"),
            gateway=Gateway(),
            owner="",
            nonce="nonce-a",
        )
