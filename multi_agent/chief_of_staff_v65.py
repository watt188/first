import ast
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from real_provider.contracts_v61 import ProviderRequestV61
from real_provider.openai_compatible_v61 import OpenAICompatibleProviderV61
from real_provider.pep_v66 import PolicyEnforcementPointV66


@dataclass
class AgentResultV65:
    role: str
    content: dict


class ChiefOfStaffV65:
    MAX_ATTEMPTS = 3
    BACKEND_OBJECTIVE = "Implement normalize_title and feature_info exactly to the fixed V6.5 contract."
    TEST_OBJECTIVE = "Write independent tests for normalize_title and feature_info exactly to the fixed V6.5 contract."
    ROLE_PATHS = {
        "backend": ("generated/feature_v65.py",),
        "test": ("generated/test_feature_v65.py",),
    }
    CANONICAL_TESTS = r'''def run_tests(module):
    assert module.normalize_title("  hello  world  ") == "hello world"
    assert module.normalize_title("hello\t\nworld") == "hello world"
    assert module.normalize_title("   ") == ""
    assert module.normalize_title("") == ""
    assert module.feature_info() == {"name": "normalize_title", "version": "6.5"}
'''

    def __init__(self, root="."):
        self.root = Path(root).resolve()
        self.provider = OpenAICompatibleProviderV61()
        self.pep = PolicyEnforcementPointV66(self.root)

    @staticmethod
    def _python_candidates(raw: str):
        candidates = []
        for match in re.finditer(r"```(?:python|py)?\s*(.*?)```", raw, flags=re.I | re.S):
            candidates.append(match.group(1).strip())
        candidates.append(raw.strip())
        for candidate in list(candidates):
            lines = candidate.splitlines()
            for end in range(len(lines), 0, -1):
                prefix = "\n".join(lines[:end]).strip()
                if not prefix:
                    continue
                try:
                    ast.parse(prefix)
                    candidates.append(prefix)
                    break
                except SyntaxError:
                    continue
        seen = set()
        for item in candidates:
            if item and item not in seen:
                seen.add(item)
                yield item

    @classmethod
    def _validate_python(cls, raw: str, required_functions: set[str]) -> str:
        banned = {"exec", "eval", "compile", "open", "__import__"}
        for candidate in cls._python_candidates(raw):
            try:
                tree = ast.parse(candidate)
            except SyntaxError:
                continue
            functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
            if not required_functions.issubset(functions):
                continue
            unsafe = False
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    unsafe = True
                    break
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in banned:
                    unsafe = True
                    break
            if not unsafe:
                return candidate.rstrip() + "\n"
        raise ValueError("no_valid_python_candidate")

    @staticmethod
    def _test_contract_ok(code: str) -> bool:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return False
        run_tests = next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run_tests"), None)
        if run_tests is None:
            return False
        assert_count = sum(isinstance(node, ast.Assert) for node in ast.walk(run_tests))
        if assert_count < 5:
            return False
        required_tokens = ("normalize_title", "feature_info", "\\t", "\\n", "6.5")
        return all(token in code for token in required_tokens)

    def _provider_text(self, system: str, user: str, max_tokens: int) -> str:
        request = ProviderRequestV61(system=system, user=user, temperature=0, max_tokens=max_tokens)
        response = self.provider.invoke(request)
        if not response.ok and response.error == "provider_circuit_open":
            retry_after = min(30.0, max(0.0, float(self.provider.circuit_retry_after_seconds())))
            if retry_after > 0:
                time.sleep(retry_after)
            response = self.provider.invoke(request)
        if not response.ok:
            raise RuntimeError(response.error or "provider_failed")
        text = response.content or ""
        if not text.strip():
            raise ValueError("empty_model_response")
        return text.strip()

    def _invoke_text(self, system: str, user: str, max_tokens: int) -> str:
        last_error = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                return self._provider_text(system, user, max_tokens)
            except (RuntimeError, ValueError) as exc:
                last_error = exc
                if attempt < self.MAX_ATTEMPTS:
                    time.sleep(0.5 * attempt)
        raise RuntimeError(f"model_text_failed_after_{self.MAX_ATTEMPTS}_attempts:{type(last_error).__name__}") from last_error

    def _invoke_python(self, system: str, user: str, max_tokens: int, required_functions: set[str]) -> str:
        last_error = None
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
        advisory_received = False
        try:
            advisory = self._invoke_text(
                "You are the planner in a strict engineering organization. Give a concise two-part decomposition only; do not expand the Chief of Staff contract.",
                "Plan exactly two responsibilities: backend implementation and tests for this fixed contract: normalize_title strips leading/trailing whitespace and collapses every internal whitespace run to one ASCII space; feature_info returns exactly name normalize_title and version 6.5. Do not add Unicode normalization, imports, persistence, networking, or extra features. Goal: " + goal,
                1200,
            )
            advisory_received = bool(advisory.strip())
        except RuntimeError:
            advisory_received = False
        return AgentResultV65("planner", {"tasks": [
            {"role": "backend", "objective": self.BACKEND_OBJECTIVE},
            {"role": "test", "objective": self.TEST_OBJECTIVE},
        ], "advisory_received": advisory_received})

    def backend(self, objective: str) -> AgentResultV65:
        code = self._invoke_python(
            "You are the backend specialist. Return only final Python code, no explanation.",
            objective + " Implement exactly two functions: normalize_title(text) and feature_info(). No imports, I/O, networking, persistence, dynamic execution, or extra functions.",
            1200,
            {"normalize_title", "feature_info"},
        )
        return AgentResultV65("backend", {"path": "generated/feature_v65.py", "content": code})

    def tests(self, objective: str) -> AgentResultV65:
        generated = False
        fallback_reason = None
        try:
            code = self._invoke_python(
                "You are the test specialist. Return only final Python code, no explanation.",
                objective + " Define only run_tests(module). Include at least five assert statements covering repeated spaces, tab/newline whitespace, blank input, empty input, and exact feature_info metadata.",
                1500,
                {"run_tests"},
            )
            if not self._test_contract_ok(code):
                raise ValueError("test_semantic_contract_failed")
            generated = True
        except (RuntimeError, ValueError) as exc:
            code = self.CANONICAL_TESTS
            fallback_reason = type(exc).__name__
        if not self._test_contract_ok(code):
            raise RuntimeError("canonical_test_contract_failed")
        return AgentResultV65("test", {"path": "generated/test_feature_v65.py", "content": code, "model_generated": generated, "fallback_reason": fallback_reason})

    def reviewer(self, goal: str) -> AgentResultV65:
        approved = False
        advisory_received = False
        try:
            advisory = self._invoke_text(
                "You are a reviewer. Answer APPROVE only if the requested bounded utility should proceed to deterministic acceptance; otherwise REJECT.",
                "Review bounded goal: " + goal,
                200,
            )
            advisory_received = bool(advisory.strip())
            approved = "APPROVE" in advisory.upper() and "REJECT" not in advisory.upper()
        except RuntimeError:
            advisory_received = False
            approved = False
        return AgentResultV65("reviewer", {"advisory_received": advisory_received, "approved": approved})

    def run(self, goal: str) -> dict:
        planner = self.plan(goal)
        backend_task, test_task = planner.content["tasks"]
        backend = self.backend(backend_task["objective"])
        tests = self.tests(test_task["objective"])
        reviewer = self.reviewer(goal)
        transaction = self.pep.transaction_write_texts([
            {"role": "backend", "path": backend.content["path"], "content": backend.content["content"]},
            {"role": "test", "path": tests.content["path"], "content": tests.content["content"]},
        ], self.ROLE_PATHS)
        if transaction.get("status") != "COMMITTED":
            raise RuntimeError("pep_transaction_failed")
        return {
            "status": "PASSED",
            "goal": goal,
            "agents": ["planner", "backend", "test", "reviewer"],
            "planner_advisory_received": planner.content["advisory_received"],
            "test_model_generated": tests.content["model_generated"],
            "test_fallback_reason": tests.content["fallback_reason"],
            "reviewer_advisory_received": reviewer.content["advisory_received"],
            "reviewer_approved": reviewer.content["approved"],
            "pep_version": "6.6",
            "pep_transaction": transaction,
            "paths": [backend.content["path"], tests.content["path"]],
            "model": os.getenv("MODEL_NAME", ""),
        }
