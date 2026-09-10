import argparse
import json
import os
from pathlib import Path

from evidence_integrity.v69 import seal, verify_manifest
from multi_agent import acceptance_v65
from real_provider.openai_compatible_v61 import OpenAICompatibleProviderV61

VERSION = "7.0"
EVIDENCE_FILES = (
    "artifacts/v65-evidence.json",
    "generated/feature_v65.py",
    "generated/test_feature_v65.py",
)
MANIFEST_PATH = "artifacts/v70-production-manifest.json"
REPORT_PATH = "artifacts/v70-production-report.json"


def _source_sha() -> str:
    value = (
        os.getenv("PRODUCTION_SOURCE_SHA")
        or os.getenv("GITHUB_HEAD_SHA")
        or os.getenv("GITHUB_SHA")
        or ""
    ).strip()
    if len(value) < 7:
        raise RuntimeError("production_source_sha_missing")
    return value


class ProductionRuntimeV70:
    """Single production entry point that converges V6.7-V6.9 controls."""

    def __init__(self, root: str | Path = "."):
        self.root = Path(root).resolve()
        self.provider = OpenAICompatibleProviderV61()

    def status(self) -> dict:
        return {
            "version": VERSION,
            "status": "READY" if self.provider.configured() else "DEGRADED",
            "provider": self.provider.health(),
            "controls": {
                "provider_resilience": "6.7",
                "pep": "6.6",
                "execution_isolation": "6.8",
                "evidence_integrity": "6.9",
                "chief_of_staff": "6.5",
            },
        }

    def preflight(self) -> dict:
        checks = {
            "provider_configured": self.provider.configured(),
            "root_exists": self.root.is_dir(),
            "source_sha_bound": False,
        }
        try:
            source_sha = _source_sha()
            checks["source_sha_bound"] = True
        except RuntimeError:
            source_sha = None
        ok = all(checks.values())
        return {
            "version": VERSION,
            "status": "PASSED" if ok else "FAILED",
            "checks": checks,
            "source_sha": source_sha,
        }

    def verify(self) -> dict:
        source_sha = _source_sha()
        manifest_path = self.root / MANIFEST_PATH
        if not manifest_path.is_file():
            raise RuntimeError("production_manifest_missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        result = verify_manifest(self.root, manifest, source_sha)
        return {"version": VERSION, "status": "PASSED", "integrity": result}

    def deliver(self) -> dict:
        preflight = self.preflight()
        if preflight["status"] != "PASSED":
            raise RuntimeError("production_preflight_failed")

        previous_cwd = Path.cwd()
        try:
            os.chdir(self.root)
            acceptance_v65.main()
        finally:
            os.chdir(previous_cwd)

        missing = [path for path in EVIDENCE_FILES if not (self.root / path).is_file()]
        if missing:
            raise RuntimeError("production_evidence_missing")

        source_sha = _source_sha()
        manifest = seal(self.root, EVIDENCE_FILES, self.root / MANIFEST_PATH, source_sha)
        integrity = verify_manifest(self.root, manifest, source_sha)

        report = {
            "version": VERSION,
            "status": "PASSED",
            "source_sha": source_sha,
            "release_contract": {
                "chief_of_staff": "PASSED",
                "provider_resilience": "BOUND",
                "pep": "BOUND",
                "execution_isolation": "BOUND",
                "evidence_integrity": "PASSED",
            },
            "manifest_sha256": integrity["manifest_sha256"],
            "chain_head": integrity["chain_head"],
            "entry_count": integrity["entry_count"],
        }
        out = self.root / REPORT_PATH
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("status", "preflight", "deliver", "verify"))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    runtime = ProductionRuntimeV70(args.root)
    if args.command == "status":
        result = runtime.status()
    elif args.command == "preflight":
        result = runtime.preflight()
    elif args.command == "verify":
        result = runtime.verify()
    else:
        result = runtime.deliver()
    print(json.dumps(result, ensure_ascii=False))
    if result.get("status") not in ("PASSED", "READY", "DEGRADED"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
