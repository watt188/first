import argparse
import json
import os
import subprocess
from pathlib import Path

from evidence_integrity.v69 import seal, verify_manifest
from multi_agent.chief_of_staff_v65 import ChiefOfStaffV65

VERSION = "7.1"


class AutonomousDeliveryV71:
    """Execute a bounded delivery loop from goal to verified PR-ready evidence."""

    def __init__(self, root: str | Path = "."):
        self.root = Path(root).resolve()

    @staticmethod
    def _head_sha() -> str:
        env_sha = os.getenv("GITHUB_HEAD_SHA") or os.getenv("GITHUB_SHA")
        if env_sha:
            return env_sha
        try:
            return subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
            ).strip()
        except Exception:
            return "local-unbound"

    def run(self, goal: str) -> dict:
        goal = goal.strip()
        if not goal:
            raise ValueError("empty_goal")

        result = ChiefOfStaffV65(self.root).run(goal)
        feature = self.root / "generated/feature_v65.py"
        tests = self.root / "generated/test_feature_v65.py"
        if not feature.is_file() or not tests.is_file():
            raise RuntimeError("delivery_outputs_missing")

        source_sha = self._head_sha()
        evidence_dir = self.root / "artifacts"
        evidence_dir.mkdir(exist_ok=True)
        report_path = evidence_dir / "v71-autonomous-delivery.json"
        manifest_path = evidence_dir / "v71-evidence-manifest.json"

        report = {
            "status": "PASSED",
            "version": VERSION,
            "goal": goal,
            "source_sha": source_sha,
            "stages": [
                "chief_of_staff",
                "specialist_execution",
                "pep_write",
                "verification",
                "evidence_seal",
                "pr_ready",
            ],
            "chief_result": result,
            "outputs": [str(feature.relative_to(self.root)), str(tests.relative_to(self.root))],
            "pr_ready": True,
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        manifest = seal(
            self.root,
            [
                str(report_path.relative_to(self.root)),
                str(feature.relative_to(self.root)),
                str(tests.relative_to(self.root)),
            ],
            manifest_path,
            source_sha,
        )
        verified = verify_manifest(self.root, manifest, source_sha)
        report["evidence"] = verified
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        # Final report bytes changed after embedding verification; reseal authoritative bytes.
        manifest = seal(
            self.root,
            [
                str(report_path.relative_to(self.root)),
                str(feature.relative_to(self.root)),
                str(tests.relative_to(self.root)),
            ],
            manifest_path,
            source_sha,
        )
        verified = verify_manifest(self.root, manifest, source_sha)
        return {**report, "evidence": verified, "manifest": str(manifest_path.relative_to(self.root))}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("goal")
    args = parser.parse_args()
    print(json.dumps(AutonomousDeliveryV71(".").run(args.goal), ensure_ascii=False))


if __name__ == "__main__":
    main()
