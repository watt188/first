import json
from pathlib import Path

import pytest

from protected_delivery.recovery_worker_v113 import (
    GitHubFailureEvidenceProbeV113,
    _decision_for,
    _derive_secret,
)

SHA = "a" * 40


def target(workflow="v61-real-provider", conclusion="failure"):
    return {
        "workflow": workflow,
        "conclusion": conclusion,
        "pr_number": 59,
        "run_id": 123,
        "head_sha": SHA,
        "run_attempt": 1,
    }


def test_real_transient_evidence_allows_retry():
    decision = _decision_for(target(), "request failed with http_503 from provider")
    assert decision.category == "provider_transient"
    assert decision.action == "rerun_failed_job"
    assert decision.retryable is True
    assert decision.requires_human is False


def test_plain_test_failure_is_not_fabricated_as_transient():
    decision = _decision_for(target(), "AssertionError: expected DONE but got FAILED")
    assert decision.action == "escalate_human"
    assert decision.retryable is False


def test_permission_failure_fails_closed():
    decision = _decision_for(target(), "Resource not accessible by integration")
    assert decision.category == "permission"
    assert decision.action == "escalate_human"


def test_secret_is_scoped_to_v113():
    assert len(_derive_secret("token")) == 32
    assert _derive_secret("token") == _derive_secret("token")
    assert _derive_secret("token") != _derive_secret("other")
    with pytest.raises(ValueError, match="worker_token_missing"):
        _derive_secret("")


class Response:
    def __init__(self, status, body):
        self.status = status
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]


class Opener:
    def __init__(self):
        self.urls = []

    def __call__(self, request, timeout=0):
        self.urls.append(request.full_url)
        if "/actions/runs/123/jobs" in request.full_url:
            body = json.dumps(
                {
                    "jobs": [
                        {"id": 7, "name": "provider", "conclusion": "failure"},
                        {"id": 8, "name": "ok", "conclusion": "success"},
                    ]
                }
            ).encode()
            return Response(200, body)
        if "/actions/jobs/7/logs" in request.full_url:
            return Response(200, b"upstream request ended with http_503")
        raise AssertionError(request.full_url)


def test_probe_collects_only_failed_job_logs():
    opener = Opener()
    probe = GitHubFailureEvidenceProbeV113(
        repository_full_name="watt188/first", token="token", opener=opener
    )
    evidence = probe.collect(123)
    assert "http_503" in evidence
    assert "job=provider" in evidence
    assert all("/actions/jobs/8/logs" not in url for url in opener.urls)


def test_probe_rejects_failure_without_failed_jobs():
    class NoFailure:
        def __call__(self, request, timeout=0):
            return Response(200, json.dumps({"jobs": []}).encode())

    probe = GitHubFailureEvidenceProbeV113(
        repository_full_name="watt188/first", token="token", opener=NoFailure()
    )
    with pytest.raises(RuntimeError, match="failure_evidence_no_failed_jobs"):
        probe.collect(123)
