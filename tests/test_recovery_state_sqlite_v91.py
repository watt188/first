import sqlite3

import pytest

from protected_delivery.recovery_lease_v89 import RecoveryLeaseGuardV89
from protected_delivery.recovery_state_sqlite_v91 import SQLiteRecoveryStateStoreV91
from protected_delivery.recovery_state_v90 import next_state

HEAD = "e" * 40


def test_sqlite_state_persists_across_reopen(tmp_path):
    path = tmp_path / "recovery.db"
    first = SQLiteRecoveryStateStoreV91(path)
    state = first.initialize(pr_number=50, head_sha=HEAD)
    second = SQLiteRecoveryStateStoreV91(path)
    assert second.read(pr_number=50, head_sha=HEAD) == state


def test_sqlite_compare_and_swap_persists_next_generation(tmp_path):
    store = SQLiteRecoveryStateStoreV91(tmp_path / "recovery.db")
    current = store.initialize(pr_number=50, head_sha=HEAD)
    replacement = next_state(current)
    written = store.compare_and_swap(
        expected_generation=current.generation,
        expected_state_hash=current.state_hash,
        replacement=replacement,
    )
    assert written.generation == 1
    assert store.read(pr_number=50, head_sha=HEAD) == replacement


def test_two_store_instances_reject_stale_writer(tmp_path):
    path = tmp_path / "recovery.db"
    a = SQLiteRecoveryStateStoreV91(path)
    b = SQLiteRecoveryStateStoreV91(path)
    current_a = a.initialize(pr_number=50, head_sha=HEAD)
    current_b = b.read(pr_number=50, head_sha=HEAD)
    assert current_b == current_a

    a_next = next_state(current_a)
    a.compare_and_swap(
        expected_generation=0,
        expected_state_hash=current_a.state_hash,
        replacement=a_next,
    )
    b_next = next_state(current_b)
    with pytest.raises(RuntimeError, match="cas_generation_conflict"):
        b.compare_and_swap(
            expected_generation=0,
            expected_state_hash=current_b.state_hash,
            replacement=b_next,
        )


def test_sqlite_store_persists_verified_lease(tmp_path):
    store = SQLiteRecoveryStateStoreV91(tmp_path / "recovery.db")
    current = store.initialize(pr_number=50, head_sha=HEAD)
    lease = RecoveryLeaseGuardV89.acquire(
        None,
        pr_number=50,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        owner="worker-a",
        nonce="nonce-a",
        now=100,
        ttl=60,
    )
    replacement = next_state(current, lease=lease)
    store.compare_and_swap(
        expected_generation=0,
        expected_state_hash=current.state_hash,
        replacement=replacement,
    )
    assert store.read(pr_number=50, head_sha=HEAD).lease == lease


def test_database_payload_tamper_fails_closed(tmp_path):
    path = tmp_path / "recovery.db"
    store = SQLiteRecoveryStateStoreV91(path)
    store.initialize(pr_number=50, head_sha=HEAD)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE recovery_state SET state_hash=? WHERE pr_number=? AND head_sha=?",
            ("0" * 64, 50, HEAD),
        )
        conn.commit()
    with pytest.raises(RuntimeError, match="state_hash_mismatch"):
        store.read(pr_number=50, head_sha=HEAD)


def test_duplicate_initialization_is_rejected(tmp_path):
    store = SQLiteRecoveryStateStoreV91(tmp_path / "recovery.db")
    store.initialize(pr_number=50, head_sha=HEAD)
    with pytest.raises(RuntimeError, match="state_already_initialized"):
        store.initialize(pr_number=50, head_sha=HEAD)


def test_uninitialized_cas_is_rejected(tmp_path):
    store = SQLiteRecoveryStateStoreV91(tmp_path / "recovery.db")
    other = SQLiteRecoveryStateStoreV91(tmp_path / "other.db")
    current = other.initialize(pr_number=50, head_sha=HEAD)
    replacement = next_state(current)
    with pytest.raises(RuntimeError, match="state_not_initialized"):
        store.compare_and_swap(
            expected_generation=0,
            expected_state_hash=current.state_hash,
            replacement=replacement,
        )
