import dataclasses

import pytest

from protected_delivery.recovery_evidence_v85 import RecoveryEvidenceLedgerV85

HEAD = "a" * 40


def _append(events=(), *, attempt=0, job_id=11, head=HEAD):
    return RecoveryEvidenceLedgerV85.append(
        events,
        pr_number=9,
        expected_head_sha=HEAD,
        current_head_sha=head,
        workflow="v70-production-convergence",
        job_id=job_id,
        category="provider_transient",
        action="rerun_failed_job",
        attempt=attempt,
        accepted=True,
    )


def test_append_and_verify_exact_head_chain():
    events = _append()
    events = _append(events, attempt=1)
    result = RecoveryEvidenceLedgerV85.verify(events, expected_head_sha=HEAD, pr_number=9)
    assert result["status"] == "VERIFIED"
    assert result["event_count"] == 2
    assert result["chain_head"] == events[-1].event_hash
    assert events[1].previous_hash == events[0].event_hash


def test_rejects_stale_head_before_append():
    with pytest.raises(RuntimeError, match="stale_pr_head"):
        _append(head="b" * 40)


def test_rejects_unaccepted_receipt():
    with pytest.raises(RuntimeError, match="unaccepted_recovery_receipt"):
        RecoveryEvidenceLedgerV85.append(
            (), pr_number=9, expected_head_sha=HEAD, current_head_sha=HEAD,
            workflow="ci", job_id=1, category="code_or_test_defect",
            action="autonomous_rework", attempt=0, accepted=False,
        )


def test_rejects_non_monotonic_attempt_for_same_job():
    events = _append(attempt=1)
    with pytest.raises(RuntimeError, match="non_monotonic_attempt"):
        _append(events, attempt=1)


def test_detects_tampered_event_payload():
    events = _append()
    tampered = (dataclasses.replace(events[0], action="escalate_human"),)
    with pytest.raises(RuntimeError, match="ledger_event_hash_mismatch"):
        RecoveryEvidenceLedgerV85.verify(tampered, expected_head_sha=HEAD, pr_number=9)


def test_detects_chain_break():
    events = _append()
    second = _append(events, attempt=1)[1]
    broken = (events[0], dataclasses.replace(second, previous_hash="f" * 64))
    with pytest.raises(RuntimeError, match="ledger_chain_mismatch"):
        RecoveryEvidenceLedgerV85.verify(broken, expected_head_sha=HEAD, pr_number=9)


def test_detects_binding_change():
    events = _append()
    with pytest.raises(RuntimeError, match="ledger_binding_mismatch"):
        RecoveryEvidenceLedgerV85.verify(events, expected_head_sha="b" * 40, pr_number=9)
