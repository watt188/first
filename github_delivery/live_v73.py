import json
import re
from dataclasses import dataclass

VERSION = "7.3"


@dataclass(frozen=True)
class PullRequestEventV73:
    repository: str
    pr_number: int
    base_sha: str
    head_sha: str
    head_branch: str

    def validate(self) -> dict:
        if not re.fullmatch(r"[0-9a-f]{40}", self.base_sha):
            raise ValueError("invalid_base_sha")
        if not re.fullmatch(r"[0-9a-f]{40}", self.head_sha):
            raise ValueError("invalid_head_sha")
        if self.base_sha == self.head_sha:
            raise ValueError("head_equals_base")
        if self.pr_number < 1:
            raise ValueError("invalid_pr_number")
        if not self.repository or "/" not in self.repository:
            raise ValueError("invalid_repository")
        if not self.head_branch.startswith("release/v7.3-"):
            raise ValueError("unexpected_head_branch")
        return {
            "status": "PASSED",
            "version": VERSION,
            "repository": self.repository,
            "pr_number": self.pr_number,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "head_branch": self.head_branch,
        }


def receipt_from_event(payload: dict) -> dict:
    pr = payload.get("pull_request") or {}
    repo = payload.get("repository") or {}
    event = PullRequestEventV73(
        repository=repo.get("full_name", ""),
        pr_number=int(payload.get("number") or 0),
        base_sha=((pr.get("base") or {}).get("sha") or ""),
        head_sha=((pr.get("head") or {}).get("sha") or ""),
        head_branch=((pr.get("head") or {}).get("ref") or ""),
    )
    return event.validate()


def main(path: str) -> None:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    print(json.dumps(receipt_from_event(payload), ensure_ascii=False))
