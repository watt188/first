import pytest

from protected_delivery.recovery_v82 import AutonomousRecoveryV82, FailureSignal


def test_provider_transient_retries_with_budget():
    decision = AutonomousRecoveryV82.next_action(
        FailureSignal("v70-production-convergence", "failure", "RuntimeError: provider_circuit_open"),
        attempts=0,
    )
    assert decision.category == "provider_transient"
    assert decision.action == "rerun_failed_job"
    assert decision.requires_human is False


def test_provider_retry_budget_exhaustion_escalates():
    decision = AutonomousRecoveryV82.next_action(
        FailureSignal("v70-production-convergence", "failure", "http_503"),
        attempts=2,
        max_attempts=2,
    )
    assert decision.action == "escalate_human"
    assert decision.requires_human is True


def test_policy_drift_never_auto_bypasses():
    decision = AutonomousRecoveryV82.next_action(
        FailureSignal("v75-merge-authorization", "failure", "missing_required_checks:ci"),
        attempts=0,
    )
    assert decision.category == "policy_drift"
    assert decision.retryable is False
    assert decision.requires_human is True


def test_permission_failure_requires_human():
    decision = AutonomousRecoveryV82.next_action(
        FailureSignal("delivery", "failure", "Resource not accessible by integration"),
        attempts=0,
    )
    assert decision.category == "permission"
    assert decision.requires_human is True


def test_evidence_failure_requests_autonomous_rework():
    decision = AutonomousRecoveryV82.next_action(
        FailureSignal("v69-evidence-integrity", "failure", "manifest sha256 mismatch"),
        attempts=0,
    )
    assert decision.category == "evidence_inconsistency"
    assert decision.action == "autonomous_rework"
    assert decision.requires_human is False


def test_deterministic_code_gate_requests_rework():
    decision = AutonomousRecoveryV82.next_action(
        FailureSignal("ci", "failure", "AssertionError"),
        attempts=0,
    )
    assert decision.category == "code_or_test_defect"
    assert decision.action == "autonomous_rework"


def test_ambiguous_failure_fails_closed():
    decision = AutonomousRecoveryV82.next_action(
        FailureSignal("unknown", "failure", "something unusual happened"),
        attempts=0,
    )
    assert decision.category == "ambiguous"
    assert decision.requires_human is True


def test_rejects_non_failure_signal():
    with pytest.raises(ValueError, match="not_a_failure"):
        AutonomousRecoveryV82.classify(FailureSignal("ci", "success", ""))
