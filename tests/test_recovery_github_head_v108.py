import io
import urllib.error

import pytest

from protected_delivery.recovery_github_head_v108 import GitHubPRHeadResolverV108

SHA = "a" * 40


class Response:
    def __init__(self, body: bytes, status: int = 200):
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.body


def test_resolves_live_head_and_builds_strict_request():
    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["accept"] = request.get_header("Accept")
        seen["timeout"] = timeout
        return Response((f'{{"head":{{"sha":"{SHA}"}}}}').encode())

    resolver = GitHubPRHeadResolverV108(
        repository_full_name="watt188/first",
        token="secret-token",
        timeout=3,
        opener=opener,
    )
    assert resolver.resolve(52) == SHA
    assert seen["url"] == "https://api.github.com/repos/watt188/first/pulls/52"
    assert seen["auth"] == "Bearer secret-token"
    assert seen["accept"] == "application/vnd.github+json"
    assert seen["timeout"] == 3.0


def test_http_and_transport_failures_fail_closed():
    def opener(request, timeout):
        raise urllib.error.URLError("offline")

    resolver = GitHubPRHeadResolverV108(repository_full_name="watt188/first", opener=opener)
    with pytest.raises(RuntimeError, match="authoritative_github_head_unavailable"):
        resolver(52)


def test_invalid_payload_and_head_fail_closed():
    bad_json = GitHubPRHeadResolverV108(
        repository_full_name="watt188/first", opener=lambda req, timeout: Response(b"not-json")
    )
    with pytest.raises(RuntimeError, match="invalid_authoritative_github_response"):
        bad_json(1)

    bad_sha = GitHubPRHeadResolverV108(
        repository_full_name="watt188/first",
        opener=lambda req, timeout: Response(b'{"head":{"sha":"BAD"}}'),
    )
    with pytest.raises(RuntimeError, match="invalid_authoritative_github_head_sha"):
        bad_sha(1)


def test_constructor_and_pr_validation():
    with pytest.raises(ValueError, match="invalid_github_repository"):
        GitHubPRHeadResolverV108(repository_full_name="bad")
    with pytest.raises(ValueError, match="invalid_github_api_base"):
        GitHubPRHeadResolverV108(repository_full_name="watt188/first", api_base="http://github.local")
    with pytest.raises(ValueError, match="invalid_github_token"):
        GitHubPRHeadResolverV108(repository_full_name="watt188/first", token=" ")
    with pytest.raises(ValueError, match="invalid_pr_number"):
        GitHubPRHeadResolverV108(repository_full_name="watt188/first", opener=lambda *a, **k: None)(0)
