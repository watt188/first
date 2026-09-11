import base64
import io
import json
import urllib.error

import pytest

from protected_delivery.recovery_durable_gateway_v112 import GitHubDurableMutationGatewayV112


class Response:
    def __init__(self, status, payload=None):
        self.status = status
        self.payload = payload or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode() if self.payload else b""


class FakeGitHub:
    def __init__(self):
        self.files = {}
        self.reruns = 0
        self.seq = 0

    def opener(self, req, timeout=10.0):
        url = req.full_url
        method = req.get_method()
        if "/contents/" in url:
            path = url.split("/contents/", 1)[1].split("?", 1)[0]
            if method == "GET":
                item = self.files.get(path)
                if item is None:
                    raise urllib.error.HTTPError(url, 404, "not found", None, io.BytesIO(b"{}"))
                return Response(200, {"content": item["content"], "sha": item["sha"]})
            if method == "PUT":
                body = json.loads(req.data)
                current = self.files.get(path)
                expected = body.get("sha")
                if current is not None and expected != current["sha"]:
                    raise urllib.error.HTTPError(url, 409, "conflict", None, io.BytesIO(b"{}"))
                if current is None and expected is not None:
                    raise urllib.error.HTTPError(url, 409, "conflict", None, io.BytesIO(b"{}"))
                self.seq += 1
                sha = f"sha-{self.seq}"
                self.files[path] = {"content": body["content"], "sha": sha}
                return Response(200 if current else 201, {"content": {"sha": sha}})
        if url.endswith("/rerun-failed-jobs") and method == "POST":
            self.reruns += 1
            return Response(201)
        raise AssertionError((method, url))

    def record(self):
        assert len(self.files) == 1
        item = next(iter(self.files.values()))
        return json.loads(base64.b64decode(item["content"]).decode())


def gateway(fake):
    return GitHubDurableMutationGatewayV112(
        repository_full_name="watt188/first",
        token="token",
        run_id=123,
        opener=fake.opener,
    )


def test_first_execution_prepares_mutates_and_commits():
    fake = FakeGitHub()
    g = gateway(fake)
    receipt = g.execute_recovery(56, "a" * 40, "rerun_failed_job", 123, "idem-1")
    assert receipt["accepted"] is True
    assert receipt["physical_exactly_once"] is False
    assert fake.reruns == 1
    record = fake.record()
    assert record["state"] == "COMMITTED"
    assert record["receipt"] == receipt


def test_committed_lookup_prevents_second_dispatch():
    fake = FakeGitHub()
    g = gateway(fake)
    first = g.execute_recovery(56, "a" * 40, "rerun_failed_job", 123, "idem-1")
    second = g.execute_recovery(56, "a" * 40, "rerun_failed_job", 123, "idem-1")
    assert second == first
    assert fake.reruns == 1


def test_prepared_crash_window_fails_closed_without_resend():
    fake = FakeGitHub()
    g = gateway(fake)
    prepared = {
        "version": "11.2",
        "state": "PREPARED",
        "repository": "watt188/first",
        "pr_number": 56,
        "expected_head_sha": "a" * 40,
        "action": "rerun_failed_job",
        "run_id": 123,
        "idempotency_key_hash": g._key_hash("idem-1"),
    }
    g._write_record("idem-1", prepared)
    with pytest.raises(RuntimeError, match="durable_mutation_ambiguous_prepared"):
        g.execute_recovery(56, "a" * 40, "rerun_failed_job", 123, "idem-1")
    assert fake.reruns == 0


def test_capabilities_are_explicit_about_boundary():
    caps = gateway(FakeGitHub()).capabilities()
    assert caps["durable_idempotency"] is True
    assert caps["lookup_by_idempotency_key"] is True
    assert caps["physical_exactly_once"] is False
    assert caps["ambiguity_policy"] == "fail_closed_on_prepared"


def test_wrong_run_identity_and_action_fail_before_mutation():
    fake = FakeGitHub()
    g = gateway(fake)
    with pytest.raises(RuntimeError, match="run_identity_mismatch"):
        g.execute_recovery(56, "a" * 40, "rerun_failed_job", 999, "idem-1")
    with pytest.raises(RuntimeError, match="action_not_supported"):
        g.execute_recovery(56, "a" * 40, "autonomous_rework", 123, "idem-2")
    assert fake.reruns == 0
