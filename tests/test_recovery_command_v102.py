import json
import pytest

from protected_delivery.recovery_command_v102 import ProductionRecoveryCommandV102

HEAD = "a" * 40


class Service:
    def __init__(self, *, status="PRODUCTION_RECOVERY_CONVERGED"):
        self.status = status
        self.health_calls = 0
        self.run_calls = []

    def health(self):
        self.health_calls += 1
        return {"status": "READY", "version": "10.1"}

    def run(self, **request):
        self.run_calls.append(request)
        return {"status": self.status, "head_sha": request["expected_head_sha"]}


def payload(**overrides):
    base = {
        "operation": "recover",
        "pr_number": 7,
        "expected_head_sha": HEAD,
        "current_head_sha": HEAD,
        "workflow": "ci",
        "job_id": 91,
        "conclusion": "failure",
        "log_excerpt": "transport_timeout_or_network",
        "now": 10,
        "max_attempts": 2,
    }
    base.update(overrides)
    return base


def test_health_is_read_only_and_strict():
    service = Service()
    command = ProductionRecoveryCommandV102(service=service)
    result = command.handle({"operation": "health"})
    assert result["command_version"] == "10.2"
    assert result["result"]["status"] == "READY"
    assert service.health_calls == 1
    assert service.run_calls == []
    with pytest.raises(ValueError, match="invalid_health_command_fields"):
        command.handle({"operation": "health", "extra": True})


def test_recover_delegates_exact_validated_request():
    service = Service()
    command = ProductionRecoveryCommandV102(service=service)
    result = command.handle(payload())
    assert result["operation"] == "recover"
    assert result["result"]["status"] == "PRODUCTION_RECOVERY_CONVERGED"
    assert len(service.run_calls) == 1
    assert service.run_calls[0]["expected_head_sha"] == HEAD
    assert service.run_calls[0]["workflow"] == "ci"


def test_stale_head_fails_before_service_mutation():
    service = Service()
    command = ProductionRecoveryCommandV102(service=service)
    with pytest.raises(RuntimeError, match="stale_pr_head"):
        command.handle(payload(current_head_sha="b" * 40))
    assert service.run_calls == []


def test_unknown_fields_and_missing_fields_fail_closed():
    command = ProductionRecoveryCommandV102(service=Service())
    with pytest.raises(ValueError, match="unknown_recovery_command_field"):
        command.handle(payload(secret="never-accepted"))
    broken = payload()
    del broken["workflow"]
    with pytest.raises(ValueError, match="missing_recovery_command_field"):
        command.handle(broken)


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("pr_number", 0, "invalid_pr_number"),
        ("job_id", True, "invalid_job_id"),
        ("now", -1, "invalid_command_time"),
        ("max_attempts", 0, "invalid_max_attempts"),
        ("expected_head_sha", "ABC", "invalid_expected_head_sha"),
        ("current_head_sha", "x" * 40, "invalid_current_head_sha"),
        ("workflow", " ", "invalid_workflow"),
        ("conclusion", "", "invalid_conclusion"),
        ("log_excerpt", 42, "invalid_log_excerpt"),
    ],
)
def test_invalid_recovery_identity_rejected(field, value, error):
    command = ProductionRecoveryCommandV102(service=Service())
    with pytest.raises((ValueError, RuntimeError), match=error):
        command.handle(payload(**{field: value}))


def test_non_converged_service_result_rejected():
    command = ProductionRecoveryCommandV102(service=Service(status="READY"))
    with pytest.raises(RuntimeError, match="command_non_converged_result"):
        command.handle(payload())


def test_json_surface_is_canonical_and_rejects_non_object():
    command = ProductionRecoveryCommandV102(service=Service())
    raw = command.handle_json(json.dumps({"operation": "health"}))
    assert raw == json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":"))
    with pytest.raises(TypeError, match="command_payload_must_be_object"):
        command.handle_json("[]")
    with pytest.raises(ValueError, match="invalid_command_json"):
        command.handle_json("{")


def test_service_contract_is_required():
    class MissingRun:
        def health(self):
            return {}

    with pytest.raises(TypeError, match="service_run_missing"):
        ProductionRecoveryCommandV102(service=MissingRun())
