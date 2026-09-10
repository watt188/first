import json
import os
import tempfile
from pathlib import Path

from real_provider.pep_v66 import UnifiedPEPV66


def test_role_aware_and_hashes():
    with tempfile.TemporaryDirectory() as tmp:
        pep = UnifiedPEPV66(tmp)
        role_paths = {
            "backend": {"generated/feature.py"},
            "test": {"generated/test_feature.py"},
        }
        denied = pep.write_text("test", "generated/feature.py", "x", role_paths)
        assert denied == {"status": "DENIED", "reason": "path_not_allowlisted"}
        ok = pep.write_text("backend", "generated/feature.py", "hello", role_paths)
        assert ok["status"] == "WRITTEN"
        assert ok["before_sha256"] is None
        assert len(ok["after_sha256"]) == 64
        assert ok["bytes"] == 5


def test_symlink_parent_is_denied():
    if not hasattr(os, "symlink"):
        return
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
        root = Path(tmp)
        (root / "generated").mkdir()
        link = root / "generated" / "escape"
        os.symlink(outside, link)
        pep = UnifiedPEPV66(root)
        result = pep.write_text(
            "backend",
            "generated/escape/pwn.py",
            "bad",
            {"backend": {"generated/escape/pwn.py"}},
        )
        assert result["status"] == "DENIED"
        assert result["reason"] in {"symlink_parent", "path_escape"}
        assert not (Path(outside) / "pwn.py").exists()


def test_transaction_preflight_prevents_partial_write():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "generated").mkdir()
        first = root / "generated" / "feature.py"
        first.write_text("original", encoding="utf-8")
        pep = UnifiedPEPV66(root)
        result = pep.transaction_write_texts(
            [
                {"role": "backend", "path": "generated/feature.py", "content": "new"},
                {"role": "test", "path": "generated/feature.py", "content": "wrong-role"},
            ],
            {
                "backend": {"generated/feature.py"},
                "test": {"generated/test_feature.py"},
            },
        )
        assert result["status"] == "DENIED"
        assert first.read_text(encoding="utf-8") == "original"


def test_transaction_commit_and_audit():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pep = UnifiedPEPV66(root)
        result = pep.transaction_write_texts(
            [
                {"role": "backend", "path": "generated/feature.py", "content": "feature"},
                {"role": "test", "path": "generated/test_feature.py", "content": "tests"},
            ],
            {
                "backend": {"generated/feature.py"},
                "test": {"generated/test_feature.py"},
            },
        )
        assert result["status"] == "COMMITTED"
        assert set(result["manifest_sha256"]) == {"generated/feature.py", "generated/test_feature.py"}
        assert all(len(value) == 64 for value in result["manifest_sha256"].values())
        audit = root / "artifacts" / "pep-v66-audit.jsonl"
        lines = audit.read_text(encoding="utf-8").splitlines()
        assert len(lines) >= 3
        records = [json.loads(line) for line in lines]
        assert records[-1]["status"] == "COMMITTED"


def test_absolute_and_parent_escape_denied():
    with tempfile.TemporaryDirectory() as tmp:
        pep = UnifiedPEPV66(tmp)
        role_paths = {"backend": {"../escape.py", "/tmp/escape.py"}}
        assert pep.write_text("backend", "../escape.py", "x", role_paths)["reason"] == "invalid_path"
        assert pep.write_text("backend", "/tmp/escape.py", "x", role_paths)["reason"] == "invalid_path"
