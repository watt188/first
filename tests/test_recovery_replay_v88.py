import dataclasses
import pytest

from protected_delivery.recovery_evidence_v85 import RecoveryEvidenceLedgerV85
from protected_delivery.recovery_replay_v88 import RecoveryReplayGuardV88, RecoveryReplayIntentV88

HEAD = "a" * 40


def _intent(**overrides):
    data = dict(
        pr_number=31,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow="v65-multi-agent",
        job_id=101,
        action="rerun_failed_job",
        attempt=1,
    )
    data.update(overrides)
    return RecoveryReplayIntentV88(**data)


def _ledger(attempt=0, action="rerun_failed_job"):
    return RecoveryEvidenceLedgerV85.append(
        (),
        pr_number=31,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        workflow="v65-multi-agent",
        job_id=101,
        category="provider_transient",
        action=action,
        attempt=attempt,
        accepted=True,
    )


def test_authorizes_new_exact_head_action():
    result = RecoveryReplayGuardV88.authorize((), _intent(attempt=0))
    assert result["status"] == "AUTHORIZED"
    assert len(result["replay_key"]) == 64


def test_rejects_duplicate_replay():
    with pytest.raises(RuntimeError, match="duplicate_recovery_replay"):
        RecoveryReplayGuardV88.authorize(_ledger(attempt=1), _intent(attempt=1))


def test_rejects_stale_attempt_for_same_job():
    with pytest.raises(RuntimeError, match="stale_recovery_attempt"):
        RecoveryReplayGuardV88.authorize(_ledger(attempt=1), _intent(action="request_rework", attempt=1))


def test_allows_monotonic_next_attempt():
    result = RecoveryReplayGuardV88.authorize(_ledger(attempt=1), _intent(attempt=2))
    assert result["ledger_event_count"] == 1


def test_rejects_stale_head():
    with pytest.raises(RuntimeError, match="stale_pr_head"):
        RecoveryReplayGuardV88.authorize((), _intent(current_head_sha="b" * 40))


def test_rejects_tampered_ledger():
    event = _ledger(attempt=1)[0]
    tampered = (dataclasses.replace(event, action="request_rework"),)
    with pytest.raises(RuntimeError, match="ledger_event_hash_mismatch"):
        RecoveryReplayGuardV88.authorize(tampered, _intent(attempt=2))


def test_rejects_invalid_identity():
    with pytest.raises(ValueError, match="invalid_recovery_identity"):
        RecoveryReplayGuardV88.authorize((), _intent(workflow=""))
