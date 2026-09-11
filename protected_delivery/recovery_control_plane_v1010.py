from protected_delivery.recovery_secure_service_v105 import SecureProductionRecoveryServiceV105
from protected_delivery.recovery_github_head_v108 import GitHubPRHeadResolverV108

VERSION = "10.10"


class _BoundReadyCommandV1010:
    """Compose live GitHub head binding and strict gateway readiness over V10.2."""

    REQUIRED = ("durable_idempotency", "lookup_by_idempotency_key")

    def __init__(self, *, command, head_resolver, capabilities):
        if not callable(getattr(command, "handle", None)):
            raise TypeError("recovery_command_handle_missing")
        if not callable(head_resolver):
            raise TypeError("authoritative_head_resolver_missing")
        if not callable(capabilities):
            raise TypeError("recovery_capability_provider_missing")
        self.command = command
        self.head_resolver = head_resolver
        self.capabilities = capabilities

    def handle(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise TypeError("command_payload_must_be_object")
        if payload.get("operation") != "recover":
            return self.command.handle(payload)

        pr_number = payload.get("pr_number")
        if not isinstance(pr_number, int) or isinstance(pr_number, bool) or pr_number < 1:
            raise ValueError("invalid_pr_number")
        expected = payload.get("expected_head_sha")
        authoritative = self.head_resolver(pr_number)
        if expected != authoritative:
            raise RuntimeError("stale_pr_head")

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

        bound = dict(payload)
        bound["current_head_sha"] = authoritative
        result = self.command.handle(bound)
        if not isinstance(result, dict):
            raise RuntimeError("invalid_recovery_command_result")
        return {
            **result,
            "control_plane_version": VERSION,
            "authoritative_head_sha": authoritative,
            "gateway_provider": provider.strip(),
            "gateway_scope": scope.strip(),
            "gateway_capabilities_attested": True,
        }


class ProductionRecoveryControlPlaneV1010:
    """Final single-host production assembly through V10.10.

    Authentication/replay/key rotation from V10.3-V10.5 stay outside the bound
    command, so only authenticated envelopes can reach live head/readiness checks.
    """

    def __init__(
        self,
        *,
        root,
        gateway,
        owner: str,
        nonce: str,
        secrets: dict[str, bytes],
        repository_full_name: str,
        gateway_capabilities,
        github_token: str | None = None,
        github_api_base: str = "https://api.github.com",
        github_timeout: float = 10.0,
        github_opener=None,
        lease_ttl: int = 120,
        max_clock_skew: int = 300,
    ):
        self.secure = SecureProductionRecoveryServiceV105(
            root=root,
            gateway=gateway,
            owner=owner,
            nonce=nonce,
            secrets=secrets,
            lease_ttl=lease_ttl,
            max_clock_skew=max_clock_skew,
        )
        self.head_resolver = GitHubPRHeadResolverV108(
            repository_full_name=repository_full_name,
            token=github_token,
            api_base=github_api_base,
            timeout=github_timeout,
            opener=github_opener,
        )
        self.command = _BoundReadyCommandV1010(
            command=self.secure.command,
            head_resolver=self.head_resolver,
            capabilities=gateway_capabilities,
        )
        self.secure.auth.command = self.command

    def activate_key(self, **kwargs):
        return self.secure.activate_key(**kwargs)

    def retire_key(self, **kwargs):
        return self.secure.retire_key(**kwargs)

    def revoke_key(self, **kwargs):
        return self.secure.revoke_key(**kwargs)

    def sign(self, **kwargs):
        return self.secure.sign(**kwargs)

    def handle(self, envelope: dict, *, now: int) -> dict:
        result = self.secure.handle(envelope, now=now)
        return {**result, "control_plane_version": VERSION}

    def health(self, *, now: int) -> dict:
        base = self.secure.health(now=now)
        return {
            **base,
            "control_plane_version": VERSION,
            "authoritative_head_source": "github_pull_request_api",
            "production_readiness_gate": True,
        }
