import dataclasses
import pytest

from protected_delivery.recovery_evidence_v85 import RecoveryEvidenceLedgerV85
from protected_delivery.recovery_reconcile_v86 import (
    LiveFailedJobV86,
    RecoveryLiveStateV86,
    RecoveryStateReconcilerV86,
)

HEAD = "a" * 40


def _ledger(job_id=101, workflow="v61-real-provider", action="rerun_failed_job", attempt=0):
    return RecoveryEvidenceLedgerV85.append(
        (),
        pr_number=7,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow=workflow,
        job_id=job_id,
        category="provider_transient",
        action=action,
        attempt=attempt,
        accepted=True,
    )


def _live(*jobs, current=HEAD):
    return RecoveryLiveStateV86(
        pr_number=7,
        expected_head_sha=HEAD,
        current_head_sha=current,
        failed_jobs=tuple(jobs),
    )


def test_reconciles_matching_exact_head_failure():
    events = _ledger()
    result = RecoveryStateReconcilerV86.reconcile(
        events,
        _live(LiveFailedJobV86("v61-real-provider", 101, "failure")),
    )
    assert result["status"] == "RECONCILED"
    assert result["retry_attempts"] == {101: 0}


def test_rejects_stale_head():
    with pytest.raises(RuntimeError, match="stale_pr_head"):
        RecoveryStateReconcilerV86.reconcile(
            _ledger(),
            _live(LiveFailedJobV86("v61-real-provider", 101, "failure"), current="b" * 40),
        )


def test_rejects_job_that_is_no_longer_failed():
    with pytest.raises(RuntimeError, match="ledger_job_no_longer_failed"):
        RecoveryStateReconcilerV86.reconcile(_ledger(), _live())


def test_rejects_workflow_identity_change():
    with pytest.raises(RuntimeError, match="workflow_identity_mismatch"):
        RecoveryStateReconcilerV86.reconcile(
            _ledger(),
            _live(LiveFailedJobV86("ci", 101, "failure")),
        )


def test_rejects_new_untracked_failure_after_ledger_started():
    with pytest.raises(RuntimeError, match="new_untracked_failure"):
        RecoveryStateReconcilerV86.reconcile(
            _ledger(),
            _live(
                LiveFailedJobV86("v61-real-provider", 101, "failure"),
                LiveFailedJobV86("ci", 202, "failure"),
            ),
        )


def test_allows_empty_ledger_as_initial_snapshot():
    result = RecoveryStateReconcilerV86.reconcile(
        (),
        _live(LiveFailedJobV86("ci", 202, "failure")),
    )
    assert result["ledger_event_count"] == 0
    assert result["live_failure_count"] == 1


def test_rejects_tampered_ledger_before_reconciling():
    events = _ledger()
    tampered = (dataclasses.replace(events[0], attempt=99),)
    with pytest.raises(RuntimeError, match="ledger_event_hash_mismatch"):
        RecoveryStateReconcilerV86.reconcile(
            tampered,
            _live(LiveFailedJobV86("v61-real-provider", 101, "failure")),
        )
