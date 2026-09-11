import dataclasses
import json
import re
from pathlib import Path
from typing import Protocol

from evidence_integrity.v69 import verify_manifest
from github_delivery.v72 import DeliverySpec, GitHubDeliveryBridgeV72
from github_delivery.server_policy_v76 import validate_ruleset

VERSION = "8.1"


class AutonomousRunner(Protocol):
    def run(self, goal: str) -> dict: ...


@dataclasses.dataclass(frozen=True)
class ProtectedDeliverySpecV81:
    goal: str
    base_sha: str
    expected_repository: str = "watt188/first"

    def validate(self) -> None:
        if not self.goal.strip():
            raise ValueError("empty_goal")
        if not re.fullmatch(r"[0-9a-f]{40}", self.base_sha):
            raise ValueError("invalid_base_sha")
        if self.expected_repository != "watt188/first":
            raise ValueError("unexpected_repository")


class ProtectedAutonomousDeliveryV81:
    """Compose autonomous generation, evidence verification, policy and PR opening.

    This controller never merges or bypasses GitHub. It opens a PR only after
    autonomous output is PR-ready, exact-base evidence verifies, and the live
    server ruleset snapshot satisfies the fail-closed production contract.
    """

    def __init__(self, root: str | Path, runner: AutonomousRunner, gateway):
        self.root = Path(root).resolve()
        self.runner = runner
        self.bridge = GitHubDeliveryBridgeV72(gateway)

    def deliver(self, spec: ProtectedDeliverySpecV81, ruleset: dict) -> dict:
        spec.validate()
        policy = validate_ruleset(ruleset)
        if policy.get("status") != "PASSED":
            raise RuntimeError("server_policy_not_satisfied")

        result = self.runner.run(spec.goal)
        if result.get("status") != "PASSED" or not result.get("pr_ready"):
            raise RuntimeError("autonomous_delivery_not_ready")
        if result.get("source_sha") != spec.base_sha:
            raise RuntimeError("delivery_source_sha_mismatch")

        manifest_rel = result.get("manifest") or result.get("evidence_manifest")
        if not manifest_rel:
            raise RuntimeError("missing_evidence_manifest")
        manifest_path = (self.root / manifest_rel).resolve()
        try:
            manifest_path.relative_to(self.root)
        except ValueError as exc:
            raise RuntimeError("unsafe_manifest_path") from exc
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        verify_manifest(self.root, manifest, spec.base_sha)

        files: dict[str, str] = {}
        for rel in result.get("outputs", []):
            path = (self.root / rel).resolve()
            try:
                path.relative_to(self.root)
            except ValueError as exc:
                raise RuntimeError("unsafe_output_path") from exc
            if not path.is_file():
                raise RuntimeError("missing_delivery_output")
            files[rel] = path.read_text(encoding="utf-8")
        if not files:
            raise RuntimeError("no_delivery_outputs")

        opened = self.bridge.deliver(
            DeliverySpec(goal=spec.goal, base_sha=spec.base_sha, base_branch="main"),
            files,
        )
        if opened.get("status") != "PR_OPENED":
            raise RuntimeError("github_delivery_failed")
        return {
            "status": "PR_OPENED_PROTECTED",
            "version": VERSION,
            "base_sha": spec.base_sha,
            "policy": policy,
            "evidence_verified": True,
            "delivery": opened,
        }
