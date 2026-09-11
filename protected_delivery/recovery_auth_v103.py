import hashlib
import hmac
import json
import re
import sqlite3
from pathlib import Path

VERSION = "10.3"
_NONCE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_SIGNATURE = re.compile(r"^[0-9a-f]{64}$")
_ENVELOPE_KEYS = {"key_id", "timestamp", "nonce", "payload", "signature"}


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _signing_bytes(*, key_id: str, timestamp: int, nonce: str, payload: dict) -> bytes:
    return f"{key_id}\n{timestamp}\n{nonce}\n{_canonical(payload)}".encode("utf-8")


def sign_envelope(*, secret: bytes, key_id: str, timestamp: int, nonce: str, payload: dict) -> dict:
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ValueError("authentication_secret_too_short")
    if not isinstance(key_id, str) or not key_id.strip():
        raise ValueError("invalid_key_id")
    if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0:
        raise ValueError("invalid_auth_timestamp")
    if not isinstance(nonce, str) or not _NONCE.fullmatch(nonce):
        raise ValueError("invalid_auth_nonce")
    if not isinstance(payload, dict):
        raise TypeError("authenticated_payload_must_be_object")
    signature = hmac.new(
        secret,
        _signing_bytes(key_id=key_id.strip(), timestamp=timestamp, nonce=nonce, payload=payload),
        hashlib.sha256,
    ).hexdigest()
    return {
        "key_id": key_id.strip(),
        "timestamp": timestamp,
        "nonce": nonce,
        "payload": payload,
        "signature": signature,
    }


class SQLiteReplayNonceStoreV103:
    """Durable single-host replay guard for authenticated command nonces."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS recovery_auth_nonce (
                    key_id TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    PRIMARY KEY (key_id, nonce)
                )
                """
            )

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def claim(self, *, key_id: str, nonce: str, timestamp: int) -> None:
        with self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO recovery_auth_nonce(key_id, nonce, timestamp) VALUES (?, ?, ?)",
                    (key_id, nonce, timestamp),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise RuntimeError("authenticated_command_replay") from exc
            except Exception:
                conn.rollback()
                raise


class AuthenticatedRecoveryCommandV103:
    """HMAC-authenticated, replay-protected boundary over the V10.2 command surface.

    V10.3 authenticates a canonical JSON envelope before V10.2 receives a
    command. A durable SQLite nonce claim prevents replay across process
    restarts. The nonce is intentionally consumed before command execution, so
    ambiguous downstream failures cannot be retried by replaying the same
    authenticated envelope.
    """

    def __init__(self, *, command, keys: dict[str, bytes], nonce_store: SQLiteReplayNonceStoreV103,
                 max_clock_skew: int = 300):
        if not callable(getattr(command, "handle", None)):
            raise TypeError("command_handle_missing")
        if not isinstance(keys, dict) or not keys:
            raise ValueError("authentication_keys_missing")
        normalized = {}
        for key_id, secret in keys.items():
            if not isinstance(key_id, str) or not key_id.strip():
                raise ValueError("invalid_key_id")
            if not isinstance(secret, bytes) or len(secret) < 32:
                raise ValueError("authentication_secret_too_short")
            normalized[key_id.strip()] = secret
        if not isinstance(max_clock_skew, int) or isinstance(max_clock_skew, bool) or max_clock_skew < 0:
            raise ValueError("invalid_max_clock_skew")
        self.command = command
        self.keys = normalized
        self.nonce_store = nonce_store
        self.max_clock_skew = max_clock_skew

    def handle(self, envelope: dict, *, now: int) -> dict:
        if not isinstance(envelope, dict):
            raise TypeError("authenticated_envelope_must_be_object")
        if set(envelope) != _ENVELOPE_KEYS:
            raise ValueError("invalid_authenticated_envelope_fields")
        if not isinstance(now, int) or isinstance(now, bool) or now < 0:
            raise ValueError("invalid_auth_now")

        key_id = envelope.get("key_id")
        timestamp = envelope.get("timestamp")
        nonce = envelope.get("nonce")
        payload = envelope.get("payload")
        signature = envelope.get("signature")
        if not isinstance(key_id, str) or key_id not in self.keys:
            raise RuntimeError("unknown_authentication_key")
        if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0:
            raise ValueError("invalid_auth_timestamp")
        if abs(now - timestamp) > self.max_clock_skew:
            raise RuntimeError("authenticated_command_expired")
        if not isinstance(nonce, str) or not _NONCE.fullmatch(nonce):
            raise ValueError("invalid_auth_nonce")
        if not isinstance(payload, dict):
            raise TypeError("authenticated_payload_must_be_object")
        if not isinstance(signature, str) or not _SIGNATURE.fullmatch(signature):
            raise ValueError("invalid_auth_signature")

        expected = hmac.new(
            self.keys[key_id],
            _signing_bytes(key_id=key_id, timestamp=timestamp, nonce=nonce, payload=payload),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise RuntimeError("authenticated_command_signature_mismatch")

        self.nonce_store.claim(key_id=key_id, nonce=nonce, timestamp=timestamp)
        result = self.command.handle(payload)
        return {
            "auth_version": VERSION,
            "key_id": key_id,
            "timestamp": timestamp,
            "nonce": nonce,
            "result": result,
        }

    def handle_json(self, raw: str, *, now: int) -> str:
        if not isinstance(raw, str):
            raise TypeError("authenticated_json_must_be_text")
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid_authenticated_json") from exc
        return _canonical(self.handle(envelope, now=now))
