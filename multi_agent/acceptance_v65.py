import ast
import json
from pathlib import Path

from multi_agent.chief_of_staff_v65 import ChiefOfStaffV65
from multi_agent.execution_v68 import IsolatedExecutionV68

BANNED = ("eval(", "exec(", "subprocess", "socket", "requests", "urllib", "open(")


def _assert_test_contract(tests: str) -> int:
    tree = ast.parse(tests)
    run_tests = next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run_tests"), None)
    if run_tests is None:
        raise AssertionError("missing_run_tests")
    assert_count = sum(isinstance(node, ast.Assert) for node in ast.walk(run_tests))
    if assert_count < 5:
        raise AssertionError("insufficient_generated_test_coverage")
    required_literals = ("hello world", "normalize_title", "6.5")
    if not all(value in tests for value in required_literals):
        raise AssertionError("generated_test_contract_literals_missing")
    if "\\t" not in tests or "\\n" not in tests:
        raise AssertionError("generated_test_whitespace_case_missing")
    return assert_count


def main() -> None:
    goal = "Ship a safe title-normalization utility with independent tests and reviewer approval."
    result = ChiefOfStaffV65(".").run(goal)

    feature_path = Path("generated/feature_v65.py")
    test_path = Path("generated/test_feature_v65.py")
    feature = feature_path.read_text(encoding="utf-8")
    tests = test_path.read_text(encoding="utf-8")

    ast.parse(feature)
    ast.parse(tests)
    generated_assert_count = _assert_test_contract(tests)
    combined = feature + "\n" + tests
    if any(token in combined for token in BANNED):
        raise RuntimeError("banned_symbol")

    isolated = IsolatedExecutionV68(timeout_seconds=5, memory_mb=128, cpu_seconds=2)
    execution = isolated.run_feature_tests(feature, tests)
    if execution.get("status") != "PASSED":
        raise RuntimeError("isolated_execution_failed:" + execution.get("error", "unknown"))

    evidence = {
        **result,
        "goal": goal,
        "acceptance": "PASSED",
        "execution_version": "6.8",
        "execution": execution,
        "generated_assert_count": generated_assert_count,
        "feature_bytes": len(feature.encode("utf-8")),
        "test_bytes": len(tests.encode("utf-8")),
    }
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/v65-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == "__main__":
    main()
