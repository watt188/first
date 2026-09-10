import hashlib

import pytest

from github_delivery.v72 import DeliverySpec, GitHubDeliveryBridgeV72


class FakeGateway:
    def __init__(self):
        self.calls = []
        self.head = "b" * 40

    def create_branch(self, *, base_sha, branch):
        self.calls.append(("branch", base_sha, branch))
        return {"branch": branch}

    def commit_files(self, *, branch, files, message):
        self.calls.append(("commit", branch, dict(files), message))
        return {"head_sha": self.head}

    def create_pull_request(self, *, head, base, title, body):
        self.calls.append(("pr", head, base, title, body))
        return {"url": "https://github.test/example/pr/1", "head_sha": self.head}


def test_bridge_orders_branch_commit_pr_and_binds_head():
    gateway = FakeGateway()
    bridge = GitHubDeliveryBridgeV72(gateway)
    base_sha = "a" * 40
    files = {"generated/a.py": "x = 1\n", "artifacts/evidence.json": "{}\n"}
    result = bridge.deliver(DeliverySpec("Ship safe utility", base_sha), files)

    assert [call[0] for call in gateway.calls] == ["branch", "commit", "pr"]
    assert result["status"] == "PR_OPENED"
    assert result["base_sha"] == base_sha
    assert result["head_sha"] == "b" * 40
    assert result["pr_url"].endswith("/1")
    manifest = {item["path"]: item for item in result["file_manifest"]}
    assert manifest["generated/a.py"]["sha256"] == hashlib.sha256(b"x = 1\n").hexdigest()


def test_invalid_base_sha_rejected_before_mutation():
    gateway = FakeGateway()
    with pytest.raises(ValueError, match="invalid_base_sha"):
        GitHubDeliveryBridgeV72(gateway).deliver(DeliverySpec("ship", "bad"), {"a.txt": "x"})
    assert gateway.calls == []


def test_unsafe_path_rejected_before_mutation():
    gateway = FakeGateway()
    with pytest.raises(ValueError, match="unsafe_file_path"):
        GitHubDeliveryBridgeV72(gateway).deliver(DeliverySpec("ship", "a" * 40), {"../x": "x"})
    assert gateway.calls == []


def test_branch_mismatch_fails_closed_before_commit():
    gateway = FakeGateway()
    gateway.create_branch = lambda **kwargs: {"branch": "wrong"}
    with pytest.raises(RuntimeError, match="branch_creation_mismatch"):
        GitHubDeliveryBridgeV72(gateway).deliver(DeliverySpec("ship", "a" * 40), {"a.txt": "x"})


def test_invalid_commit_head_fails_closed_before_pr():
    gateway = FakeGateway()
    gateway.commit_files = lambda **kwargs: {"head_sha": "bad"}
    with pytest.raises(RuntimeError, match="invalid_commit_head"):
        GitHubDeliveryBridgeV72(gateway).deliver(DeliverySpec("ship", "a" * 40), {"a.txt": "x"})


def test_branch_name_is_deterministic():
    assert GitHubDeliveryBridgeV72.branch_for("Ship Safe Utility", "a" * 40) == "auto/ship-safe-utility-aaaaaaaa"
