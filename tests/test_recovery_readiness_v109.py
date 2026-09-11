import pytest
from protected_delivery.recovery_readiness_v109 import ProductionReadinessGateV109


class Command:
    def __init__(self): self.calls=[]
    def execute(self, payload):
        self.calls.append(dict(payload)); return {"status":"PRODUCTION_RECOVERY_CONVERGED"}


def caps(**overrides):
    value={"provider":"github-recovery-gateway","scope":"single-host","durable_idempotency":True,"lookup_by_idempotency_key":True}
    value.update(overrides); return value


def test_ready_gateway_delegates_and_attests():
    command=Command(); gate=ProductionReadinessGateV109(command=command, capabilities=lambda:caps())
    result=gate.execute({"operation":"recover"})
    assert len(command.calls)==1
    assert result["production_readiness_version"]=="10.9"
    assert result["gateway_capabilities_attested"] is True


def test_missing_idempotency_capability_fails_before_mutation():
    command=Command(); gate=ProductionReadinessGateV109(command=command, capabilities=lambda:caps(durable_idempotency=False))
    with pytest.raises(RuntimeError, match="recovery_gateway_not_production_ready"):
        gate.execute({"operation":"recover"})
    assert command.calls==[]


def test_missing_lookup_capability_fails_before_mutation():
    command=Command(); gate=ProductionReadinessGateV109(command=command, capabilities=lambda:caps(lookup_by_idempotency_key=False))
    with pytest.raises(RuntimeError, match="recovery_gateway_not_production_ready"):
        gate.execute({"operation":"recover"})
    assert command.calls==[]


def test_invalid_snapshot_fails_closed():
    command=Command(); gate=ProductionReadinessGateV109(command=command, capabilities=lambda:None)
    with pytest.raises(RuntimeError, match="invalid_recovery_capabilities"):
        gate.execute({"operation":"recover"})
    assert command.calls==[]


def test_health_passthrough_skips_capability_gate():
    command=Command(); gate=ProductionReadinessGateV109(command=command, capabilities=lambda:(_ for _ in ()).throw(Exception()))
    assert gate.execute({"operation":"health"})["status"]=="PRODUCTION_RECOVERY_CONVERGED"


def test_attestation_is_not_inferred_from_truthy_values():
    command=Command(); gate=ProductionReadinessGateV109(command=command, capabilities=lambda:caps(durable_idempotency="yes"))
    with pytest.raises(RuntimeError, match="recovery_gateway_not_production_ready"):
        gate.execute({"operation":"recover"})
