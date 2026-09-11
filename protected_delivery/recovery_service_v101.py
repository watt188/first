from pathlib import Path
import sqlite3

from protected_delivery.recovery_pending_v93 import SQLitePendingActionJournalV93
from protected_delivery.recovery_production_v100 import ProductionRecoveryRuntimeV100
from protected_delivery.recovery_state_sqlite_v91 import SQLiteRecoveryStateStoreV91

VERSION = "10.1"


class ProductionRecoveryServiceV101:
    """Production-facing durable bootstrap for the V10.0 recovery runtime.

    V10.1 removes the remaining assembly burden for the single-host production
    profile: it owns durable SQLite state/journal paths, validates the gateway
    contract, performs storage integrity preflight, and exposes one exact-head
    recovery entry point. The service preserves V10.0 fail-closed semantics.
    """

    def __init__(self, *, root, gateway, owner: str, nonce: str, lease_ttl: int = 120):
        if not owner.strip() or not nonce.strip():
            raise ValueError("invalid_service_identity")
        if lease_ttl < 1:
            raise ValueError("invalid_lease_ttl")
        if not callable(getattr(gateway, "execute_recovery", None)):
            raise TypeError("gateway_execute_recovery_missing")
        if not callable(getattr(gateway, "lookup_recovery", None)):
            raise TypeError("gateway_lookup_recovery_missing")

        self.root = Path(root)
        if self.root.exists() and not self.root.is_dir():
            raise RuntimeError("recovery_service_root_not_directory")
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "recovery_state.sqlite3"
        self.journal_path = self.root / "recovery_pending.sqlite3"
        if self.state_path == self.journal_path:
            raise RuntimeError("recovery_storage_path_collision")

        self.store = SQLiteRecoveryStateStoreV91(self.state_path)
        self.journal = SQLitePendingActionJournalV93(self.journal_path)
        self.gateway = gateway
        self.owner = owner.strip()
        self.nonce = nonce.strip()
        self.runtime = ProductionRecoveryRuntimeV100(
            store=self.store,
            journal=self.journal,
            gateway=gateway,
            owner=self.owner,
            nonce=self.nonce,
            lease_ttl=lease_ttl,
        )
        self.health()

    @staticmethod
    def _integrity(path: Path) -> None:
        conn = sqlite3.connect(str(path), timeout=10)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
        if row is None or str(row[0]).lower() != "ok":
            raise RuntimeError("recovery_storage_integrity_failed")

    def health(self) -> dict:
        if not self.state_path.exists() or not self.journal_path.exists():
            raise RuntimeError("recovery_storage_missing")
        self._integrity(self.state_path)
        self._integrity(self.journal_path)
        return {
            "status": "READY",
            "version": VERSION,
            "owner": self.owner,
            "state_backend": "sqlite",
            "journal_backend": "sqlite",
            "durability_scope": "single_host",
            "gateway_execute": True,
            "gateway_lookup": True,
        }

    def run(self, **request) -> dict:
        self.health()
        result = self.runtime.run(**request)
        if result.get("status") != "PRODUCTION_RECOVERY_CONVERGED":
            raise RuntimeError("production_service_non_converged_result")
        return {**result, "service_version": VERSION, "service_status": "READY"}
