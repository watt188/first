import dataclasses
import hashlib
import json
import re
from typing import Protocol

VERSION = "7.2"


class GitHubGateway(Protocol):
    def create_branch(self, *, base_sha: str, branch: str) -> dict: ...
    def commit_files(self, *, branch: str, files: dict[str, str], message: str) -> dict: ...
    def create_pull_request(self, *, head: str, base: str, title: str, body: str) -> dict: ...


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return value[:48] or "delivery"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclasses.dataclass(frozen=True)
class DeliverySpec:
    goal: str
    base_sha: str
    base_branch: str = "main"

    def validate(self) -> None:
        if not self.goal.strip():
            raise ValueError("empty_goal")
        if not re.fullmatch(r"[0-9a-f]{40}", self.base_sha):
            raise ValueError("invalid_base_sha")
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", self.base_branch):
            raise ValueError("invalid_base_branch")


class GitHubDeliveryBridgeV72:
    """Convert one validated delivery bundle into branch -> commit -> PR actions.

    The bridge is transport-agnostic. A concrete GitHub gateway owns network mutation.
    Exact operation ordering and returned head binding are verified fail-closed here.
    """

    def __init__(self, gateway: GitHubGateway):
        self.gateway = gateway

    @staticmethod
    def branch_for(goal: str, base_sha: str) -> str:
        return f"auto/{_slug(goal)}-{base_sha[:8]}"

    def deliver(self, spec: DeliverySpec, files: dict[str, str]) -> dict:
        spec.validate()
        if not files:
            raise ValueError("no_files")
        normalized: dict[str, str] = {}
        for path, content in files.items():
            if path.startswith("/") or ".." in path.split("/") or not path.strip():
                raise ValueError("unsafe_file_path")
            if not isinstance(content, str):
                raise TypeError("non_text_file")
            normalized[path] = content

        branch = self.branch_for(spec.goal, spec.base_sha)
        created = self.gateway.create_branch(base_sha=spec.base_sha, branch=branch)
        if created.get("branch") != branch:
            raise RuntimeError("branch_creation_mismatch")

        commit_message = f"Autonomous delivery: {spec.goal.strip()}"
        committed = self.gateway.commit_files(branch=branch, files=normalized, message=commit_message)
        head_sha = committed.get("head_sha", "")
        if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
            raise RuntimeError("invalid_commit_head")

        file_manifest = [
            {"path": path, "sha256": _sha256_text(normalized[path]), "bytes": len(normalized[path].encode("utf-8"))}
            for path in sorted(normalized)
        ]
        body_payload = {
            "bridge_version": VERSION,
            "goal": spec.goal.strip(),
            "base_sha": spec.base_sha,
            "head_sha": head_sha,
            "files": file_manifest,
        }
        body = "Autonomous delivery evidence:\n\n```json\n" + json.dumps(body_payload, sort_keys=True, indent=2) + "\n```"
        pr = self.gateway.create_pull_request(
            head=branch,
            base=spec.base_branch,
            title=f"Autonomous Delivery: {spec.goal.strip()}",
            body=body,
        )
        if not pr.get("url") or pr.get("head_sha") not in (None, head_sha):
            raise RuntimeError("pr_creation_mismatch")

        return {
            "status": "PR_OPENED",
            "version": VERSION,
            "branch": branch,
            "base_sha": spec.base_sha,
            "head_sha": head_sha,
            "pr_url": pr["url"],
            "file_manifest": file_manifest,
        }
