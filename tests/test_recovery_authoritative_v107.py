import pytest

from protected_delivery.recovery_authoritative_v107 import AuthoritativeRecoveryCommandV107

SHA_A = "a" * 40
SHA_B = "b" * 40


class Command:
    def __init__(self):
        self.calls = []

    def execute(self, payload):
        self.calls.append(dict(payload))
        return {"status": "PRODUCTION_RECOVERY_CONVERGED"}


def request(**overrides):
    value = {
        "operation": "recover",
        "pr_number": 7,
        "expected_head_sha": SHA_A,
        "current_head_sha": SHA_B,
        "workflow": "ci",
        "job_id": 1,
        "conclusion": "failure",
        "log_excerpt": "failed",
        "now": 1,
    }
    value.update(overrides)
    return value


def test_recover_overwrites_untrusted_current_head_with_authoritative_value():
    command = Command()
    wrapper = AuthoritativeRecoveryCommandV107(command=command, head_resolver=lambda pr: SHA_A)
    result = wrapper.execute(request())
    assert command.calls[0]["current_head_sha"] == SHA_A
    assert result["authoritative_head_sha"] == SHA_A
    assert result["authoritative_binding_version"] == "10.7"


def test_stale_expected_head_fails_before_downstream_mutation():
    command = Command()
    wrapper = AuthoritativeRecoveryCommandV107(command=command, head_resolver=lambda pr: SHA_B)
    with pytest.raises(RuntimeError, match="stale_pr_head"):
        wrapper.execute(request())
    assert command.calls == []


def test_invalid_authoritative_head_fails_closed():
    command = Command()
    wrapper = AuthoritativeRecoveryCommandV107(command=command, head_resolver=lambda pr: "not-a-sha")
    with pytest.raises(ValueError, match="invalid_authoritative_head_sha"):
        wrapper.execute(request())
    assert command.calls == []


def test_health_passthrough_does_not_require_head_lookup():
    command = Command()
    called = []
    wrapper = AuthoritativeRecoveryCommandV107(command=command, head_resolver=lambda pr: called.append(pr))
    assert wrapper.execute({"operation": "health"})["status"] == "PRODUCTION_RECOVERY_CONVERGED"
    assert called == []


def test_contract_validation():
    with pytest.raises(TypeError, match="recovery_command_execute_missing"):
        AuthoritativeRecoveryCommandV107(command=object(), head_resolver=lambda pr: SHA_A)
    with pytest.raises(TypeError, match="authoritative_head_resolver_missing"):
        AuthoritativeRecoveryCommandV107(command=Command(), head_resolver=object())
