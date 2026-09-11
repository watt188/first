from pathlib import Path
import sqlite3

from protected_delivery.recovery_auth_rotation_v104 import (
    RotatingAuthenticatedRecoveryCommandV104,
    SQLiteAuthenticationKeyringV104,
)
from protected_delivery.recovery_auth_v103 import SQLiteReplayNonceStoreV103
from protected_delivery.recovery_command_v102 import ProductionRecoveryCommandV102
from protected_delivery.recovery_service_v101 import ProductionRecoveryServiceV101

VERSION = "10.5"


class SecureProductionRecoveryServiceV105:
    """Single-host secure production assembly for the recovery control plane.

    V10.5 composes the durable V10.1 runtime, strict V10.2 command surface,
    V10.3 replay guard, and V10.4 rotating authentication policy behind one
    production-facing object. HMAC secrets remain caller-supplied in memory;
    only lifecycle metadata and nonces are persisted.
    """

    def __init__(
        self,
        *,
        root,
        gateway,
        owner: str,
        nonce: str,
        secrets: dict[str, bytes],
        lease_ttl: int = 120,
        max_clock_skew: int = 300,
    ):
        self.root = Path(root)
        if self.root.exists() and not self.root.is_dir():
            raise RuntimeError("secure_recovery_root_not_directory")
        self.root.mkdir(parents=True, exist_ok=True)

        self.runtime_root = self.root / "runtime"
        self.keyring_path = self.root / "authentication_keys.sqlite3"
        self.nonce_path = self.root / "authentication_nonces.sqlite3"
        if len({str(self.runtime_root), str(self.keyring_path), str(self.nonce_path)}) != 3:
            raise RuntimeError("secure_recovery_storage_collision")

        self.service = ProductionRecoveryServiceV101(
            root=self.runtime_root,
            gateway=gateway,
            owner=owner,
            nonce=nonce,
            lease_ttl=lease_ttl,
        )
        self.command = ProductionRecoveryCommandV102(service=self.service)
        self.keyring = SQLiteAuthenticationKeyringV104(self.keyring_path, secrets=secrets)
        self.nonce_store = SQLiteReplayNonceStoreV103(self.nonce_path)
        self.auth = RotatingAuthenticatedRecoveryCommandV104(
            command=self.command,
            keyring=self.keyring,
            nonce_store=self.nonce_store,
            max_clock_skew=max_clock_skew,
        )
        self._integrity(self.keyring_path)
        self._integrity(self.nonce_path)

    @staticmethod
    def _integrity(path: Path) -> None:
        conn = sqlite3.connect(str(path), timeout=10)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
        if row is None or str(row[0]).lower() != "ok":
            raise RuntimeError("secure_recovery_storage_integrity_failed")

    def activate_key(self, *, key_id: str, not_before: int) -> None:
        self.keyring.activate(key_id=key_id, not_before=not_before)

    def retire_key(self, *, key_id: str, retired_at: int) -> None:
        self.keyring.retire(key_id=key_id, retired_at=retired_at)

    def revoke_key(self, *, key_id: str, revoked_at: int) -> None:
        self.keyring.revoke(key_id=key_id, revoked_at=revoked_at)

    def sign(self, *, key_id: str, timestamp: int, nonce: str, payload: dict) -> dict:
        return self.auth.sign(key_id=key_id, timestamp=timestamp, nonce=nonce, payload=payload)

    def handle(self, envelope: dict, *, now: int) -> dict:
        result = self.auth.handle(envelope, now=now)
        return {**result, "secure_service_version": VERSION}

    def health(self, *, now: int) -> dict:
        service = self.service.health()
        self._integrity(self.keyring_path)
        self._integrity(self.nonce_path)
        return {
            "status": "READY",
            "version": VERSION,
            "runtime": service,
            "authentication": self.keyring.status(now=now),
            "authentication_mode": "hmac_sha256_rotating",
            "replay_backend": "sqlite",
            "durability_scope": "single_host",
        }
