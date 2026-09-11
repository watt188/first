import pytest

from protected_delivery.recovery_evidence_v85 import RecoveryEvidenceLedgerV85
from protected_delivery.recovery_lease_v89 import RecoveryLeaseGuardV89
from protected_delivery.recovery_pending_v93 import SQLitePendingActionJournalV93
from protected_delivery.recovery_replay_v88 import replay_key
from protected_delivery.recovery_runtime_audit_v99 import RecoveryRuntimeAuditV99
from protected_delivery.recovery_state_v90 import InMemoryRecoveryStateStoreV90, RecoveryStateV90, next_state

HEAD = "b" * 40


def _audit(tmp_path, *, store=None, owner="worker-a", nonce="nonce-a"):
    return RecoveryRuntimeAuditV99(
        store=store or InMemoryRecoveryStateStoreV90(),
        journal=SQLitePendingActionJournalV93(tmp_path / "pending.sqlite"),
        owner=owner,
        nonce=nonce,
    )


def _kwargs(**overrides):
    data = dict(
        pr_number=9,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow="v61-real-provider",
        job_id=101,
        action="rerun_failed_job",
        attempt=0,
        now=20,
    )
    data.update(overrides)
    return data


def _seed_state(auditor, *, owner="worker-a", nonce="nonce-a", now=10, with_lease=True):
    state = auditor.store.initialize(pr_number=9, head_sha=HEAD)
    lease = None
    if with_lease:
        lease = RecoveryLeaseGuardV89.acquire(
            None,
            pr_number=9,
            expected_head_sha=HEAD,
            current_head_sha=HEAD,
            owner=owner,
            nonce=nonce,
            now=now,
            ttl=120,
        )
        state = auditor.store.compare_and_swap(
            expected_generation=state.generation,
            expected_state_hash=state.state_hash,
            replacement=next_state(state, lease=lease),
        )
    return state, lease


def _prepare(auditor, *, owner="worker-a", lease_key=None):
    if lease_key is None:
        _, lease = _seed_state(auditor, owner=owner)
        lease_key = lease.lease_key
    key = replay_key(
        pr_number=9,
        head_sha=HEAD,
        workflow="v61-real-provider",
        job_id=101,
        action="rerun_failed_job",
        attempt=0,
    )
    pending = auditor.journal.prepare(
        pr_number=9,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow="v61-real-provider",
        job_id=101,
        action="rerun_failed_job",
        attempt=0,
        replay_key=key,
        owner=owner,
        lease_key=lease_key,
    )
    return key, pending


def test_empty_runtime_is_healthy_idle(tmp_path):
    auditor = _audit(tmp_path)
    result = auditor.audit(**_kwargs())
    assert result["status"] == "HEALTHY_IDLE"
    assert result["needs_reconciliation"] is False


def test_prepared_action_needs_reconciliation(tmp_path):
    auditor = _audit(tmp_path)
    _prepare(auditor)
    result = auditor.audit(**_kwargs())
    assert result["status"] == "PREPARED_NEEDS_RECONCILIATION"
    assert result["needs_reconciliation"] is True
    assert result["lease_status"] == "OWNED_ACTIVE"


def test_active_foreign_lease_blocks_audit_recovery(tmp_path):
    store = InMemoryRecoveryStateStoreV90()
    auditor = _audit(tmp_path, store=store, owner="worker-b", nonce="nonce-b")
    state, lease = _seed_state(auditor, owner="worker-a", nonce="nonce-a")
    key = replay_key(
        pr_number=9, head_sha=HEAD, workflow="v61-real-provider",
        job_id=101, action="rerun_failed_job", attempt=0,
    )
    auditor.journal.prepare(
        pr_number=9, expected_head_sha=HEAD, current_head_sha=HEAD,
        workflow="v61-real-provider", job_id=101, action="rerun_failed_job",
        attempt=0, replay_key=key, owner="worker-a", lease_key=lease.lease_key,
    )
    result = auditor.audit(**_kwargs(now=20))
    assert result["status"] == "BLOCKED_ACTIVE_FOREIGN_LEASE"
    assert result["needs_reconciliation"] is False


def test_committed_without_evidence_needs_reconciliation(tmp_path):
    auditor = _audit(tmp_path)
    _, pending = _prepare(auditor)
    auditor.journal.commit(pending, receipt_id="receipt-1")
    result = auditor.audit(**_kwargs())
    assert result["status"] == "COMMITTED_NEEDS_RECONCILIATION"
    assert result["receipt_id"] == "receipt-1"


def test_committed_with_evidence_and_no_lease_is_converged(tmp_path):
    auditor = _audit(tmp_path)
    state, lease = _seed_state(auditor)
    key, pending = _prepare(auditor, lease_key=lease.lease_key)
    auditor.journal.commit(pending, receipt_id="receipt-2")
    ledger = RecoveryEvidenceLedgerV85.append(
        state.ledger,
        pr_number=9,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow="v61-real-provider",
        job_id=101,
        category="provider_transient",
        action="rerun_failed_job",
        attempt=0,
        accepted=True,
    )
    replacement = RecoveryStateV90(
        pr_number=state.pr_number,
        head_sha=state.head_sha,
        generation=state.generation + 1,
        lease=None,
        ledger=ledger,
    ).seal()
    auditor.store.compare_and_swap(
        expected_generation=state.generation,
        expected_state_hash=state.state_hash,
        replacement=replacement,
    )
    result = auditor.audit(**_kwargs())
    assert result["status"] == "CONVERGED"
    assert result["needs_reconciliation"] is False
    assert result["receipt_id"] == "receipt-2"
    assert result["lease_status"] == "NONE"


def test_aborted_is_terminal(tmp_path):
    auditor = _audit(tmp_path)
    _, pending = _prepare(auditor)
    auditor.journal.abort(pending, reason="provider-rejected")
    result = auditor.audit(**_kwargs())
    assert result["status"] == "TERMINAL_ABORTED"
    assert result["needs_reconciliation"] is False


def test_stale_head_fails_closed(tmp_path):
    auditor = _audit(tmp_path)
    with pytest.raises(RuntimeError, match="stale_pr_head"):
        auditor.audit(**_kwargs(current_head_sha="c" * 40))


def test_expired_lease_without_journal_requests_cleanup(tmp_path):
    auditor = _audit(tmp_path)
    _seed_state(auditor, now=1)
    result = auditor.audit(**_kwargs(now=500))
    assert result["status"] == "STALE_LEASE_NEEDS_RECONCILIATION"
    assert result["needs_reconciliation"] is True


def test_legacy_evidence_without_journal_is_read_only(tmp_path):
    auditor = _audit(tmp_path)
    state, _ = _seed_state(auditor, with_lease=False)
    ledger = RecoveryEvidenceLedgerV85.append(
        state.ledger,
        pr_number=9,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow="v61-real-provider",
        job_id=101,
        category="provider_transient",
        action="rerun_failed_job",
        attempt=0,
        accepted=True,
    )
    replacement = RecoveryStateV90(
        pr_number=state.pr_number,
        head_sha=state.head_sha,
        generation=state.generation + 1,
        lease=None,
        ledger=ledger,
    ).seal()
    auditor.store.compare_and_swap(
        expected_generation=state.generation,
        expected_state_hash=state.state_hash,
        replacement=replacement,
    )
    result = auditor.audit(**_kwargs())
    assert result["status"] == "LEGACY_EVIDENCE_ONLY"
    assert result["needs_reconciliation"] is False
