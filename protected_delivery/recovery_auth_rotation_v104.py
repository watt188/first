import sqlite3
from pathlib import Path

from protected_delivery.recovery_auth_v103 import AuthenticatedRecoveryCommandV103, sign_envelope

VERSION = "10.4"


class SQLiteAuthenticationKeyringV104:
    """Durable key lifecycle metadata without persisting authentication secrets.

    Secrets are supplied by the caller at process start. SQLite stores only key
    ids and activation/retirement/revocation metadata so rotation survives
    restarts without writing HMAC material to disk.
    """

    def __init__(self, path: str | Path, *, secrets: dict[str, bytes]):
        if not isinstance(secrets, dict) or not secrets:
            raise ValueError("authentication_keys_missing")
        normalized = {}
        for key_id, secret in secrets.items():
            if not isinstance(key_id, str) or not key_id.strip():
                raise ValueError("invalid_key_id")
            if not isinstance(secret, bytes) or len(secret) < 32:
                raise ValueError("authentication_secret_too_short")
            normalized[key_id.strip()] = secret
        self.path = str(path)
        self.secrets = normalized
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS recovery_auth_key (
                    key_id TEXT PRIMARY KEY,
                    not_before INTEGER NOT NULL,
                    retired_at INTEGER,
                    revoked_at INTEGER,
                    generation INTEGER NOT NULL
                )
                """
            )

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    @staticmethod
    def _time(value: int, name: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(name)
        return value

    def activate(self, *, key_id: str, not_before: int) -> None:
        key_id = key_id.strip() if isinstance(key_id, str) else ""
        if key_id not in self.secrets:
            raise RuntimeError("authentication_secret_unavailable")
        not_before = self._time(not_before, "invalid_key_not_before")
        with self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT generation FROM recovery_auth_key WHERE key_id=?", (key_id,)
                ).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO recovery_auth_key(key_id, not_before, retired_at, revoked_at, generation) VALUES (?, ?, NULL, NULL, 0)",
                        (key_id, not_before),
                    )
                else:
                    raise RuntimeError("authentication_key_already_registered")
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def retire(self, *, key_id: str, retired_at: int) -> None:
        retired_at = self._time(retired_at, "invalid_key_retired_at")
        with self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT not_before, retired_at, revoked_at, generation FROM recovery_auth_key WHERE key_id=?",
                    (key_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError("authentication_key_unknown")
                if row[2] is not None:
                    raise RuntimeError("authentication_key_revoked")
                if row[1] is not None:
                    raise RuntimeError("authentication_key_already_retired")
                if retired_at < int(row[0]):
                    raise RuntimeError("authentication_key_invalid_retirement")
                conn.execute(
                    "UPDATE recovery_auth_key SET retired_at=?, generation=? WHERE key_id=? AND generation=?",
                    (retired_at, int(row[3]) + 1, key_id, int(row[3])),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def revoke(self, *, key_id: str, revoked_at: int) -> None:
        revoked_at = self._time(revoked_at, "invalid_key_revoked_at")
        with self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT not_before, revoked_at, generation FROM recovery_auth_key WHERE key_id=?",
                    (key_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError("authentication_key_unknown")
                if row[1] is not None:
                    raise RuntimeError("authentication_key_already_revoked")
                if revoked_at < int(row[0]):
                    raise RuntimeError("authentication_key_invalid_revocation")
                conn.execute(
                    "UPDATE recovery_auth_key SET revoked_at=?, generation=? WHERE key_id=? AND generation=?",
                    (revoked_at, int(row[2]) + 1, key_id, int(row[2])),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def verification_keys(self, *, signed_at: int, now: int) -> dict[str, bytes]:
        signed_at = self._time(signed_at, "invalid_auth_timestamp")
        now = self._time(now, "invalid_auth_now")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key_id, not_before, retired_at, revoked_at FROM recovery_auth_key"
            ).fetchall()
        accepted = {}
        for key_id, not_before, retired_at, revoked_at in rows:
            if key_id not in self.secrets:
                continue
            if signed_at < int(not_before):
                continue
            if retired_at is not None and signed_at > int(retired_at):
                continue
            if revoked_at is not None and now >= int(revoked_at):
                continue
            accepted[str(key_id)] = self.secrets[str(key_id)]
        return accepted

    def signing_secret(self, *, key_id: str, now: int) -> bytes:
        now = self._time(now, "invalid_auth_now")
        keys = self.verification_keys(signed_at=now, now=now)
        if key_id not in keys:
            raise RuntimeError("authentication_key_not_active")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT retired_at, revoked_at FROM recovery_auth_key WHERE key_id=?", (key_id,)
            ).fetchone()
        if row is None or row[0] is not None or row[1] is not None:
            raise RuntimeError("authentication_key_not_active")
        return keys[key_id]

    def status(self, *, now: int) -> dict:
        now = self._time(now, "invalid_auth_now")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key_id, not_before, retired_at, revoked_at, generation FROM recovery_auth_key ORDER BY key_id"
            ).fetchall()
        items = []
        for key_id, not_before, retired_at, revoked_at, generation in rows:
            if revoked_at is not None and now >= int(revoked_at):
                state = "REVOKED"
            elif retired_at is not None and now > int(retired_at):
                state = "RETIRED"
            elif now < int(not_before):
                state = "PENDING"
            else:
                state = "ACTIVE"
            items.append({"key_id": key_id, "state": state, "generation": int(generation)})
        return {"version": VERSION, "keys": items}


class RotatingAuthenticatedRecoveryCommandV104:
    """V10.3 authenticated command boundary with durable rotation policy."""

    def __init__(self, *, command, keyring: SQLiteAuthenticationKeyringV104, nonce_store, max_clock_skew: int = 300):
        self.command = command
        self.keyring = keyring
        self.nonce_store = nonce_store
        self.max_clock_skew = max_clock_skew

    def handle(self, envelope: dict, *, now: int) -> dict:
        if not isinstance(envelope, dict):
            raise TypeError("authenticated_envelope_must_be_object")
        signed_at = envelope.get("timestamp")
        if not isinstance(signed_at, int) or isinstance(signed_at, bool):
            raise ValueError("invalid_auth_timestamp")
        keys = self.keyring.verification_keys(signed_at=signed_at, now=now)
        auth = AuthenticatedRecoveryCommandV103(
            command=self.command,
            keys=keys,
            nonce_store=self.nonce_store,
            max_clock_skew=self.max_clock_skew,
        )
        result = auth.handle(envelope, now=now)
        return {**result, "rotation_version": VERSION}

    def sign(self, *, key_id: str, timestamp: int, nonce: str, payload: dict) -> dict:
        secret = self.keyring.signing_secret(key_id=key_id, now=timestamp)
        return sign_envelope(secret=secret, key_id=key_id, timestamp=timestamp, nonce=nonce, payload=payload)
