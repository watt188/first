import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


class IsolatedExecutionV68:
    """Run generated Python in a bounded child process with a clean environment."""

    def __init__(self, timeout_seconds=5, memory_mb=128, cpu_seconds=2):
        self.timeout_seconds = max(1, min(int(timeout_seconds), 30))
        self.memory_mb = max(32, min(int(memory_mb), 512))
        self.cpu_seconds = max(1, min(int(cpu_seconds), 10))

    @staticmethod
    def _clean_env():
        return {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def _preexec(self):
        try:
            import resource
        except ImportError:
            return None

        memory = self.memory_mb * 1024 * 1024
        cpu = self.cpu_seconds

        def apply_limits():
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))

        return apply_limits

    def run_feature_tests(self, feature_source: str, test_source: str):
        runner = r'''import importlib.util
import json
from pathlib import Path


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("module_load_failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

feature = load(Path("feature.py"), "feature")
tests = load(Path("tests.py"), "tests")
tests.run_tests(feature)
checks = {
    "normalize": feature.normalize_title("  hello\t\n world  ") == "hello world",
    "blank": feature.normalize_title("   ") == "",
    "metadata": feature.feature_info() == {"name": "normalize_title", "version": "6.5"},
}
if not all(checks.values()):
    raise AssertionError("acceptance_contract_failed")
print(json.dumps({"status": "PASSED", "checks": checks}, sort_keys=True))
'''
        with tempfile.TemporaryDirectory(prefix="v68-isolated-") as td:
            root = Path(td)
            (root / "feature.py").write_text(feature_source, encoding="utf-8")
            (root / "tests.py").write_text(test_source, encoding="utf-8")
            (root / "runner.py").write_text(runner, encoding="utf-8")
            try:
                proc = subprocess.run(
                    [sys.executable, "-I", "-S", "runner.py"],
                    cwd=root,
                    env=self._clean_env(),
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    preexec_fn=self._preexec() if os.name == "posix" else None,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return {"status": "FAILED", "error": "execution_timeout"}

        if proc.returncode != 0:
            return {
                "status": "FAILED",
                "error": "execution_failed",
                "returncode": proc.returncode,
            }
        try:
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            return {"status": "FAILED", "error": "invalid_runner_output"}
        return {
            "status": payload.get("status"),
            "checks": payload.get("checks", {}),
            "isolation": {
                "python_isolated_mode": True,
                "site_disabled": True,
                "clean_environment": True,
                "working_directory_isolated": True,
                "timeout_seconds": self.timeout_seconds,
                "memory_mb": self.memory_mb,
                "cpu_seconds": self.cpu_seconds,
            },
        }
