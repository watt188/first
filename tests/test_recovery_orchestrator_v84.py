import pytest

from protected_delivery.recovery_orchestrator_v84 import LiveRecoveryOrchestratorV84, RecoverySnapshotV84, WorkflowFailureV84

HEAD = "a" * 40


class Gateway:
    def __init__(self): self.calls = []
    def rerun_failed_job(self, *, job_id, expected_head_sha):
        self.calls.append(("rerun", job_id, expected_head_sha)); return {"accepted": True}
    def request_rework(self, *, pr_number, expected_head_sha, reason):
        self.calls.append(("rework", pr_number, expected_head_sha, reason)); return {"accepted": True}
    def escalate_human(self, *, pr_number, expected_head_sha, reason):
        self.calls.append(("human", pr_number, expected_head_sha, reason)); return {"accepted": True}


def test_single_provider_failure_reruns_exact_job():
    gateway = Gateway()
    result = LiveRecoveryOrchestratorV84(gateway).recover(RecoverySnapshotV84(26, HEAD, HEAD, (WorkflowFailureV84("v70-production-convergence", "failure", 99, "provider_circuit_open"),)))
    assert result["action"] == "rerun_failed_job"
    assert result["version"] == "8.4"
    assert gateway.calls == [("rerun", 99, HEAD)]


def test_single_code_failure_requests_rework():
    gateway = Gateway()
    result = LiveRecoveryOrchestratorV84(gateway).recover(RecoverySnapshotV84(26, HEAD, HEAD, (WorkflowFailureV84("ci", "failure", 8, "assertion failed"),)))
    assert result["action"] == "autonomous_rework"
    assert gateway.calls[0][0] == "rework"


def test_stale_head_fails_before_action():
    gateway = Gateway()
    with pytest.raises(RuntimeError, match="stale_pr_head"):
        LiveRecoveryOrchestratorV84(gateway).recover(RecoverySnapshotV84(26, HEAD, "b" * 40, (WorkflowFailureV84("ci", "failure", 8),)))
    assert gateway.calls == []


def test_mixed_multi_failure_escalates():
    gateway = Gateway()
    result = LiveRecoveryOrchestratorV84(gateway).recover(RecoverySnapshotV84(26, HEAD, HEAD, (WorkflowFailureV84("ci", "failure", 8, "assertion failed"), WorkflowFailureV84("v70-production-convergence", "failure", 9, "provider_circuit_open"))))
    assert result["action"] == "escalate_human"
    assert result["category"] == "multi_failure_ambiguous"
    assert gateway.calls == [("human", 26, HEAD, "multi_failure_ambiguous")]


def test_retry_budget_exhaustion_escalates():
    gateway = Gateway()
    result = LiveRecoveryOrchestratorV84(gateway).recover(RecoverySnapshotV84(26, HEAD, HEAD, (WorkflowFailureV84("v70-production-convergence", "failure", 9, "provider_circuit_open"),), attempts_by_job={9: 2}, max_attempts=2))
    assert result["action"] == "escalate_human"
    assert gateway.calls[0][0] == "human"
