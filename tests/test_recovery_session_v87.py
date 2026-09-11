import pytest

from protected_delivery.recovery_evidence_v85 import RecoveryEvidenceLedgerV85
from protected_delivery.recovery_orchestrator_v84 import WorkflowFailureV84
from protected_delivery.recovery_session_v87 import RecoverySessionCoordinatorV87, RecoverySessionV87

HEAD = "a" * 40


class Gateway:
    def __init__(self):
        self.calls = []

    def rerun_failed_job(self, *, job_id, expected_head_sha):
        self.calls.append(("rerun", job_id, expected_head_sha))
        return {"accepted": True}

    def request_rework(self, *, pr_number, expected_head_sha, reason):
        self.calls.append(("rework", pr_number, expected_head_sha, reason))
        return {"accepted": True}

    def escalate_human(self, *, pr_number, expected_head_sha, reason):
        self.calls.append(("human", pr_number, expected_head_sha, reason))
        return {"accepted": True}


def test_single_provider_failure_executes_and_appends_evidence():
    gateway = Gateway()
    c = RecoverySessionCoordinatorV87(gateway)
    session = RecoverySessionV87(
        pr_number=31,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        failures=(WorkflowFailureV84("v71-autonomous-delivery", "failure", 101, "http_503"),),
    )
    result = c.run(session)
    assert result["action"] == "rerun_failed_job"
    assert result["ledger_appended"] is True
    assert len(result["ledger"]) == 1
    verified = RecoveryEvidenceLedgerV85.verify(result["ledger"], expected_head_sha=HEAD, pr_number=31)
    assert verified["status"] == "VERIFIED"
    assert gateway.calls[0][0] == "rerun"


def test_code_failure_requests_rework_and_appends_evidence():
    gateway = Gateway()
    c = RecoverySessionCoordinatorV87(gateway)
    session = RecoverySessionV87(
        pr_number=31,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        failures=(WorkflowFailureV84("ci", "failure", 202, "assertion failed"),),
    )
    result = c.run(session)
    assert result["action"] == "autonomous_rework"
    assert result["ledger_appended"] is True
    assert gateway.calls[0][0] == "rework"


def test_stale_head_fails_before_gateway_mutation():
    gateway = Gateway()
    c = RecoverySessionCoordinatorV87(gateway)
    session = RecoverySessionV87(
        pr_number=31,
        expected_head_sha=HEAD,
        current_head_sha="b" * 40,
        failures=(WorkflowFailureV84("ci", "failure", 1, "boom"),),
    )
    with pytest.raises(RuntimeError, match="stale_pr_head"):
        c.run(session)
    assert gateway.calls == []


def test_existing_ledger_must_match_live_failure():
    gateway = Gateway()
    ledger = RecoveryEvidenceLedgerV85.append(
        (), pr_number=31, expected_head_sha=HEAD, current_head_sha=HEAD,
        workflow="v71-autonomous-delivery", job_id=10, category="provider_transient",
        action="rerun_failed_job", attempt=0, accepted=True,
    )
    c = RecoverySessionCoordinatorV87(gateway)
    session = RecoverySessionV87(
        pr_number=31,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        ledger=ledger,
        failures=(WorkflowFailureV84("v71-autonomous-delivery", "failure", 11, "http_503"),),
    )
    with pytest.raises(RuntimeError, match="ledger_job_no_longer_failed|new_untracked_failure"):
        c.run(session)
    assert gateway.calls == []


def test_multi_failure_escalation_does_not_forge_single_failure_evidence():
    gateway = Gateway()
    c = RecoverySessionCoordinatorV87(gateway)
    session = RecoverySessionV87(
        pr_number=31,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        failures=(
            WorkflowFailureV84("v71-autonomous-delivery", "failure", 1, "http_503"),
            WorkflowFailureV84("ci", "failure", 2, "assertion failed"),
        ),
    )
    result = c.run(session)
    assert result["action"] == "escalate_human"
    assert result["ledger_appended"] is False
    assert result["ledger"] == ()


def test_rejected_gateway_receipt_fails_closed():
    class RejectingGateway(Gateway):
        def rerun_failed_job(self, *, job_id, expected_head_sha):
            return {"accepted": False}

    c = RecoverySessionCoordinatorV87(RejectingGateway())
    session = RecoverySessionV87(
        pr_number=31,
        expected_head_sha=HEAD,
        current_head_sha=HEAD,
        failures=(WorkflowFailureV84("v71-autonomous-delivery", "failure", 101, "http_503"),),
    )
    with pytest.raises(RuntimeError, match="rerun_not_accepted"):
        c.run(session)
