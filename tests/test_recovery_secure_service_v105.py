import tempfile
from pathlib import Path

import pytest

from protected_delivery.recovery_secure_service_v105 import SecureProductionRecoveryServiceV105


class Gateway:
    def execute_recovery(self, **kwargs):
        return {"accepted": True, "receipt_id": "receipt-1"}

    def lookup_recovery(self, **kwargs):
        return {"found": True, "accepted": True, "receipt_id": "receipt-1"}


def build(root: Path, secrets=None):
    return SecureProductionRecoveryServiceV105(
        root=root,
        gateway=Gateway(),
        owner="worker-a",
        nonce="runtime-nonce",
        secrets=secrets or {"k1": b"a" * 32, "k2": b"b" * 32},
        max_clock_skew=300,
    )


def test_secure_service_health_and_authenticated_health_command():
    with tempfile.TemporaryDirectory() as td:
        service = build(Path(td))
        service.activate_key(key_id="k1", not_before=100)
        health = service.health(now=120)
        assert health["status"] == "READY"
        assert health["authentication_mode"] == "hmac_sha256_rotating"
        env = service.sign(
            key_id="k1", timestamp=120, nonce="secure_nonce_123456",
            payload={"operation": "health"},
        )
        result = service.handle(env, now=120)
        assert result["secure_service_version"] == "10.5"
        assert result["result"]["operation"] == "health"
        assert result["result"]["result"]["status"] == "READY"


def test_rotation_cutover_through_single_service_boundary():
    with tempfile.TemporaryDirectory() as td:
        service = build(Path(td))
        service.activate_key(key_id="k1", not_before=100)
        service.activate_key(key_id="k2", not_before=150)
        old = service.sign(
            key_id="k1", timestamp=160, nonce="old_secure_nonce_12",
            payload={"operation": "health"},
        )
        service.retire_key(key_id="k1", retired_at=170)
        assert service.handle(old, now=171)["key_id"] == "k1"
        with pytest.raises(RuntimeError, match="authentication_key_not_active"):
            service.sign(
                key_id="k1", timestamp=171, nonce="late_secure_nonce12",
                payload={"operation": "health"},
            )
        fresh = service.sign(
            key_id="k2", timestamp=171, nonce="new_secure_nonce_123",
            payload={"operation": "health"},
        )
        assert service.handle(fresh, now=171)["key_id"] == "k2"


def test_replay_and_rotation_state_survive_service_restart():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        secrets = {"k1": b"a" * 32, "k2": b"b" * 32}
        first = build(root, secrets)
        first.activate_key(key_id="k1", not_before=100)
        env = first.sign(
            key_id="k1", timestamp=150, nonce="restart_nonce_12345",
            payload={"operation": "health"},
        )
        assert first.handle(env, now=150)["key_id"] == "k1"
        reopened = build(root, secrets)
        assert reopened.health(now=151)["authentication"]["keys"][0]["state"] == "ACTIVE"
        with pytest.raises(RuntimeError, match="authenticated_command_replay"):
            reopened.handle(env, now=151)


def test_secret_material_is_not_persisted_by_service_metadata_databases():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        secret = b"z" * 32
        service = build(root, {"k1": secret})
        service.activate_key(key_id="k1", not_before=1)
        assert secret not in service.keyring_path.read_bytes()
        assert secret not in service.nonce_path.read_bytes()


def test_revocation_fails_closed_at_service_boundary():
    with tempfile.TemporaryDirectory() as td:
        service = build(Path(td))
        service.activate_key(key_id="k1", not_before=100)
        env = service.sign(
            key_id="k1", timestamp=120, nonce="revoked_nonce_12345",
            payload={"operation": "health"},
        )
        service.revoke_key(key_id="k1", revoked_at=121)
        with pytest.raises((RuntimeError, ValueError), match="authentication"):
            service.handle(env, now=122)
