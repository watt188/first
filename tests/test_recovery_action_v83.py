import pytest

from protected_delivery.recovery_v82 import FailureSignal
from protected_delivery.recovery_action_v83 import RecoveryActionBridgeV83, RecoveryContextV83

HEAD = "a" * 40


class Gateway:
    def __init__(self, accepted=True):
        self.accepted = accepted
        self.calls = []

    def rerun_failed_job(self, **kwargs):
        self.calls.append(("rerun", kwargs))
        return {"accepted": self.accepted}

    def request_rework(self, **kwargs):
        self.calls.append(("rework", kwargs))
        return {"accepted": self.accepted}

    def escalate_human(self, **kwargs):
        self.calls.append(("human", kwargs))
        return {"accepted": self.accepted}


def ctx(**kwargs):
    base = dict(pr_number=7, expected_head_sha=HEAD, current_head_sha=HEAD, failed_job_id=99)
    base.update(kwargs)
    return RecoveryContextV83(**base)


def test_transient_provider_reruns_exact_failed_job():
    gateway = Gateway()
    result = RecoveryActionBridgeV83(gateway).execute(
        FailureSignal("v70-production-convergence", "failure", "provider_circuit_open"), ctx()
    )
    assert result["action"] == "rerun_failed_job"
    assert gateway.calls == [("rerun", {"job_id": 99, "expected_head_sha": HEAD})]


def test_code_failure_requests_rework():
    gateway = Gateway()
    result = RecoveryActionBridgeV83(gateway).execute(
        FailureSignal("ci", "failure", "pytest assertion failed"), ctx()
    )
    assert result["action"] == "autonomous_rework"
    assert gateway.calls[0][0] == "rework"


def test_policy_drift_escalates_human():
    gateway = Gateway()
    result = RecoveryActionBridgeV83(gateway).execute(
        FailureSignal("v75-merge-authorization", "failure", "ruleset policy drift"), ctx()
    )
    assert result["requires_human"] is True
    assert gateway.calls[0][0] == "human"


def test_retry_budget_exhaustion_escalates():
    gateway = Gateway()
    result = RecoveryActionBridgeV83(gateway).execute(
        FailureSignal("v70-production-convergence", "failure", "http_503"), ctx(attempts=2, max_attempts=2)
    )
    assert result["action"] == "escalate_human"
    assert gateway.calls[0][0] == "human"


def test_stale_head_rejected_before_action():
    gateway = Gateway()
    with pytest.raises(RuntimeError, match="stale_pr_head"):
        RecoveryActionBridgeV83(gateway).execute(
            FailureSignal("ci", "failure", "assertion"),
            RecoveryContextV83(7, HEAD, "b" * 40, 99),
        )
    assert gateway.calls == []


def test_unaccepted_gateway_receipt_fails_closed():
    with pytest.raises(RuntimeError, match="rerun_not_accepted"):
        RecoveryActionBridgeV83(Gateway(False)).execute(
            FailureSignal("v70-production-convergence", "failure", "timeout"), ctx()
        )
