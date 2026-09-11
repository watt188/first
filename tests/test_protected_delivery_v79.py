import json
from pathlib import Path

import pytest

from evidence_integrity.v69 import seal
from github_delivery.server_policy_v76 import REQUIRED_CHECKS
from protected_delivery.v79 import ProtectedAutonomousDeliveryV79, ProtectedDeliverySpecV79

BASE_SHA = "a" * 40


def _ruleset():
    return {
        "id": 22864994,
        "name": "main-production-gate",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/heads/main"]}},
        "bypass_actors": [],
        "current_user_can_bypass": "never",
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "pull_request", "parameters": {"required_approving_review_count": 0}},
            {"type": "required_status_checks", "parameters": {
                "strict_required_status_checks_policy": True,
                "required_status_checks": [{"context": name} for name in REQUIRED_CHECKS],
            }},
        ],
    }


class Runner:
    def __init__(self, root: Path, source_sha: str = BASE_SHA):
        self.root = root
        self.source_sha = source_sha

    def run(self, goal: str):
        generated = self.root / "generated"
        artifacts = self.root / "artifacts"
        generated.mkdir(parents=True, exist_ok=True)
        artifacts.mkdir(parents=True, exist_ok=True)
        (generated / "feature_v65.py").write_text("def ready():\n    return True\n", encoding="utf-8")
        (generated / "test_feature_v65.py").write_text("def test_ready():\n    assert True\n", encoding="utf-8")
        (artifacts / "report.json").write_text(json.dumps({"goal": goal}), encoding="utf-8")
        seal(self.root, ["artifacts/report.json", "generated/feature_v65.py", "generated/test_feature_v65.py"], artifacts / "manifest.json", self.source_sha)
        return {"status":"PASSED","pr_ready":True,"source_sha":self.source_sha,"manifest":"artifacts/manifest.json","outputs":["generated/feature_v65.py","generated/test_feature_v65.py"]}


class Gateway:
    def create_branch(self, *, base_sha, branch):
        self.branch = branch
        return {"branch": branch}
    def commit_files(self, *, branch, files, message):
        self.files = files
        return {"head_sha": "b" * 40}
    def create_pull_request(self, *, head, base, title, body):
        return {"url": "https://example.test/pr/1", "head_sha": "b" * 40}


def test_protected_delivery_opens_pr_only_after_policy_and_evidence(tmp_path):
    gateway = Gateway()
    result = ProtectedAutonomousDeliveryV79(tmp_path, Runner(tmp_path), gateway).deliver(ProtectedDeliverySpecV79("ship bounded feature", BASE_SHA), _ruleset())
    assert result["status"] == "PR_OPENED_PROTECTED"
    assert result["evidence_verified"] is True
    assert result["policy"]["strict"] is True
    assert sorted(gateway.files) == ["generated/feature_v65.py", "generated/test_feature_v65.py"]


def test_rejects_stale_autonomous_evidence(tmp_path):
    controller = ProtectedAutonomousDeliveryV79(tmp_path, Runner(tmp_path, "c" * 40), Gateway())
    with pytest.raises(RuntimeError, match="delivery_source_sha_mismatch"):
        controller.deliver(ProtectedDeliverySpecV79("ship bounded feature", BASE_SHA), _ruleset())


def test_rejects_policy_drift_before_runner_executes(tmp_path):
    ruleset = _ruleset()
    ruleset["rules"][-1]["parameters"]["strict_required_status_checks_policy"] = False
    with pytest.raises(ValueError, match="strict_status_checks_disabled"):
        ProtectedAutonomousDeliveryV79(tmp_path, Runner(tmp_path), Gateway()).deliver(ProtectedDeliverySpecV79("ship bounded feature", BASE_SHA), ruleset)


def test_rejects_unexpected_repository():
    with pytest.raises(ValueError, match="unexpected_repository"):
        ProtectedDeliverySpecV79("x", BASE_SHA, "other/repo").validate()
