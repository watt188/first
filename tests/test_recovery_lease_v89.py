import dataclasses

import pytest

from protected_delivery.recovery_lease_v89 import RecoveryLeaseGuardV89

HEAD = "a" * 40


def test_acquire_and_verify():
    lease = RecoveryLeaseGuardV89.acquire(
        None,
        pr_number=33,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        owner="worker-a",
        nonce="n1",
        now=100,
        ttl=30,
    )
    verified = RecoveryLeaseGuardV89.verify(lease, pr_number=33, head_sha=HEAD)
    assert verified["status"] == "VERIFIED"
    assert lease.expires_at == 130


def test_same_owner_same_nonce_is_idempotent():
    first = RecoveryLeaseGuardV89.acquire(
        None,
        pr_number=33,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        owner="worker-a",
        nonce="n1",
        now=100,
    )
    second = RecoveryLeaseGuardV89.acquire(
        first,
        pr_number=33,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        owner="worker-a",
        nonce="n1",
        now=101,
    )
    assert second == first


def test_concurrent_owner_is_rejected_while_active():
    current = RecoveryLeaseGuardV89.acquire(
        None,
        pr_number=33,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        owner="worker-a",
        nonce="n1",
        now=100,
        ttl=30,
    )
    with pytest.raises(RuntimeError, match="recovery_lease_held"):
        RecoveryLeaseGuardV89.acquire(
            current,
            pr_number=33,
            expected_head_sha=HEAD,
            current_head_sha=HEAD,
            owner="worker-b",
            nonce="n2",
            now=120,
        )


def test_expired_lease_can_be_reacquired():
    current = RecoveryLeaseGuardV89.acquire(
        None,
        pr_number=33,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        owner="worker-a",
        nonce="n1",
        now=100,
        ttl=10,
    )
    replacement = RecoveryLeaseGuardV89.acquire(
        current,
        pr_number=33,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        owner="worker-b",
        nonce="n2",
        now=110,
        ttl=10,
    )
    assert replacement.owner == "worker-b"
    assert replacement.lease_key != current.lease_key


def test_stale_head_fails_closed():
    with pytest.raises(RuntimeError, match="stale_pr_head"):
        RecoveryLeaseGuardV89.acquire(
            None,
            pr_number=33,
            expected_head_sha=HEAD,
            current_head_sha="b" * 40,
            owner="worker-a",
            nonce="n1",
            now=100,
        )


def test_tampered_lease_is_rejected():
    lease = RecoveryLeaseGuardV89.acquire(
        None,
        pr_number=33,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        owner="worker-a",
        nonce="n1",
        now=100,
    )
    tampered = dataclasses.replace(lease, expires_at=999)
    with pytest.raises(RuntimeError, match="lease_key_mismatch"):
        RecoveryLeaseGuardV89.verify(tampered, pr_number=33, head_sha=HEAD)


def test_renew_requires_owner_and_active_lease():
    lease = RecoveryLeaseGuardV89.acquire(
        None,
        pr_number=33,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        owner="worker-a",
        nonce="n1",
        now=100,
        ttl=30,
    )
    with pytest.raises(RuntimeError, match="lease_owner_mismatch"):
        RecoveryLeaseGuardV89.renew(lease, owner="worker-b", nonce="n1", now=110)
    with pytest.raises(RuntimeError, match="lease_not_active"):
        RecoveryLeaseGuardV89.renew(lease, owner="worker-a", nonce="n1", now=130)


def test_release_requires_owner():
    lease = RecoveryLeaseGuardV89.acquire(
        None,
        pr_number=33,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        owner="worker-a",
        nonce="n1",
        now=100,
    )
    with pytest.raises(RuntimeError, match="lease_owner_mismatch"):
        RecoveryLeaseGuardV89.release(lease, owner="worker-b", nonce="n1", now=110)
    result = RecoveryLeaseGuardV89.release(lease, owner="worker-a", nonce="n1", now=110)
    assert result["status"] == "RELEASED"
