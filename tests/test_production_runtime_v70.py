import json
from pathlib import Path

import pytest

from evidence_integrity.v69 import seal
from production_runtime.v70 import EVIDENCE_FILES, MANIFEST_PATH, ProductionRuntimeV70


def test_status_exposes_converged_controls(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    status = ProductionRuntimeV70(".").status()
    assert status["version"] == "7.0"
    assert status["controls"]["provider_resilience"] == "6.7"
    assert status["controls"]["execution_isolation"] == "6.8"
    assert status["controls"]["evidence_integrity"] == "6.9"


def test_preflight_requires_provider_and_source_sha(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_API_KEY", "x")
    monkeypatch.setenv("MODEL_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("MODEL_NAME", "model")
    monkeypatch.delenv("PRODUCTION_SOURCE_SHA", raising=False)
    monkeypatch.delenv("GITHUB_HEAD_SHA", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    result = ProductionRuntimeV70(tmp_path).preflight()
    assert result["status"] == "FAILED"
    assert result["checks"]["provider_configured"] is True
    assert result["checks"]["source_sha_bound"] is False


def test_verify_rejects_wrong_head(monkeypatch, tmp_path):
    for rel in EVIDENCE_FILES:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("proof\n", encoding="utf-8")
    seal(tmp_path, EVIDENCE_FILES, tmp_path / MANIFEST_PATH, "abcdef1234567890")
    monkeypatch.setenv("PRODUCTION_SOURCE_SHA", "ffffffffffffffff")
    with pytest.raises(ValueError, match="source_sha_mismatch"):
        ProductionRuntimeV70(tmp_path).verify()


def test_verify_accepts_bound_manifest(monkeypatch, tmp_path):
    for rel in EVIDENCE_FILES:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("proof\n", encoding="utf-8")
    source_sha = "abcdef1234567890"
    seal(tmp_path, EVIDENCE_FILES, tmp_path / MANIFEST_PATH, source_sha)
    monkeypatch.setenv("PRODUCTION_SOURCE_SHA", source_sha)
    result = ProductionRuntimeV70(tmp_path).verify()
    assert result["status"] == "PASSED"
    assert result["integrity"]["entry_count"] == 3
    assert len(result["integrity"]["manifest_sha256"]) == 64
