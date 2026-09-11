import json
import re
import urllib.error
import urllib.request

VERSION = "10.8"
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


class GitHubPRHeadResolverV108:
    """Authoritative PR-head resolver backed by GitHub's pull-request API."""

    def __init__(
        self,
        *,
        repository_full_name: str,
        token: str | None = None,
        api_base: str = "https://api.github.com",
        timeout: float = 10.0,
        opener=None,
    ):
        if not isinstance(repository_full_name, str) or not _REPO.fullmatch(repository_full_name):
            raise ValueError("invalid_github_repository")
        if token is not None and (not isinstance(token, str) or not token.strip()):
            raise ValueError("invalid_github_token")
        if not isinstance(api_base, str) or not api_base.startswith("https://"):
            raise ValueError("invalid_github_api_base")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("invalid_github_timeout")
        self.repository_full_name = repository_full_name
        self.token = token.strip() if isinstance(token, str) else None
        self.api_base = api_base.rstrip("/")
        self.timeout = float(timeout)
        self.opener = opener or urllib.request.urlopen

    def __call__(self, pr_number: int) -> str:
        return self.resolve(pr_number)

    def resolve(self, pr_number: int) -> str:
        if not isinstance(pr_number, int) or isinstance(pr_number, bool) or pr_number < 1:
            raise ValueError("invalid_pr_number")
        url = f"{self.api_base}/repos/{self.repository_full_name}/pulls/{pr_number}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "first-recovery-control-plane/10.8",
        }
        if self.token is not None:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with self.opener(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise RuntimeError("authoritative_github_head_unavailable")
                raw = response.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            raise RuntimeError("authoritative_github_head_unavailable") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
            sha = payload["head"]["sha"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RuntimeError("invalid_authoritative_github_response") from exc
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise RuntimeError("invalid_authoritative_github_head_sha")
        return sha
