from protected_delivery.recovery_authoritative_v107 import AuthoritativeRecoveryCommandV107
from protected_delivery.recovery_github_head_v108 import GitHubPRHeadResolverV108

VERSION = "10.8"


class GitHubAuthoritativeRecoveryCommandV108:
    """Concrete V10.7 authoritative command bound to GitHub PR heads."""

    def __init__(
        self,
        *,
        command,
        repository_full_name: str,
        token: str | None = None,
        api_base: str = "https://api.github.com",
        timeout: float = 10.0,
        opener=None,
    ):
        self.resolver = GitHubPRHeadResolverV108(
            repository_full_name=repository_full_name,
            token=token,
            api_base=api_base,
            timeout=timeout,
            opener=opener,
        )
        self.bound = AuthoritativeRecoveryCommandV107(command=command, head_resolver=self.resolver)

    def execute(self, payload: dict) -> dict:
        result = self.bound.execute(payload)
        if not isinstance(result, dict):
            raise RuntimeError("invalid_authoritative_recovery_result")
        return {**result, "github_binding_version": VERSION}
