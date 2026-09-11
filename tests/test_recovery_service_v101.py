import pytest

from protected_delivery.recovery_service_v101 import ProductionRecoveryServiceV101

HEAD = "a" * 40


class Gateway:
    def __init__(self):
        self.execute_calls = 0
        self.lookup_calls = 0
        self.receipts = {}

    def execute_recovery(self, *, idempotency_key, **kwargs):
        self.execute_calls += 1
        receipt = f"receipt-{self.execute_calls}"
        self.receipts[idempotency_key] = receipt
        return {"accepted": True, "receipt_id": receipt}

    def lookup_recovery(self, *, idempotency_key):
        self.lookup_calls += 1
        receipt = self.receipts.get(idempotency_key)
        if receipt is None:
            return {"found": False}
        return {"found": True, "accepted": True, "receipt_id": receipt}


def _request():
    return dict(
        pr_number=7,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow="v61-real-provider",
        job_id=91,
        conclusion="failure",
        log_excerpt="transport_timeout_or_network",
        now=20,
    )


def test_service_bootstrap_is_ready_and_uses_separate_durable_files(tmp_path):
    service = ProductionRecoveryServiceV101(
        root=tmp_path / "runtime", gateway=Gateway(), owner="worker-a", nonce="nonce-a"
    )
    health = service.health()
    assert health["status"] == "READY"
    assert health["durability_scope"] == "single_host"
    assert service.state_path.exists()
    assert service.journal_path.exists()
    assert service.state_path != service.journal_path


def test_service_runs_v100_to_convergence(tmp_path):
    gateway = Gateway()
    service = ProductionRecoveryServiceV101(
        root=tmp_path / "runtime", gateway=gateway, owner="worker-a", nonce="nonce-a"
    )
    result = service.run(**_request())
    assert result["status"] == "PRODUCTION_RECOVERY_CONVERGED"
    assert result["service_version"] == "10.1"
    assert result["post_audit"] == "CONVERGED"
    assert gateway.execute_calls == 1
    state = service.store.read(pr_number=7, head_sha=HEAD)
    assert state is not None
    assert len(state.ledger) == 1
    assert state.lease is None


def test_durable_state_survives_service_restart(tmp_path):
    root = tmp_path / "runtime"
    gateway = Gateway()
    first = ProductionRecoveryServiceV101(
        root=root, gateway=gateway, owner="worker-a", nonce="nonce-a"
    )
    first.run(**_request())
    before = first.store.read(pr_number=7, head_sha=HEAD)

    restarted = ProductionRecoveryServiceV101(
        root=root, gateway=gateway, owner="worker-a", nonce="nonce-a"
    )
    after = restarted.store.read(pr_number=7, head_sha=HEAD)
    assert after == before
    assert restarted.health()["status"] == "READY"


def test_stale_head_fails_closed_before_mutation(tmp_path):
    gateway = Gateway()
    service = ProductionRecoveryServiceV101(
        root=tmp_path / "runtime", gateway=gateway, owner="worker-a", nonce="nonce-a"
    )
    request = _request()
    request["current_head_sha"] = "b" * 40
    with pytest.raises(RuntimeError, match="stale_pr_head"):
        service.run(**request)
    assert gateway.execute_calls == 0


def test_gateway_contract_is_required(tmp_path):
    class BadGateway:
        pass

    with pytest.raises(TypeError, match="gateway_execute_recovery_missing"):
        ProductionRecoveryServiceV101(
            root=tmp_path / "runtime", gateway=BadGateway(), owner="worker-a", nonce="nonce-a"
        )


def test_root_must_be_directory(tmp_path):
    root = tmp_path / "not-a-dir"
    root.write_text("x")
    with pytest.raises(RuntimeError, match="recovery_service_root_not_directory"):
        ProductionRecoveryServiceV101(
            root=root, gateway=Gateway(), owner="worker-a", nonce="nonce-a"
        )
