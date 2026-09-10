import ast
import json
import os
import sys
import tempfile
from pathlib import Path

from real_provider.agent_v61 import RealProviderAgentV61
from real_provider.openai_compatible_v61 import OpenAICompatibleProviderV61


def verify_generated(path: Path) -> dict:
    if not path.exists():
        return {"ok": False, "reason": "file_missing"}
    text = path.read_text(encoding="utf-8")
    if len(text) > 4000:
        return {"ok": False, "reason": "file_too_large"}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {"ok": False, "reason": "syntax_error"}
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    if len(funcs) != 1 or funcs[0].name != "runtime_status":
        return {"ok": False, "reason": "unexpected_shape"}
    banned = {"eval", "exec", "compile", "__import__", "open"}
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    if banned & used:
        return {"ok": False, "reason": "banned_symbol"}
    ns = {}
    exec(compile(tree, str(path), "exec"), {"__builtins__": {}}, ns)
    result = ns["runtime_status"]()
    if result != {"status": "Ready"}:
        return {"ok": False, "reason": "functional_mismatch", "result": result}
    return {"ok": True, "sha256_input_bytes": len(text.encode("utf-8"))}


def main() -> int:
    provider = OpenAICompatibleProviderV61()
    if not provider.configured():
        print(json.dumps({"status": "FAILED", "reason": "provider_not_configured"}))
        return 10

    with tempfile.TemporaryDirectory(prefix="chief-of-staff-v64-") as td:
        root = Path(td)
        rel = "generated/runtime_status.py"
        agent = RealProviderAgentV61(root, provider=provider)
        goal = (
            "Create exactly one Python file. It must define only this zero-argument function: "
            "runtime_status(). The function must return exactly {'status': 'Ready'}. "
            "No imports, no file/network access, no comments, no markdown, no extra functions."
        )
        out = agent.execute_writer("backend", goal, [rel])
        if out.get("status") != "DONE":
            print(json.dumps({"status": "FAILED", "reason": "agent_failed", "detail": out}, default=str))
            return 20

        verification = verify_generated(root / rel)
        if not verification.get("ok"):
            print(json.dumps({"status": "FAILED", "reason": "verification_failed", "verification": verification}))
            return 30

        artifact_dir = Path(os.getenv("GITHUB_WORKSPACE", td)) / "runtime" / "v64"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / "generated_runtime_status.py"
        artifact_path.write_text((root / rel).read_text(encoding="utf-8"), encoding="utf-8")
        report = {
            "status": "PASSED",
            "model": out.get("provider", {}).get("model", ""),
            "latency_ms": out.get("provider", {}).get("latency_ms", 0),
            "applied": out.get("applied", []),
            "verification": verification,
            "artifact": str(artifact_path),
        }
        (artifact_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
