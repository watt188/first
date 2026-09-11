import dataclasses
import hashlib
import json
import re
from typing import Protocol

from protected_delivery.recovery_evidence_v85 import RecoveryEvidenceEventV85, RecoveryEvidenceLedgerV85
from protected_delivery.recovery_lease_v89 import RecoveryLeaseGuardV89, RecoveryLeaseV89

VERSION = "9.0"


def _digest(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclasses.dataclass(frozen=True)
class RecoveryStateV90:
    pr_number: int
    head_sha: str
    generation: int
    lease: RecoveryLeaseV89 | None
    ledger: tuple[RecoveryEvidenceEventV85, ...]
    state_hash: str = ""

    def payload(self) -> dict:
        return {
            "pr_number": self.pr_number,
            "head_sha": self.head_sha,
            "generation": self.generation,
            "lease": dataclasses.asdict(self.lease) if self.lease else None,
            "ledger": [dataclasses.asdict(event) for event in self.ledger],
        }

    def seal(self) -> "RecoveryStateV90":
        return dataclasses.replace(self, state_hash=_digest(self.payload()))


class RecoveryStateStoreV90(Protocol):
    def read(self, *, pr_number: int, head_sha: str) -> RecoveryStateV90 | None: ...

    def compare_and_swap(
        self,
        *,
        expected_generation: int,
        expected_state_hash: str,
        replacement: RecoveryStateV90,
    ) -> RecoveryStateV90: ...


class InMemoryRecoveryStateStoreV90:
    """Reference CAS transport used to prove state semantics deterministically.

    Production transports should implement the same read + compare-and-swap
    contract using a durable backend. A stale generation/hash never overwrites
    newer recovery evidence or a newer lease.
    """

    def __init__(self):
        self._states: dict[tuple[int, str], RecoveryStateV90] = {}

    @staticmethod
    def _validate_binding(pr_number: int, head_sha: str) -> None:
        if pr_number < 1:
            raise ValueError("invalid_pr_number")
        if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
            raise ValueError("invalid_head_sha")

    @classmethod
    def verify_state(cls, state: RecoveryStateV90) -> dict:
        cls._validate_binding(state.pr_number, state.head_sha)
        if state.generation < 0:
            raise ValueError("invalid_generation")
        if state.state_hash != _digest(state.payload()):
            raise RuntimeError("state_hash_mismatch")
        RecoveryEvidenceLedgerV85.verify(
            state.ledger,
            expected_head_sha=state.head_sha,
            pr_number=state.pr_number,
        )
        if state.lease is not None:
            RecoveryLeaseGuardV89.verify(
                state.lease,
                pr_number=state.pr_number,
                head_sha=state.head_sha,
            )
        return {
            "status": "VERIFIED",
            "version": VERSION,
            "generation": state.generation,
            "state_hash": state.state_hash,
            "ledger_events": len(state.ledger),
            "has_lease": state.lease is not None,
        }

    def read(self, *, pr_number: int, head_sha: str) -> RecoveryStateV90 | None:
        self._validate_binding(pr_number, head_sha)
        state = self._states.get((pr_number, head_sha))
        if state is not None:
            self.verify_state(state)
        return state

    def initialize(self, *, pr_number: int, head_sha: str) -> RecoveryStateV90:
        self._validate_binding(pr_number, head_sha)
        key = (pr_number, head_sha)
        if key in self._states:
            raise RuntimeError("state_already_initialized")
        state = RecoveryStateV90(
            pr_number=pr_number,
            head_sha=head_sha,
            generation=0,
            lease=None,
            ledger=(),
        ).seal()
        self._states[key] = state
        return state

    def compare_and_swap(
        self,
        *,
        expected_generation: int,
        expected_state_hash: str,
        replacement: RecoveryStateV90,
    ) -> RecoveryStateV90:
        self.verify_state(replacement)
        key = (replacement.pr_number, replacement.head_sha)
        current = self._states.get(key)
        if current is None:
            raise RuntimeError("state_not_initialized")
        self.verify_state(current)
        if current.generation != expected_generation:
            raise RuntimeError("cas_generation_conflict")
        if current.state_hash != expected_state_hash:
            raise RuntimeError("cas_hash_conflict")
        if replacement.generation != current.generation + 1:
            raise RuntimeError("invalid_next_generation")
        self._states[key] = replacement
        return replacement


def next_state(
    current: RecoveryStateV90,
    *,
    lease: RecoveryLeaseV89 | None = None,
    ledger: tuple[RecoveryEvidenceEventV85, ...] | None = None,
) -> RecoveryStateV90:
    InMemoryRecoveryStateStoreV90.verify_state(current)
    replacement = RecoveryStateV90(
        pr_number=current.pr_number,
        head_sha=current.head_sha,
        generation=current.generation + 1,
        lease=current.lease if lease is None else lease,
        ledger=current.ledger if ledger is None else tuple(ledger),
    ).seal()
    InMemoryRecoveryStateStoreV90.verify_state(replacement)
    return replacement
