import hashlib
import json
import sqlite3
import time
from pathlib import Path

from protected_delivery.recovery_control_plane_v1010 import ProductionRecoveryControlPlaneV1010

VERSION = "11.0"


class SQLiteCanaryRecoveryGatewayV110:
    """Durable, non-destructive canary gateway for production-path validation.

    It exercises the exact downstream idempotency/lookup contract without
    mutating repository state. This proves the control-plane path against live
    GitHub head data, not provider-independent physical exactly-once behavior.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS canary_receipt ("
                "idempotency_key TEXT PRIMARY KEY, receipt_id TEXT NOT NULL, payload TEXT NOT NULL)"
            )

    def execute_recovery(self, pr_number, expected_head_sha, action, job_id, idempotency_key):
        payload = json.dumps(
            {
                "pr_number": pr_number,
                "expected_head_sha": expected_head_sha,
                "action": action,
                "job_id": job_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        receipt_id = "v110-" + hashlib.sha256((idempotency_key + "\n" + payload).encode()).hexdigest()
        with sqlite3.connect(self.path, timeout=10, isolation_level=None) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT receipt_id, payload FROM canary_receipt WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO canary_receipt(idempotency_key, receipt_id, payload) VALUES (?, ?, ?)",
                        (idempotency_key, receipt_id, payload),
                    )
                elif row[1] != payload:
                    raise RuntimeError("canary_idempotency_identity_conflict")
                else:
                    receipt_id = row[0]
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {"accepted": True, "receipt_id": receipt_id}

    def lookup_recovery(self, idempotency_key):
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                "SELECT receipt_id FROM canary_receipt WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
        if row is None:
            return None
        return {"accepted": True, "receipt_id": row[0]}


def run_live_validation(*, root, repository_full_name, pr_number, expected_head_sha, github_token=None, now=None):
    now = int(time.time()) if now is None else int(now)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    gateway = SQLiteCanaryRecoveryGatewayV110(root / "v110_canary.sqlite3")
    plane = ProductionRecoveryControlPlaneV1010(
        root=root / "control-plane",
        gateway=gateway,
        owner="v110-live-validator",
        nonce="v110-runtime",
        secrets={"v110": b"v11-live-validation-secret-material!!"},
        repository_full_name=repository_full_name,
        gateway_capabilities=lambda: {
            "provider": "v110-sqlite-canary",
            "scope": "single-host-validation",
            "durable_idempotency": True,
            "lookup_by_idempotency_key": True,
        },
        github_token=github_token,
    )
    plane.activate_key(key_id="v110", not_before=now)
    payload = {
        "operation": "recover",
        "pr_number": int(pr_number),
        "expected_head_sha": expected_head_sha,
        "current_head_sha": "0" * 40,
        "workflow": "v110-live-validation",
        "job_id": 110,
        "conclusion": "failure",
        "log_excerpt": "non-destructive live validation canary",
        "now": now,
        "max_attempts": 2,
    }
    envelope = plane.sign(
        key_id="v110",
        timestamp=now,
        nonce="v110-live-canary-0001",
        payload=payload,
    )
    first = plane.handle(envelope, now=now)
    replay_rejected = False
    try:
        plane.handle(envelope, now=now)
    except RuntimeError as exc:
        replay_rejected = "authenticated_command_replay" in str(exc)
    if not replay_rejected:
        raise RuntimeError("v110_replay_validation_failed")
    nested = first["result"]
    recovery = nested["result"]
    if recovery.get("status") != "PRODUCTION_RECOVERY_CONVERGED":
        raise RuntimeError("v110_recovery_not_converged")
    return {
        "version": VERSION,
        "status": "LIVE_PRODUCTION_VALIDATION_PASSED",
        "repository": repository_full_name,
        "pr_number": int(pr_number),
        "authoritative_head_sha": nested["authoritative_head_sha"],
        "gateway_provider": nested["gateway_provider"],
        "gateway_capabilities_attested": nested["gateway_capabilities_attested"],
        "replay_rejected": replay_rejected,
        "durability_scope": "single_host_validation",
        "external_repository_mutation": False,
        "physical_exactly_once_claimed": False,
    }
