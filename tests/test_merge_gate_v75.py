import pytest

from github_delivery.merge_gate_v75 import authorize_merge


HEAD = "a" * 40


def good_payload():
    return {
        "pr_number": 99,
        "head_sha": HEAD,
        "expected_head_sha": HEAD,
        "mergeable": True,
        "draft": False,
        "checks": {"ci": "success", "v74-provider-reliability": "success"},
        "required_checks": ["ci", "v74-provider-reliability"],
        "evidence_status": "PASSED",
        "evidence_source_sha": HEAD,
    }


def test_authorizes_only_fully_bound_green_candidate():
    result = authorize_merge(good_payload())
    assert result["status"] == "AUTHORIZED"
    assert result["head_sha"] == HEAD
    assert result["required_check_count"] == 2
    assert result["evidence_bound"] is True


def test_head_move_fails_closed():
    payload = good_payload()
    payload["expected_head_sha"] = "b" * 40
    with pytest.raises(RuntimeError, match="head_moved"):
        authorize_merge(payload)


def test_evidence_must_bind_exact_head():
    payload = good_payload()
    payload["evidence_source_sha"] = "b" * 40
    with pytest.raises(RuntimeError, match="evidence_head_mismatch"):
        authorize_merge(payload)


def test_missing_required_check_fails_closed():
    payload = good_payload()
    del payload["checks"]["ci"]
    with pytest.raises(RuntimeError, match="missing_required_checks"):
        authorize_merge(payload)


def test_non_green_required_check_fails_closed():
    payload = good_payload()
    payload["checks"]["ci"] = "failure"
    with pytest.raises(RuntimeError, match="required_checks_not_green"):
        authorize_merge(payload)


def test_draft_or_unmergeable_or_bad_evidence_fails():
    for key, value, error in (
        ("draft", True, "draft_pr"),
        ("mergeable", False, "pr_not_mergeable"),
        ("evidence_status", "FAILED", "evidence_not_passed"),
    ):
        payload = good_payload()
        payload[key] = value
        with pytest.raises(RuntimeError, match=error):
            authorize_merge(payload)
