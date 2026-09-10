import json
from pathlib import Path

import pytest

from autonomous_delivery.v71 import AutonomousDeliveryV71
from evidence_integrity.v69 import verify_manifest


class FakeChief:
    def __init__(self, root):
        self.root = Path(root)

    def run(self, goal):
        generated = self.root / "generated"
        generated.mkdir(exist_ok=True)
        (generated / "feature_v65.py").write_text("def normalize_title(text):\n    return ' '.join(text.split())\n", encoding="utf-8")
        (generated / "test_feature_v65.py").write_text("def run_tests(module):\n    assert module.normalize_title('  a  b ') == 'a b'\n", encoding="utf-8")
        return {"status": "PASSED", "goal": goal, "pep_version": "6.6"}


def test_empty_goal_rejected(tmp_path):
    runtime = AutonomousDeliveryV71(tmp_path)
    with pytest.raises(ValueError, match="empty_goal"):
        runtime.run("   ")


def test_delivery_produces_commit_bound_verified_evidence(tmp_path, monkeypatch):
    import autonomous_delivery.v71 as module

    monkeypatch.setattr(module, "ChiefOfStaffV65", FakeChief)
    monkeypatch.setenv("GITHUB_HEAD_SHA", "abc1234567890")

    result = AutonomousDeliveryV71(tmp_path).run("ship bounded utility")
    assert result["status"] == "PASSED"
    assert result["version"] == "7.1"
    assert result["source_sha"] == "abc1234567890"
    assert result["pr_ready"] is True
    assert result["evidence"]["status"] == "PASSED"
    assert result["evidence"]["source_sha"] == "abc1234567890"
    assert result["evidence"]["entry_count"] == 3

    manifest = tmp_path / result["manifest"]
    assert manifest.is_file()
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["source_sha"] == "abc1234567890"
    paths = [entry["path"] for entry in payload["entries"]]
    assert paths == sorted(paths)
    assert "artifacts/v71-autonomous-delivery.json" in paths
    assert verify_manifest(tmp_path, payload, "abc1234567890")["status"] == "PASSED"

    persisted_report = json.loads((tmp_path / "artifacts/v71-autonomous-delivery.json").read_text(encoding="utf-8"))
    assert persisted_report["evidence_status"] == "SEALED_AND_VERIFIED"
    assert persisted_report["evidence_manifest"] == "artifacts/v71-evidence-manifest.json"
    assert "evidence" not in persisted_report


def test_missing_outputs_fail_closed(tmp_path, monkeypatch):
    import autonomous_delivery.v71 as module

    class EmptyChief:
        def __init__(self, root):
            pass
        def run(self, goal):
            return {"status": "PASSED"}

    monkeypatch.setattr(module, "ChiefOfStaffV65", EmptyChief)
    with pytest.raises(RuntimeError, match="delivery_outputs_missing"):
        AutonomousDeliveryV71(tmp_path).run("ship")
