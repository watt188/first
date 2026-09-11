import tempfile
from pathlib import Path

import pytest

from protected_delivery.recovery_evidence_v85 import RecoveryEvidenceLedgerV85
from protected_delivery.recovery_lease_v89 import RecoveryLeaseGuardV89
from protected_delivery.recovery_pending_v93 import SQLitePendingActionJournalV93
from protected_delivery.recovery_replay_v88 import replay_key
from protected_delivery.recovery_state_v90 import InMemoryRecoveryStateStoreV90, RecoveryStateV90
from protected_delivery.recovery_transaction_reconcile_v97 import RecoveryTransactionReconcilerV97

HEAD = "a" * 40
PR = 40
WORKFLOW = "ci"
JOB = 77
CATEGORY = "code_or_test_defect"
ACTION = "autonomous_rework"
ATTEMPT = 0
OWNER = "worker-a"
NONCE = "nonce-a"


def _key():
    return replay_key(
        pr_number=PR,
        head_sha=HEAD,
        workflow=WORKFLOW,
        job_id=JOB,
        action=ACTION,
        attempt=ATTEMPT,
    )


def _journal(path: Path, *, status="COMMITTED"):
    journal = SQLitePendingActionJournalV93(path)
    lease = RecoveryLeaseGuardV89.acquire(
        None,
        pr_number=PR,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        owner=OWNER,
        nonce=NONCE,
        now=10,
        ttl=20,
    )
    prepared = journal.prepare(
        pr_number=PR,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow=WORKFLOW,
        job_id=JOB,
        action=ACTION,
        attempt=ATTEMPT,
        replay_key=_key(),
        owner=OWNER,
        lease_key=lease.lease_key,
    )
    if status == "COMMITTED":
        journal.commit(prepared, receipt_id="receipt-1")
    elif status == "ABORTED":
        journal.abort(prepared, reason="aborted")
    return journal, lease


def _store_with_lease(lease):
    store = InMemoryRecoveryStateStoreV90()
    base = store.initialize(pr_number=PR, head_sha=HEAD)
    leased = RecoveryStateV90(
        pr_number=PR,
        head_sha=HEAD,
        generation=base.generation + 1,
        lease=lease,
        ledger=base.ledger,
    ).seal()
    store.compare_and_swap(
        expected_generation=base.generation,
        expected_state_hash=base.state_hash,
        replacement=leased,
    )
    return store


def _reconciler(store, journal, *, owner=OWNER, nonce=NONCE):
    return RecoveryTransactionReconcilerV97(
        store=store, journal=journal, owner=owner, nonce=nonce
    )


def _call(r, *, now=11):
    return r.reconcile(
        pr_number=PR,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow=WORKFLOW,
        job_id=JOB,
        category=CATEGORY,
        action=ACTION,
        attempt=ATTEMPT,
        now=now,
    )


def test_committed_journal_repairs_missing_evidence_and_clears_owned_lease():
    with tempfile.TemporaryDirectory() as td:
        journal, lease = _journal(Path(td) / "journal.db")
        store = _store_with_lease(lease)
        result = _call(_reconciler(store, journal), now=12)
        assert result["status"] == "RECOVERY_TRANSACTION_RECONCILED"
        assert result["evidence_appended"] is True
        assert result["lease_cleared"] is True
        state = store.read(pr_number=PR, head_sha=HEAD)
        assert state.lease is None
        assert len(state.ledger) == 1
        assert state.ledger[0].action == ACTION


def test_existing_evidence_is_not_appended_twice_and_stale_owned_lease_is_cleared():
    with tempfile.TemporaryDirectory() as td:
        journal, lease = _journal(Path(td) / "journal.db")
        store = _store_with_lease(lease)
        state = store.read(pr_number=PR, head_sha=HEAD)
        ledger = RecoveryEvidenceLedgerV85.append(
            state.ledger,
            pr_number=PR,
            expected_head_sha=HEAD,
            current_head_sha=HEAD,
            workflow=WORKFLOW,
            job_id=JOB,
            category=CATEGORY,
            action=ACTION,
            attempt=ATTEMPT,
            accepted=True,
        )
        evidenced = RecoveryStateV90(
            pr_number=PR,
            head_sha=HEAD,
            generation=state.generation + 1,
            lease=lease,
            ledger=ledger,
        ).seal()
        store.compare_and_swap(
            expected_generation=state.generation,
            expected_state_hash=state.state_hash,
            replacement=evidenced,
        )
        result = _call(_reconciler(store, journal), now=12)
        assert result["evidence_appended"] is False
        assert result["lease_cleared"] is True
        final = store.read(pr_number=PR, head_sha=HEAD)
        assert len(final.ledger) == 1
        assert final.lease is None


def test_active_foreign_lease_fails_closed_after_evidence_repair():
    with tempfile.TemporaryDirectory() as td:
        journal, _ = _journal(Path(td) / "journal.db")
        foreign = RecoveryLeaseGuardV89.acquire(
            None,
            pr_number=PR,
            expected_head_sha=HEAD,
            current_head_sha=HEAD,
            owner="worker-b",
            nonce="nonce-b",
            now=10,
            ttl=50,
        )
        store = _store_with_lease(foreign)
        with pytest.raises(RuntimeError, match="recovery_lease_held"):
            _call(_reconciler(store, journal), now=12)
        state = store.read(pr_number=PR, head_sha=HEAD)
        assert len(state.ledger) == 1
        assert state.lease is not None


def test_expired_foreign_lease_can_be_cleared_after_committed_proof():
    with tempfile.TemporaryDirectory() as td:
        journal, _ = _journal(Path(td) / "journal.db")
        foreign = RecoveryLeaseGuardV89.acquire(
            None,
            pr_number=PR,
            expected_head_sha=HEAD,
            current_head_sha=HEAD,
            owner="worker-b",
            nonce="nonce-b",
            now=10,
            ttl=2,
        )
        store = _store_with_lease(foreign)
        result = _call(_reconciler(store, journal), now=20)
        assert result["lease_cleared"] is True
        assert store.read(pr_number=PR, head_sha=HEAD).lease is None


def test_prepared_and_stale_head_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        journal, lease = _journal(Path(td) / "journal.db", status="PREPARED")
        store = _store_with_lease(lease)
        r = _reconciler(store, journal)
        with pytest.raises(RuntimeError, match="pending_recovery_unresolved"):
            _call(r)
        with pytest.raises(RuntimeError, match="stale_pr_head"):
            r.reconcile(
                pr_number=PR,
                expected_head_sha=HEAD,
                current_head_sha="b" * 40,
                workflow=WORKFLOW,
                job_id=JOB,
                category=CATEGORY,
                action=ACTION,
                attempt=ATTEMPT,
                now=12,
            )
