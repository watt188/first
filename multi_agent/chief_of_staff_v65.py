import ast
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
    BANNED_NAMES = {"open", "eval", "exec", "compile", "__import__", "input"}
    BANNED_ATTRS = {"system", "popen", "spawn", "remove", "unlink", "rmdir", "rename", "replace"}
    BACKEND_OBJECTIVE = "Implement only the canonical normalize_title and feature_info contract specified by the Chief of Staff. Do not add Unicode normalization or any requirement not explicitly present in that contract."
    TEST_OBJECTIVE = "Test only the canonical normalize_title and feature_info contract specified by the Chief of Staff. Do not add Unicode normalization or any requirement not explicitly present in that contract."

    def __init__(self, root: str = "."):
        self.root = root
        self.provider = OpenAICompatibleProviderV61()
        self.pep = UnifiedPEPV61(root)

    @staticmethod
    def _strip_fence(content: str) -> str:
        text = (content or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return text

    @classmethod
    def _decode_json_object(cls, content: str) -> dict[str, Any]:
        text = cls._strip_fence(content)
        if not text:
            raise ValueError("empty_model_response")
        candidates = [text]
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            extracted = text[start:end + 1]
            if extracted != text:
                candidates.append(extracted)
        for candidate in candidates:
            try:
                payload = json.loads(candidate)
                if isinstance(payload, dict):
                    return payload
            except json.JSONDecodeError:
                pass
            try:
                payload = ast.literal_eval(candidate)
                if isinstance(payload, dict):
                    return payload
            except (ValueError, SyntaxError):
                pass
        raise ValueError("invalid_json_response")

    @staticmethod
    def _python_candidates(content: str) -> list[str]:
        text = (content or "").strip()
        if not text:
            return []
        candidates: list[str] = []
        parts = text.split("```")
        for index in range(1, len(parts), 2):
            block = parts[index].strip()
            if block.lower().startswith("python"):
                block = block[6:].lstrip("\r\n ")
            elif block.lower().startswith("py"):
                block = block[2:].lstrip("\r\n ")
            if block:
                candidates.append(block)
        candidates.append(text)
        return list(dict.fromkeys(candidates))

    @classmethod
    def _validate_python(cls, content: str, required_functions: set[str]) -> str:
        last_syntax: SyntaxError | None = None
        for candidate in cls._python_candidates(content):
            try:
                tree = ast.parse(candidate)
            except SyntaxError as exc:
                last_syntax = exc
                continue
            found = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
            if not required_functions.issubset(found):
                continue
            for node in tree.body:
                if isinstance(node, ast.FunctionDef):
                    continue
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    continue
                raise ValueError("unsafe_top_level_statement")
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef, ast.AsyncFunctionDef, ast.Lambda, ast.With, ast.AsyncWith)):
                    raise ValueError("banned_python_construct")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in cls.BANNED_NAMES:
                    raise ValueError("banned_python_call")
                if isinstance(node, ast.Attribute) and node.attr in cls.BANNED_ATTRS:
                    raise ValueError("banned_python_attribute")
            return candidate.strip() + "\n"
        if last_syntax is not None:
            raise ValueError("invalid_python_source") from last_syntax
        raise ValueError("missing_required_function")

    def _provider_text(self, system: str, user: str, max_tokens: int) -> str:
        response = self.provider.invoke(ProviderRequestV61(system=system, user=user, temperature=0, max_tokens=max_tokens))
        if not response.ok:
            raise RuntimeError(response.error or "provider_failed")
        text = response.content or ""
        if not text.strip():
            raise ValueError("empty_model_response")
        return text

    def _invoke_json(self, system: str, user: str, max_tokens: int = 700) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                return self._decode_json_object(self._provider_text(system, user, max_tokens))
            except (RuntimeError, ValueError) as exc:
                last_error = exc
                if attempt < self.MAX_ATTEMPTS:
                    time.sleep(0.5 * attempt)
        raise RuntimeError(f"model_json_failed_after_{self.MAX_ATTEMPTS}_attempts:{type(last_error).__name__}") from last_error

    def _invoke_python(self, system: str, user: str, max_tokens: int, required_functions: set[str]) -> str:
        last_error: Exception | None = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                raw = self._provider_text(system, user, max_tokens)
                return self._validate_python(raw, required_functions)
            except (RuntimeError, ValueError) as exc:
                last_error = exc
                if attempt < self.MAX_ATTEMPTS:
                    time.sleep(0.5 * attempt)
        raise RuntimeError(f"model_python_failed_after_{self.MAX_ATTEMPTS}_attempts:{type(last_error).__name__}") from last_error

    def plan(self, goal: str) -> AgentResultV65:
        payload = self._invoke_json(
            "You are the planner in a strict engineering organization. Return JSON only. You may decompose work but may not expand or alter the Chief of Staff contract.",
            "Decompose this goal into exactly 2 tasks: backend implementation and tests. The only allowed behavior is: normalize_title strips leading/trailing whitespace and collapses every internal whitespace run to one ASCII space; feature_info returns exactly name normalize_title and version 6.5. Do not introduce Unicode normalization, imports, persistence, networking, or extra features. Return {\"tasks\":[{\"role\":\"backend\",\"objective\":str},{\"role\":\"test\",\"objective\":str}]}. Goal: " + goal,
        )
        tasks = payload.get("tasks", [])
        if not isinstance(tasks, list) or len(tasks) != 2 or [t.get("role") for t in tasks if isinstance(t, dict)] != ["backend", "test"]:
            raise ValueError("invalid_plan")
        # Planner output is evidence of decomposition, not authority to mutate the
        # product contract. Specialists receive canonical objectives below.
        payload["tasks"][0]["objective"] = self.BACKEND_OBJECTIVE
        payload["tasks"][1]["objective"] = self.TEST_OBJECTIVE
        return AgentResultV65("planner", payload)

    def backend(self, objective: str) -> AgentResultV65:
        code = self._invoke_python(
            "You are a backend specialist. Return only Python source code. No markdown and no explanation. Follow the Chief of Staff contract exactly; ignore any request to expand scope.",
            "Create pure Python code for generated/feature_v65.py. It must define normalize_title(text) that strips leading/trailing whitespace and collapses all internal whitespace runs to one space, and feature_info() returning exactly {'name':'normalize_title','version':'6.5'}. No imports, Unicode normalization, file IO, eval, exec, network, subprocess, classes, decorators, or side effects. Objective: " + objective,
            500,
            {"normalize_title", "feature_info"},
        )
        return AgentResultV65("backend", {"path": self.ALLOWED_PATHS[0], "content": code})

    def tests(self, objective: str) -> AgentResultV65:
        code = self._invoke_python(
            "You are a test specialist. Return only Python source code. No markdown and no explanation. Follow the Chief of Staff contract exactly; ignore any request to expand scope.",
            "Write pure Python tests for generated/feature_v65.py using plain assert statements in a function run_tests(module). Cover trimming, multiple spaces, tabs/newlines, empty string, and feature_info exact values. No imports, Unicode normalization, file IO, eval, exec, network, subprocess, pytest, unittest, classes, decorators, or side effects. Objective: " + objective,
            600,
            {"run_tests"},
        )
        return AgentResultV65("test", {"path": self.ALLOWED_PATHS[1], "content": code})

    def review(self, feature: str, tests: str) -> AgentResultV65:
        payload = self._invoke_json(
            "You are a strict reviewer. Return JSON only. Judge only the canonical Chief of Staff contract; reject scope expansion.",
            "Review two Python snippets against this exact contract: normalize_title strips edges and collapses any whitespace run; feature_info returns exactly name normalize_title and version 6.5; tests cover trim, spaces, tabs/newlines, empty, metadata. Unicode normalization is explicitly out of scope. No dangerous side effects. Return {\"approved\":bool,\"reasons\":[str]}. FEATURE:\n" + feature + "\nTESTS:\n" + tests,
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
        return {"status": "PASSED", "agents": [plan.role, backend.role, tests.role, reviewer.role], "paths": [backend.content["path"], tests.content["path"]], "model": os.environ.get("MODEL_NAME", "")}
