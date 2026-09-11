import dataclasses
import hashlib
import json
import re

VERSION = "8.9"


def _key(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclasses.dataclass(frozen=True)
class RecoveryLeaseV89:
    pr_number: int
    head_sha: str
    owner: str
    nonce: str
    acquired_at: int
    expires_at: int
    lease_key: str

    def payload(self) -> dict:
        return {
            "pr_number": self.pr_number,
            "head_sha": self.head_sha,
            "owner": self.owner,
            "nonce": self.nonce,
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
        }


class RecoveryLeaseGuardV89:
    """Exact-head concurrency guard for autonomous recovery sessions.

    The guard is deterministic and transport-agnostic. It models a single
    logical lease per PR/head so concurrent workers cannot both authorize the
    same recovery session. The caller supplies monotonic integer time and is
    responsible for persisting the returned lease with compare-and-swap
    semantics in its transport. This module never mutates GitHub or merges.
    """

    @staticmethod
    def _validate_identity(pr_number: int, head_sha: str, owner: str, nonce: str) -> None:
        if pr_number < 1:
            raise ValueError("invalid_pr_number")
        if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
            raise ValueError("invalid_head_sha")
        if not owner.strip():
            raise ValueError("empty_owner")
        if not nonce.strip():
            raise ValueError("empty_nonce")

    @classmethod
    def verify(cls, lease: RecoveryLeaseV89, *, pr_number: int, head_sha: str) -> dict:
        cls._validate_identity(lease.pr_number, lease.head_sha, lease.owner, lease.nonce)
        if lease.pr_number != pr_number or lease.head_sha != head_sha:
            raise RuntimeError("lease_binding_mismatch")
        if lease.acquired_at < 0 or lease.expires_at <= lease.acquired_at:
            raise RuntimeError("invalid_lease_window")
        if lease.lease_key != _key(lease.payload()):
            raise RuntimeError("lease_key_mismatch")
        return {
            "status": "VERIFIED",
            "version": VERSION,
            "lease_key": lease.lease_key,
            "owner": lease.owner,
            "expires_at": lease.expires_at,
        }

    @classmethod
    def acquire(
        cls,
        current: RecoveryLeaseV89 | None,
        *,
        pr_number: int,
        expected_head_sha: str,
        current_head_sha: str,
        owner: str,
        nonce: str,
        now: int,
        ttl: int = 120,
    ) -> RecoveryLeaseV89:
        cls._validate_identity(pr_number, expected_head_sha, owner, nonce)
        if expected_head_sha != current_head_sha:
            raise RuntimeError("stale_pr_head")
        if now < 0 or ttl < 1:
            raise ValueError("invalid_lease_time")

        if current is not None:
            cls.verify(current, pr_number=pr_number, head_sha=expected_head_sha)
            if now < current.expires_at:
                if current.owner == owner and current.nonce == nonce:
                    return current
                raise RuntimeError("recovery_lease_held")

        lease = RecoveryLeaseV89(
            pr_number=pr_number,
            head_sha=expected_head_sha,
            owner=owner.strip(),
            nonce=nonce.strip(),
            acquired_at=now,
            expires_at=now + ttl,
            lease_key="",
        )
        return dataclasses.replace(lease, lease_key=_key(lease.payload()))

    @classmethod
    def renew(
        cls,
        lease: RecoveryLeaseV89,
        *,
        owner: str,
        nonce: str,
        now: int,
        ttl: int = 120,
    ) -> RecoveryLeaseV89:
        cls.verify(lease, pr_number=lease.pr_number, head_sha=lease.head_sha)
        if owner != lease.owner or nonce != lease.nonce:
            raise RuntimeError("lease_owner_mismatch")
        if now < lease.acquired_at or now >= lease.expires_at:
            raise RuntimeError("lease_not_active")
        if ttl < 1:
            raise ValueError("invalid_lease_time")
        renewed = dataclasses.replace(lease, expires_at=now + ttl, lease_key="")
        return dataclasses.replace(renewed, lease_key=_key(renewed.payload()))

    @classmethod
    def release(
        cls,
        lease: RecoveryLeaseV89,
        *,
        owner: str,
        nonce: str,
        now: int,
    ) -> dict:
        cls.verify(lease, pr_number=lease.pr_number, head_sha=lease.head_sha)
        if owner != lease.owner or nonce != lease.nonce:
            raise RuntimeError("lease_owner_mismatch")
        if now < lease.acquired_at:
            raise RuntimeError("invalid_release_time")
        return {
            "status": "RELEASED",
            "version": VERSION,
            "lease_key": lease.lease_key,
            "owner": lease.owner,
            "expired": now >= lease.expires_at,
        }
