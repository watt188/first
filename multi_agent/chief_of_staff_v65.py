import json
import os
import time
from dataclasses import dataclass
from typing import Any

from real_provider.openai_compatible_v61 import OpenAICompatibleProviderV61
from real_provider.contracts_v61 import ProviderRequestV61
from real_provider.pep_v61 import UnifiedPEPV61


@dataclass
class AgentResultV65:
    role: str
    content: dict[str, Any]


class ChiefOfStaffV65:
    """Production-style bounded multi-agent orchestrator."""

    ALLOWED_PATHS = ("generated/feature_v65.py", "generated/test_feature_v65.py")
    MAX_ATTEMPTS = 3

    def __init__(self, root: str = "."):
        self.root = root
        self.provider = OpenAICompatibleProviderV61()
        self.pep = UnifiedPEPV61(root)

    @staticmethod
    def _decode_json_object(content: str) -> dict[str, Any]:
        text = (content or "").strip()
        if not text:
            raise ValueError("empty_model_response")
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("invalid_json_response")
            try:
                payload = json.loads(text[start:end + 1])
            except json.JSONDecodeError as exc:
                raise ValueError("invalid_json_response") from exc
        if not isinstance(payload, dict):
            raise ValueError("json_response_not_object")
        return payload

    def _invoke_json(self, system: str, user: str, max_tokens: int = 700) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            response = self.provider.invoke(
                ProviderRequestV61(system=system, user=user, temperature=0, max_tokens=max_tokens)
            )
            try:
                if not response.ok:
                    raise RuntimeError(response.error or "provider_failed")
                return self._decode_json_object(response.content)
            except (RuntimeError, ValueError) as exc:
                last_error = exc
                if attempt < self.MAX_ATTEMPTS:
                    time.sleep(0.5 * attempt)
        raise RuntimeError(f"model_json_failed_after_{self.MAX_ATTEMPTS}_attempts:{type(last_error).__name__}") from last_error

    def plan(self, goal: str) -> AgentResultV65:
        payload = self._invoke_json(
            "You are the planner in a strict engineering organization. Return JSON only.",
            "Decompose this goal into exactly 2 tasks: backend implementation and tests. Return {\"tasks\":[{\"role\":\"backend\",\"objective\":str},{\"role\":\"test\",\"objective\":str}]}. Goal: " + goal,
        )
        tasks = payload.get("tasks", [])
        if [t.get("role") for t in tasks] != ["backend", "test"]:
            raise ValueError("invalid_plan")
        return AgentResultV65("planner", payload)

    def backend(self, objective: str) -> AgentResultV65:
        payload = self._invoke_json(
            "You are a backend specialist. Return JSON only and no markdown.",
            "Create pure Python code for generated/feature_v65.py. It must define normalize_title(text) that strips leading/trailing whitespace and collapses all internal whitespace runs to one space, and feature_info() returning exactly {'name':'normalize_title','version':'6.5'}. No imports, file IO, eval, exec, network, subprocess, classes, decorators, or side effects. Return {\"path\":\"generated/feature_v65.py\",\"content\":str}. Objective: " + objective,
            500,
        )
        if payload.get("path") != self.ALLOWED_PATHS[0] or not isinstance(payload.get("content"), str):
            raise ValueError("invalid_backend_output")
        return AgentResultV65("backend", payload)

    def tests(self, objective: str) -> AgentResultV65:
        payload = self._invoke_json(
            "You are a test specialist. Return JSON only and no markdown.",
            "Write pure Python tests for generated/feature_v65.py using plain assert statements in a function run_tests(module). Cover trimming, multiple spaces, tabs/newlines, empty string, and feature_info exact values. No imports, file IO, eval, exec, network, subprocess, pytest, unittest, classes, decorators, or side effects. Return {\"path\":\"generated/test_feature_v65.py\",\"content\":str}. Objective: " + objective,
            600,
        )
        if payload.get("path") != self.ALLOWED_PATHS[1] or not isinstance(payload.get("content"), str):
            raise ValueError("invalid_test_output")
        return AgentResultV65("test", payload)

    def review(self, feature: str, tests: str) -> AgentResultV65:
        payload = self._invoke_json(
            "You are a strict reviewer. Return JSON only.",
            "Review two Python snippets against this contract: normalize_title strips edges and collapses any whitespace run; feature_info returns exactly name normalize_title and version 6.5; tests cover trim, spaces, tabs/newlines, empty, metadata. No dangerous side effects. Return {\"approved\":bool,\"reasons\":[str]}. FEATURE:\n" + feature + "\nTESTS:\n" + tests,
            500,
        )
        if payload.get("approved") is not True:
            raise ValueError("review_rejected:" + ";".join(payload.get("reasons", [])))
        return AgentResultV65("reviewer", payload)

    def run(self, goal: str) -> dict[str, Any]:
        plan = self.plan(goal)
        backend_task, test_task = plan.content["tasks"]
        backend = self.backend(backend_task["objective"])
        tests = self.tests(test_task["objective"])
        reviewer = self.review(backend.content["content"], tests.content["content"])
        feature_write = self.pep.write_text("backend", backend.content["path"], backend.content["content"], self.ALLOWED_PATHS)
        test_write = self.pep.write_text("backend", tests.content["path"], tests.content["content"], self.ALLOWED_PATHS)
        if feature_write.get("status") != "WRITTEN" or test_write.get("status") != "WRITTEN":
            raise RuntimeError("pep_write_failed")
        return {
            "status": "PASSED",
            "agents": [plan.role, backend.role, tests.role, reviewer.role],
            "paths": [backend.content["path"], tests.content["path"]],
            "model": os.environ.get("MODEL_NAME", ""),
        }
