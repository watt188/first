import tempfile
from pathlib import Path

import pytest

from protected_delivery.recovery_auth_rotation_v104 import (
    RotatingAuthenticatedRecoveryCommandV104,
    SQLiteAuthenticationKeyringV104,
)
from protected_delivery.recovery_auth_v103 import SQLiteReplayNonceStoreV103


class Command:
    def handle(self, payload):
        return {"accepted": payload["operation"]}


def setup(tmp: Path):
    secrets = {"old": b"o" * 32, "new": b"n" * 32}
    keyring = SQLiteAuthenticationKeyringV104(tmp / "keys.db", secrets=secrets)
    nonce = SQLiteReplayNonceStoreV103(tmp / "nonce.db")
    auth = RotatingAuthenticatedRecoveryCommandV104(command=Command(), keyring=keyring, nonce_store=nonce, max_clock_skew=300)
    return secrets, keyring, auth


def test_rotation_overlap_and_signing_cutover():
    with tempfile.TemporaryDirectory() as td:
        _, ring, auth = setup(Path(td))
        ring.activate(key_id="old", not_before=100)
        ring.activate(key_id="new", not_before=150)
        old = auth.sign(key_id="old", timestamp=160, nonce="old_nonce_12345678", payload={"operation": "health"})
        new = auth.sign(key_id="new", timestamp=160, nonce="new_nonce_12345678", payload={"operation": "health"})
        assert auth.handle(old, now=160)["rotation_version"] == "10.4"
        assert auth.handle(new, now=160)["result"]["accepted"] == "health"
        ring.retire(key_id="old", retired_at=170)
        with pytest.raises(RuntimeError, match="authentication_key_not_active"):
            auth.sign(key_id="old", timestamp=171, nonce="late_nonce_123456", payload={"operation": "health"})
        assert auth.sign(key_id="new", timestamp=171, nonce="newer_nonce_123456", payload={"operation": "health"})["key_id"] == "new"


def test_retired_key_accepts_pre_cutover_inflight_envelope():
    with tempfile.TemporaryDirectory() as td:
        secrets, ring, auth = setup(Path(td))
        ring.activate(key_id="old", not_before=100)
        env = auth.sign(key_id="old", timestamp=160, nonce="inflight_nonce_1234", payload={"operation": "health"})
        ring.retire(key_id="old", retired_at=165)
        assert auth.handle(env, now=170)["key_id"] == "old"
        from protected_delivery.recovery_auth_v103 import sign_envelope
        after = sign_envelope(secret=secrets["old"], key_id="old", timestamp=166, nonce="after_cutover_1234", payload={"operation": "health"})
        with pytest.raises((RuntimeError, ValueError), match="authentication"):
            auth.handle(after, now=170)


def test_revocation_is_immediate_even_for_pre_revocation_signature():
    with tempfile.TemporaryDirectory() as td:
        _, ring, auth = setup(Path(td))
        ring.activate(key_id="old", not_before=100)
        env = auth.sign(key_id="old", timestamp=150, nonce="revoke_nonce_123456", payload={"operation": "health"})
        ring.revoke(key_id="old", revoked_at=155)
        with pytest.raises((RuntimeError, ValueError), match="authentication"):
            auth.handle(env, now=156)


def test_rotation_metadata_persists_without_secret_material():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        secrets, ring, _ = setup(root)
        ring.activate(key_id="old", not_before=100)
        ring.retire(key_id="old", retired_at=200)
        reopened = SQLiteAuthenticationKeyringV104(root / "keys.db", secrets=secrets)
        assert reopened.status(now=201)["keys"] == [{"key_id": "old", "state": "RETIRED", "generation": 1}]
        raw = (root / "keys.db").read_bytes()
        assert secrets["old"] not in raw


def test_replay_protection_survives_rotation_layer_restart():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        secrets, ring, auth = setup(root)
        ring.activate(key_id="new", not_before=100)
        env = auth.sign(key_id="new", timestamp=150, nonce="durable_nonce_12345", payload={"operation": "health"})
        assert auth.handle(env, now=150)["result"]["accepted"] == "health"
        reopened_ring = SQLiteAuthenticationKeyringV104(root / "keys.db", secrets=secrets)
        reopened = RotatingAuthenticatedRecoveryCommandV104(
            command=Command(), keyring=reopened_ring,
            nonce_store=SQLiteReplayNonceStoreV103(root / "nonce.db"), max_clock_skew=300,
        )
        with pytest.raises(RuntimeError, match="authenticated_command_replay"):
            reopened.handle(env, now=151)
