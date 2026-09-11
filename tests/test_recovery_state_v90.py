import dataclasses

import pytest

from protected_delivery.recovery_lease_v89 import RecoveryLeaseGuardV89
from protected_delivery.recovery_state_v90 import (
    InMemoryRecoveryStateStoreV90,
    next_state,
)

HEAD = "c" * 40


def test_initialize_read_and_verify():
    store = InMemoryRecoveryStateStoreV90()
    state = store.initialize(pr_number=40, head_sha=HEAD)
    assert state.generation == 0
    assert store.read(pr_number=40, head_sha=HEAD) == state
    assert store.verify_state(state)["status"] == "VERIFIED"


def test_compare_and_swap_advances_generation():
    store = InMemoryRecoveryStateStoreV90()
    current = store.initialize(pr_number=40, head_sha=HEAD)
    replacement = next_state(current)
    written = store.compare_and_swap(
        expected_generation=current.generation,
        expected_state_hash=current.state_hash,
        replacement=replacement,
    )
    assert written.generation == 1
    assert store.read(pr_number=40, head_sha=HEAD) == written


def test_stale_generation_cannot_overwrite_newer_state():
    store = InMemoryRecoveryStateStoreV90()
    current = store.initialize(pr_number=40, head_sha=HEAD)
    first = next_state(current)
    store.compare_and_swap(
        expected_generation=0,
        expected_state_hash=current.state_hash,
        replacement=first,
    )
    stale = next_state(current)
    with pytest.raises(RuntimeError, match="cas_generation_conflict"):
        store.compare_and_swap(
            expected_generation=0,
            expected_state_hash=current.state_hash,
            replacement=stale,
        )


def test_hash_conflict_fails_closed():
    store = InMemoryRecoveryStateStoreV90()
    current = store.initialize(pr_number=40, head_sha=HEAD)
    replacement = next_state(current)
    with pytest.raises(RuntimeError, match="cas_hash_conflict"):
        store.compare_and_swap(
            expected_generation=0,
            expected_state_hash="0" * 64,
            replacement=replacement,
        )


def test_invalid_generation_jump_is_rejected():
    store = InMemoryRecoveryStateStoreV90()
    current = store.initialize(pr_number=40, head_sha=HEAD)
    replacement = dataclasses.replace(current, generation=2, state_hash="").seal()
    with pytest.raises(RuntimeError, match="invalid_next_generation"):
        store.compare_and_swap(
            expected_generation=0,
            expected_state_hash=current.state_hash,
            replacement=replacement,
        )


def test_state_hash_tamper_is_rejected():
    store = InMemoryRecoveryStateStoreV90()
    current = store.initialize(pr_number=40, head_sha=HEAD)
    tampered = dataclasses.replace(current, state_hash="f" * 64)
    with pytest.raises(RuntimeError, match="state_hash_mismatch"):
        store.verify_state(tampered)


def test_lease_is_verified_inside_state():
    store = InMemoryRecoveryStateStoreV90()
    current = store.initialize(pr_number=40, head_sha=HEAD)
    lease = RecoveryLeaseGuardV89.acquire(
        None,
        pr_number=40,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        owner="worker-a",
        nonce="nonce-1",
        now=10,
        ttl=30,
    )
    replacement = next_state(current, lease=lease)
    store.compare_and_swap(
        expected_generation=0,
        expected_state_hash=current.state_hash,
        replacement=replacement,
    )
    assert store.read(pr_number=40, head_sha=HEAD).lease == lease


def test_cross_head_state_is_not_visible():
    store = InMemoryRecoveryStateStoreV90()
    store.initialize(pr_number=40, head_sha=HEAD)
    assert store.read(pr_number=40, head_sha="d" * 40) is None
