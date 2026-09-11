VERSION = "10.7"


class AuthoritativeRecoveryCommandV107:
    """Fail-closed exact-head boundary above an existing recovery command.

    The caller-supplied current_head_sha is never trusted. A head resolver must
    return the authoritative live head for the target PR immediately before the
    command is delegated to V10.2+.
    """

    def __init__(self, *, command, head_resolver):
        if not callable(getattr(command, "execute", None)):
            raise TypeError("recovery_command_execute_missing")
        if not callable(head_resolver):
            raise TypeError("authoritative_head_resolver_missing")
        self.command = command
        self.head_resolver = head_resolver

    @staticmethod
    def _sha(value, *, error):
        if not isinstance(value, str) or len(value) != 40 or value.lower() != value:
            raise ValueError(error)
        if any(ch not in "0123456789abcdef" for ch in value):
            raise ValueError(error)
        return value

    def execute(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise TypeError("recovery_command_must_be_object")
        operation = payload.get("operation")
        if operation != "recover":
            return self.command.execute(payload)

        pr_number = payload.get("pr_number")
        if not isinstance(pr_number, int) or isinstance(pr_number, bool) or pr_number < 1:
            raise ValueError("invalid_pr_number")
        expected = self._sha(payload.get("expected_head_sha"), error="invalid_expected_head_sha")
        authoritative = self._sha(
            self.head_resolver(pr_number), error="invalid_authoritative_head_sha"
        )
        if expected != authoritative:
            raise RuntimeError("stale_pr_head")

        bound = dict(payload)
        bound["current_head_sha"] = authoritative
        result = self.command.execute(bound)
        if not isinstance(result, dict):
            raise RuntimeError("invalid_recovery_command_result")
        return {**result, "authoritative_binding_version": VERSION, "authoritative_head_sha": authoritative}
