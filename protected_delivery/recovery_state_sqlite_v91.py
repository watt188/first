import dataclasses
import json
import sqlite3
from pathlib import Path

from protected_delivery.recovery_evidence_v85 import RecoveryEvidenceEventV85
from protected_delivery.recovery_lease_v89 import RecoveryLeaseV89
from protected_delivery.recovery_state_v90 import (
    InMemoryRecoveryStateStoreV90,
    RecoveryStateV90,
)

VERSION = "9.1"


def _encode_state(state: RecoveryStateV90) -> str:
    InMemoryRecoveryStateStoreV90.verify_state(state)
    return json.dumps(state.payload(), sort_keys=True, separators=(",", ":"))


def _decode_state(raw: str, state_hash: str) -> RecoveryStateV90:
    payload = json.loads(raw)
    lease_payload = payload.get("lease")
    lease = RecoveryLeaseV89(**lease_payload) if lease_payload else None
    ledger = tuple(RecoveryEvidenceEventV85(**event) for event in payload.get("ledger", []))
    state = RecoveryStateV90(
        pr_number=int(payload["pr_number"]),
        head_sha=str(payload["head_sha"]),
        generation=int(payload["generation"]),
        lease=lease,
        ledger=ledger,
        state_hash=state_hash,
    )
    InMemoryRecoveryStateStoreV90.verify_state(state)
    return state


class SQLiteRecoveryStateStoreV91:
    """Durable SQLite implementation of the V9.0 CAS state contract.

    Each compare-and-swap executes under BEGIN IMMEDIATE and updates only when
    both generation and state_hash still match. This prevents stale recovery
    workers from overwriting a newer lease/evidence state. SQLite provides a
    durable single-node transport; it is not a distributed consensus store.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def _initialize_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS recovery_state (
                    pr_number INTEGER NOT NULL,
                    head_sha TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    state_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (pr_number, head_sha)
                )
                """
            )

    def initialize(self, *, pr_number: int, head_sha: str) -> RecoveryStateV90:
        state = RecoveryStateV90(
            pr_number=pr_number,
            head_sha=head_sha,
            generation=0,
            lease=None,
            ledger=(),
        ).seal()
        InMemoryRecoveryStateStoreV90.verify_state(state)
        payload = _encode_state(state)
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO recovery_state(pr_number, head_sha, generation, state_hash, payload) VALUES (?, ?, ?, ?, ?)",
                    (state.pr_number, state.head_sha, state.generation, state.state_hash, payload),
                )
                conn.commit()
        except sqlite3.IntegrityError as exc:
            raise RuntimeError("state_already_initialized") from exc
        return state

    def read(self, *, pr_number: int, head_sha: str) -> RecoveryStateV90 | None:
        InMemoryRecoveryStateStoreV90._validate_binding(pr_number, head_sha)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload, state_hash FROM recovery_state WHERE pr_number=? AND head_sha=?",
                (pr_number, head_sha),
            ).fetchone()
        if row is None:
            return None
        return _decode_state(str(row[0]), str(row[1]))

    def compare_and_swap(
        self,
        *,
        expected_generation: int,
        expected_state_hash: str,
        replacement: RecoveryStateV90,
    ) -> RecoveryStateV90:
        InMemoryRecoveryStateStoreV90.verify_state(replacement)
        if replacement.generation != expected_generation + 1:
            raise RuntimeError("invalid_next_generation")
        payload = _encode_state(replacement)
        with self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT generation, state_hash FROM recovery_state WHERE pr_number=? AND head_sha=?",
                    (replacement.pr_number, replacement.head_sha),
                ).fetchone()
                if row is None:
                    raise RuntimeError("state_not_initialized")
                current_generation, current_hash = int(row[0]), str(row[1])
                if current_generation != expected_generation:
                    raise RuntimeError("cas_generation_conflict")
                if current_hash != expected_state_hash:
                    raise RuntimeError("cas_hash_conflict")
                changed = conn.execute(
                    """
                    UPDATE recovery_state
                    SET generation=?, state_hash=?, payload=?
                    WHERE pr_number=? AND head_sha=? AND generation=? AND state_hash=?
                    """,
                    (
                        replacement.generation,
                        replacement.state_hash,
                        payload,
                        replacement.pr_number,
                        replacement.head_sha,
                        expected_generation,
                        expected_state_hash,
                    ),
                ).rowcount
                if changed != 1:
                    raise RuntimeError("cas_write_conflict")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        persisted = self.read(pr_number=replacement.pr_number, head_sha=replacement.head_sha)
        if persisted != replacement:
            raise RuntimeError("persisted_state_mismatch")
        return persisted
