import dataclasses
import hashlib
import json
import re
import sqlite3
from pathlib import Path

VERSION = "9.3"
_STATUSES = {"PREPARED", "COMMITTED", "ABORTED"}


def _digest(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclasses.dataclass(frozen=True)
class PendingRecoveryActionV93:
    pr_number: int
    head_sha: str
    workflow: str
    job_id: int
    action: str
    attempt: int
    replay_key: str
    owner: str
    lease_key: str
    status: str
    receipt_id: str = ""
    action_hash: str = ""

    def payload(self) -> dict:
        return {
            "pr_number": self.pr_number,
            "head_sha": self.head_sha,
            "workflow": self.workflow,
            "job_id": self.job_id,
            "action": self.action,
            "attempt": self.attempt,
            "replay_key": self.replay_key,
            "owner": self.owner,
            "lease_key": self.lease_key,
            "status": self.status,
            "receipt_id": self.receipt_id,
        }

    def seal(self) -> "PendingRecoveryActionV93":
        return dataclasses.replace(self, action_hash=_digest(self.payload()))


class SQLitePendingActionJournalV93:
    """Write-ahead journal for recovery actions.

    PREPARED is persisted before the external mutation. A process restart can
    therefore detect an unresolved action and fail closed instead of blindly
    replaying it after a lease expires. Only one PREPARED action may exist per
    PR/exact head. COMMITTED/ABORTED transitions are hash-bound and durable.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def _initialize_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS recovery_pending_action (
                    replay_key TEXT PRIMARY KEY,
                    pr_number INTEGER NOT NULL,
                    head_sha TEXT NOT NULL,
                    status TEXT NOT NULL,
                    action_hash TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS one_prepared_per_head
                ON recovery_pending_action(pr_number, head_sha)
                WHERE status='PREPARED'
                """
            )

    @staticmethod
    def verify(action: PendingRecoveryActionV93) -> dict:
        if action.pr_number < 1:
            raise ValueError("invalid_pr_number")
        if not re.fullmatch(r"[0-9a-f]{40}", action.head_sha):
            raise ValueError("invalid_head_sha")
        if not action.workflow.strip() or action.job_id < 1 or not action.action.strip():
            raise ValueError("invalid_action_identity")
        if action.attempt < 0:
            raise ValueError("invalid_attempt")
        if not re.fullmatch(r"[0-9a-f]{64}", action.replay_key):
            raise ValueError("invalid_replay_key")
        if not action.owner.strip() or not re.fullmatch(r"[0-9a-f]{64}", action.lease_key):
            raise ValueError("invalid_lease_identity")
        if action.status not in _STATUSES:
            raise ValueError("invalid_pending_status")
        if action.status == "COMMITTED" and not action.receipt_id.strip():
            raise RuntimeError("committed_without_receipt")
        if action.action_hash != _digest(action.payload()):
            raise RuntimeError("pending_action_hash_mismatch")
        return {"status": "VERIFIED", "version": VERSION, "action_hash": action.action_hash}

    @staticmethod
    def _encode(action: PendingRecoveryActionV93) -> str:
        SQLitePendingActionJournalV93.verify(action)
        return json.dumps(action.payload(), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _decode(raw: str, action_hash: str) -> PendingRecoveryActionV93:
        payload = json.loads(raw)
        action = PendingRecoveryActionV93(**payload, action_hash=action_hash)
        SQLitePendingActionJournalV93.verify(action)
        return action

    def read(self, replay_key: str) -> PendingRecoveryActionV93 | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload, action_hash FROM recovery_pending_action WHERE replay_key=?",
                (replay_key,),
            ).fetchone()
        return None if row is None else self._decode(str(row[0]), str(row[1]))

    def prepared_for_head(self, *, pr_number: int, head_sha: str) -> PendingRecoveryActionV93 | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload, action_hash FROM recovery_pending_action WHERE pr_number=? AND head_sha=? AND status='PREPARED'",
                (pr_number, head_sha),
            ).fetchone()
        return None if row is None else self._decode(str(row[0]), str(row[1]))

    def prepare(
        self,
        *,
        pr_number: int,
        expected_head_sha: str,
        current_head_sha: str,
        workflow: str,
        job_id: int,
        action: str,
        attempt: int,
        replay_key: str,
        owner: str,
        lease_key: str,
    ) -> PendingRecoveryActionV93:
        if expected_head_sha != current_head_sha:
            raise RuntimeError("stale_pr_head")
        candidate = PendingRecoveryActionV93(
            pr_number=pr_number,
            head_sha=expected_head_sha,
            workflow=workflow.strip(),
            job_id=job_id,
            action=action.strip(),
            attempt=attempt,
            replay_key=replay_key,
            owner=owner.strip(),
            lease_key=lease_key,
            status="PREPARED",
        ).seal()
        self.verify(candidate)
        encoded = self._encode(candidate)
        with self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute(
                    "SELECT payload, action_hash FROM recovery_pending_action WHERE replay_key=?",
                    (replay_key,),
                ).fetchone()
                if existing is not None:
                    prior = self._decode(str(existing[0]), str(existing[1]))
                    if prior == candidate:
                        conn.commit()
                        return prior
                    raise RuntimeError("pending_replay_key_conflict")
                active = conn.execute(
                    "SELECT replay_key FROM recovery_pending_action WHERE pr_number=? AND head_sha=? AND status='PREPARED'",
                    (pr_number, expected_head_sha),
                ).fetchone()
                if active is not None:
                    raise RuntimeError("pending_action_already_exists")
                conn.execute(
                    "INSERT INTO recovery_pending_action(replay_key, pr_number, head_sha, status, action_hash, payload) VALUES (?, ?, ?, ?, ?, ?)",
                    (replay_key, pr_number, expected_head_sha, candidate.status, candidate.action_hash, encoded),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self.read(replay_key)

    def _transition(self, current: PendingRecoveryActionV93, *, status: str, receipt_id: str) -> PendingRecoveryActionV93:
        self.verify(current)
        if current.status != "PREPARED":
            raise RuntimeError("pending_action_not_prepared")
        if status not in {"COMMITTED", "ABORTED"}:
            raise ValueError("invalid_transition")
        updated = dataclasses.replace(current, status=status, receipt_id=receipt_id.strip(), action_hash="").seal()
        self.verify(updated)
        encoded = self._encode(updated)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                "UPDATE recovery_pending_action SET status=?, action_hash=?, payload=? WHERE replay_key=? AND status='PREPARED' AND action_hash=?",
                (updated.status, updated.action_hash, encoded, current.replay_key, current.action_hash),
            ).rowcount
            if changed != 1:
                conn.rollback()
                raise RuntimeError("pending_transition_conflict")
            conn.commit()
        return self.read(current.replay_key)

    def commit(self, current: PendingRecoveryActionV93, *, receipt_id: str) -> PendingRecoveryActionV93:
        if not receipt_id.strip():
            raise ValueError("empty_receipt_id")
        return self._transition(current, status="COMMITTED", receipt_id=receipt_id)

    def abort(self, current: PendingRecoveryActionV93, *, reason: str) -> PendingRecoveryActionV93:
        if not reason.strip():
            raise ValueError("empty_abort_reason")
        return self._transition(current, status="ABORTED", receipt_id=reason)
