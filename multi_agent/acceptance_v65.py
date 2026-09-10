import ast
import importlib.util
import json
from pathlib import Path

from multi_agent.chief_of_staff_v65 import ChiefOfStaffV65

BANNED = ("eval(", "exec(", "subprocess", "socket", "requests", "urllib", "open(")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("module_load_failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    goal = "Ship a safe title-normalization utility with independent tests and reviewer approval."
    result = ChiefOfStaffV65(".").run(goal)

    feature_path = Path("generated/feature_v65.py")
    test_path = Path("generated/test_feature_v65.py")
    feature = feature_path.read_text(encoding="utf-8")
    tests = test_path.read_text(encoding="utf-8")

    ast.parse(feature)
    ast.parse(tests)
    combined = feature + "\n" + tests
    if any(token in combined for token in BANNED):
        raise RuntimeError("banned_symbol")

    module = _load(feature_path, "feature_v65")
    test_module = _load(test_path, "test_feature_v65")
    test_module.run_tests(module)

    if module.normalize_title("  hello\t\n world  ") != "hello world":
        raise AssertionError("acceptance_normalize_failed")
    if module.feature_info() != {"name": "normalize_title", "version": "6.5"}:
        raise AssertionError("acceptance_metadata_failed")

    evidence = {
        **result,
        "goal": goal,
        "acceptance": "PASSED",
        "feature_bytes": len(feature.encode("utf-8")),
        "test_bytes": len(tests.encode("utf-8")),
    }
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/v65-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == "__main__":
    main()
