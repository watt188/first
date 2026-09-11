import json
import re

VERSION = "10.2"
_HEAD = re.compile(r"^[0-9a-f]{40}$")
_RECOVER_KEYS = {
    "operation",
    "pr_number",
    "expected_head_sha",
    "current_head_sha",
    "workflow",
    "job_id",
    "conclusion",
    "log_excerpt",
    "now",
    "max_attempts",
}
_REQUIRED_RECOVER = {
    "operation",
    "pr_number",
    "expected_head_sha",
    "current_head_sha",
    "workflow",
    "job_id",
    "conclusion",
    "now",
}


class ProductionRecoveryCommandV102:
    """Strict JSON-compatible command surface over V10.1.

    The adapter exposes only two operations: ``health`` and ``recover``. It
    rejects unknown fields, validates exact-head identities before delegation,
    and never accepts arbitrary method names or executable payloads.
    """

    def __init__(self, *, service):
        if not callable(getattr(service, "health", None)):
            raise TypeError("service_health_missing")
        if not callable(getattr(service, "run", None)):
            raise TypeError("service_run_missing")
        self.service = service

    @staticmethod
    def _validate_recover(payload: dict) -> dict:
        unknown = set(payload) - _RECOVER_KEYS
        missing = _REQUIRED_RECOVER - set(payload)
        if unknown:
            raise ValueError("unknown_recovery_command_field")
        if missing:
            raise ValueError("missing_recovery_command_field")
        if payload.get("operation") != "recover":
            raise ValueError("invalid_recovery_operation")

        pr_number = payload.get("pr_number")
        job_id = payload.get("job_id")
        now = payload.get("now")
        max_attempts = payload.get("max_attempts", 2)
        if not isinstance(pr_number, int) or isinstance(pr_number, bool) or pr_number < 1:
            raise ValueError("invalid_pr_number")
        if not isinstance(job_id, int) or isinstance(job_id, bool) or job_id < 1:
            raise ValueError("invalid_job_id")
        if not isinstance(now, int) or isinstance(now, bool) or now < 0:
            raise ValueError("invalid_command_time")
        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
            raise ValueError("invalid_max_attempts")

        expected = payload.get("expected_head_sha")
        current = payload.get("current_head_sha")
        if not isinstance(expected, str) or not _HEAD.fullmatch(expected):
            raise ValueError("invalid_expected_head_sha")
        if not isinstance(current, str) or not _HEAD.fullmatch(current):
            raise ValueError("invalid_current_head_sha")
        if expected != current:
            raise RuntimeError("stale_pr_head")

        workflow = payload.get("workflow")
        conclusion = payload.get("conclusion")
        log_excerpt = payload.get("log_excerpt", "")
        if not isinstance(workflow, str) or not workflow.strip():
            raise ValueError("invalid_workflow")
        if not isinstance(conclusion, str) or not conclusion.strip():
            raise ValueError("invalid_conclusion")
        if not isinstance(log_excerpt, str):
            raise ValueError("invalid_log_excerpt")

        return {
            "pr_number": pr_number,
            "expected_head_sha": expected,
            "current_head_sha": current,
            "workflow": workflow.strip(),
            "job_id": job_id,
            "conclusion": conclusion.strip(),
            "log_excerpt": log_excerpt,
            "now": now,
            "max_attempts": max_attempts,
        }

    def handle(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise TypeError("command_payload_must_be_object")
        operation = payload.get("operation")
        if operation == "health":
            if set(payload) != {"operation"}:
                raise ValueError("invalid_health_command_fields")
            result = self.service.health()
            return {"command_version": VERSION, "operation": "health", "result": result}
        if operation == "recover":
            request = self._validate_recover(payload)
            result = self.service.run(**request)
            if result.get("status") != "PRODUCTION_RECOVERY_CONVERGED":
                raise RuntimeError("command_non_converged_result")
            return {"command_version": VERSION, "operation": "recover", "result": result}
        raise ValueError("unsupported_recovery_operation")

    def handle_json(self, raw: str) -> str:
        if not isinstance(raw, str):
            raise TypeError("command_json_must_be_text")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid_command_json") from exc
        result = self.handle(payload)
        return json.dumps(result, sort_keys=True, separators=(",", ":"))
