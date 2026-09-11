import json

import pytest

from protected_delivery.recovery_auth_v103 import (
    AuthenticatedRecoveryCommandV103,
    SQLiteReplayNonceStoreV103,
    sign_envelope,
)


SECRET = b"k" * 32


class DummyCommand:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = 0

    def handle(self, payload):
        self.calls += 1
        if self.fail:
            raise RuntimeError("downstream_failed")
        return {"ok": True, "payload": payload}


def make_auth(tmp_path, *, command=None, skew=300):
    command = command or DummyCommand()
    store = SQLiteReplayNonceStoreV103(tmp_path / "auth.sqlite3")
    auth = AuthenticatedRecoveryCommandV103(
        command=command,
        keys={"primary": SECRET},
        nonce_store=store,
        max_clock_skew=skew,
    )
    return auth, command


def envelope(payload=None, *, timestamp=1000, nonce="nonce_1234567890abcd"):
    return sign_envelope(
        secret=SECRET,
        key_id="primary",
        timestamp=timestamp,
        nonce=nonce,
        payload=payload or {"operation": "health"},
    )


def test_valid_envelope_executes_command(tmp_path):
    auth, command = make_auth(tmp_path)
    result = auth.handle(envelope(), now=1000)
    assert result["auth_version"] == "10.3"
    assert result["key_id"] == "primary"
    assert result["result"]["ok"] is True
    assert command.calls == 1


def test_signature_tamper_fails_before_command(tmp_path):
    auth, command = make_auth(tmp_path)
    signed = envelope()
    signed["payload"] = {"operation": "recover"}
    with pytest.raises(RuntimeError, match="signature_mismatch"):
        auth.handle(signed, now=1000)
    assert command.calls == 0


def test_expired_envelope_fails_closed(tmp_path):
    auth, command = make_auth(tmp_path, skew=10)
    with pytest.raises(RuntimeError, match="expired"):
        auth.handle(envelope(timestamp=1000), now=1011)
    assert command.calls == 0


def test_replay_rejected_across_store_reopen(tmp_path):
    auth, command = make_auth(tmp_path)
    signed = envelope()
    auth.handle(signed, now=1000)

    reopened = AuthenticatedRecoveryCommandV103(
        command=command,
        keys={"primary": SECRET},
        nonce_store=SQLiteReplayNonceStoreV103(tmp_path / "auth.sqlite3"),
    )
    with pytest.raises(RuntimeError, match="replay"):
        reopened.handle(signed, now=1000)
    assert command.calls == 1


def test_nonce_consumed_before_ambiguous_downstream_failure(tmp_path):
    command = DummyCommand(fail=True)
    auth, _ = make_auth(tmp_path, command=command)
    signed = envelope(nonce="nonce_failure_123456")
    with pytest.raises(RuntimeError, match="downstream_failed"):
        auth.handle(signed, now=1000)
    with pytest.raises(RuntimeError, match="replay"):
        auth.handle(signed, now=1000)
    assert command.calls == 1


def test_unknown_envelope_field_rejected(tmp_path):
    auth, _ = make_auth(tmp_path)
    signed = envelope()
    signed["extra"] = "nope"
    with pytest.raises(ValueError, match="envelope_fields"):
        auth.handle(signed, now=1000)


def test_unknown_key_rejected(tmp_path):
    auth, _ = make_auth(tmp_path)
    signed = envelope()
    signed["key_id"] = "missing"
    with pytest.raises(RuntimeError, match="unknown_authentication_key"):
        auth.handle(signed, now=1000)


def test_short_secret_rejected(tmp_path):
    store = SQLiteReplayNonceStoreV103(tmp_path / "auth.sqlite3")
    with pytest.raises(ValueError, match="secret_too_short"):
        AuthenticatedRecoveryCommandV103(
            command=DummyCommand(), keys={"primary": b"short"}, nonce_store=store
        )


def test_json_output_is_canonical(tmp_path):
    auth, _ = make_auth(tmp_path)
    signed = envelope(nonce="nonce_json_123456789")
    raw = json.dumps(signed, indent=2)
    out = auth.handle_json(raw, now=1000)
    assert out == json.dumps(json.loads(out), sort_keys=True, separators=(",", ":"))
