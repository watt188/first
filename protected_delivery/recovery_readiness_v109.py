VERSION = "10.9"


class ProductionReadinessGateV109:
    """Fail-closed capability gate for live recovery.

    Capability declarations are configuration attestations, not proof of a
    provider's physical exactly-once behavior.
    """

    REQUIRED = ("durable_idempotency", "lookup_by_idempotency_key")

    def __init__(self, *, command, capabilities):
        if not callable(getattr(command, "execute", None)):
            raise TypeError("recovery_command_execute_missing")
        if not callable(capabilities):
            raise TypeError("recovery_capability_provider_missing")
        self.command = command
        self.capabilities = capabilities

    def execute(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise TypeError("recovery_command_must_be_object")
        if payload.get("operation") != "recover":
            return self.command.execute(payload)

        snapshot = self.capabilities()
        if not isinstance(snapshot, dict):
            raise RuntimeError("invalid_recovery_capabilities")
        missing = [name for name in self.REQUIRED if snapshot.get(name) is not True]
        if missing:
            raise RuntimeError("recovery_gateway_not_production_ready:" + ",".join(missing))
        provider = snapshot.get("provider")
        scope = snapshot.get("scope")
        if not isinstance(provider, str) or not provider.strip():
            raise RuntimeError("invalid_recovery_gateway_provider")
        if not isinstance(scope, str) or not scope.strip():
            raise RuntimeError("invalid_recovery_gateway_scope")

        result = self.command.execute(payload)
        if not isinstance(result, dict):
            raise RuntimeError("invalid_recovery_command_result")
        return {
            **result,
            "production_readiness_version": VERSION,
            "gateway_provider": provider.strip(),
            "gateway_scope": scope.strip(),
            "gateway_capabilities_attested": True,
        }
