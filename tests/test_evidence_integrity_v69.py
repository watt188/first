import json
from pathlib import Path

import pytest

from evidence_integrity.v69 import build_manifest, seal, verify_manifest


def _write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_build_and_verify_manifest(tmp_path):
    _write(tmp_path, "artifacts/a.json", '{"ok":true}')
    _write(tmp_path, "generated/x.py", "x = 1\n")
    manifest = build_manifest(tmp_path, ["generated/x.py", "artifacts/a.json"], "abcdef1234567890")
    result = verify_manifest(tmp_path, manifest, "abcdef1234567890")
    assert result["status"] == "PASSED"
    assert result["entry_count"] == 2
    assert len(result["chain_head"]) == 64
    assert len(result["manifest_sha256"]) == 64


def test_tampered_file_is_rejected(tmp_path):
    _write(tmp_path, "a.txt", "original")
    manifest = build_manifest(tmp_path, ["a.txt"], "abcdef1234567890")
    _write(tmp_path, "a.txt", "tampered")
    with pytest.raises(ValueError, match="evidence_digest_mismatch"):
        verify_manifest(tmp_path, manifest, "abcdef1234567890")


def test_tampered_manifest_entry_is_rejected(tmp_path):
    _write(tmp_path, "a.txt", "original")
    manifest = build_manifest(tmp_path, ["a.txt"], "abcdef1234567890")
    manifest["entries"][0]["bytes"] += 1
    with pytest.raises(ValueError):
        verify_manifest(tmp_path, manifest, "abcdef1234567890")


def test_wrong_source_sha_is_rejected(tmp_path):
    _write(tmp_path, "a.txt", "original")
    manifest = build_manifest(tmp_path, ["a.txt"], "abcdef1234567890")
    with pytest.raises(ValueError, match="source_sha_mismatch"):
        verify_manifest(tmp_path, manifest, "ffffffffffffffff")


def test_path_escape_is_rejected(tmp_path):
    _write(tmp_path, "a.txt", "original")
    with pytest.raises(ValueError, match="unsafe_evidence_path"):
        build_manifest(tmp_path, ["../a.txt"], "abcdef1234567890")


def test_seal_writes_verifiable_manifest(tmp_path):
    _write(tmp_path, "a.txt", "hello")
    out = tmp_path / "artifacts" / "manifest.json"
    seal(tmp_path, ["a.txt"], out, "abcdef1234567890")
    manifest = json.loads(out.read_text(encoding="utf-8"))
    assert verify_manifest(tmp_path, manifest, "abcdef1234567890")["status"] == "PASSED"
