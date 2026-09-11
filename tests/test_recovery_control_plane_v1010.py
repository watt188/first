import json
import tempfile

import pytest

from protected_delivery.recovery_control_plane_v1010 import ProductionRecoveryControlPlaneV1010

SHA = "a" * 40


class Gateway:
    def __init__(self):
        self.executed = []
        self.receipts = {}

    def execute_recovery(self, pr_number, expected_head_sha, action, job_id, idempotency_key):
        receipt = {"accepted": True, "receipt": f"r-{idempotency_key}"}
        self.executed.append((pr_number, expected_head_sha, action, job_id, idempotency_key))
        self.receipts[idempotency_key] = receipt
        return receipt

    def lookup_recovery(self, idempotency_key):
        return self.receipts.get(idempotency_key)


class Response:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps({"head": {"sha": SHA}}).encode()


def opener(request, timeout):
    return Response()


def caps(**overrides):
    value = {
        "provider": "test-gateway",
        "scope": "single-host",
        "durable_idempotency": True,
        "lookup_by_idempotency_key": True,
    }
    value.update(overrides)
    return value


def payload(**overrides):
    value = {
        "operation": "recover",
        "pr_number": 54,
        "expected_head_sha": SHA,
        "current_head_sha": "b" * 40,
        "workflow": "ci",
        "job_id": 1,
        "conclusion": "failure",
        "log_excerpt": "failure",
        "now": 100,
    }
    value.update(overrides)
    return value


def build(tmp, *, capabilities=lambda: caps()):
    gateway = Gateway()
    plane = ProductionRecoveryControlPlaneV1010(
        root=tmp,
        gateway=gateway,
        owner="worker-1",
        nonce="runtime-nonce",
        secrets={"k1": b"x" * 32},
        repository_full_name="watt188/first",
        gateway_capabilities=capabilities,
        github_opener=opener,
    )
    plane.activate_key(key_id="k1", not_before=0)
    return plane, gateway


def test_health_reports_final_control_plane(tmp_path):
    plane, _ = build(tmp_path)
    health = plane.health(now=100)
    assert health["control_plane_version"] == "10.10"
    assert health["authoritative_head_source"] == "github_pull_request_api"
    assert health["production_readiness_gate"] is True


def test_authenticated_recover_uses_live_head_and_readiness(tmp_path):
    plane, gateway = build(tmp_path)
    envelope = plane.sign(key_id="k1", timestamp=100, nonce="abcdefghijklmnop", payload=payload())
    result = plane.handle(envelope, now=100)
    assert result["control_plane_version"] == "10.10"
    nested = result["result"]
    assert nested["result"]["status"] == "PRODUCTION_RECOVERY_CONVERGED"
    assert nested["authoritative_head_sha"] == SHA
    assert nested["gateway_capabilities_attested"] is True
    assert len(gateway.executed) == 1
    assert gateway.executed[0][1] == SHA


def test_stale_expected_head_fails_before_external_mutation(tmp_path):
    plane, gateway = build(tmp_path)
    envelope = plane.sign(
        key_id="k1", timestamp=100, nonce="abcdefghijklmnop", payload=payload(expected_head_sha="b" * 40)
    )
    with pytest.raises(RuntimeError, match="stale_pr_head"):
        plane.handle(envelope, now=100)
    assert gateway.executed == []


def test_unready_gateway_fails_before_external_mutation(tmp_path):
    plane, gateway = build(tmp_path, capabilities=lambda: caps(durable_idempotency=False))
    envelope = plane.sign(key_id="k1", timestamp=100, nonce="abcdefghijklmnop", payload=payload())
    with pytest.raises(RuntimeError, match="recovery_gateway_not_production_ready"):
        plane.handle(envelope, now=100)
    assert gateway.executed == []


def test_replay_is_rejected(tmp_path):
    plane, gateway = build(tmp_path)
    envelope = plane.sign(key_id="k1", timestamp=100, nonce="abcdefghijklmnop", payload=payload())
    plane.handle(envelope, now=100)
    with pytest.raises(RuntimeError, match="authenticated_command_replay"):
        plane.handle(envelope, now=100)
    assert len(gateway.executed) == 1
