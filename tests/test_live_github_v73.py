import pytest
from github_delivery.live_v73 import PullRequestEventV73, receipt_from_event


def _event():
    return {
        "number": 14,
        "repository": {"full_name": "watt188/first"},
        "pull_request": {
            "base": {"sha": "9" * 40},
            "head": {"sha": "a" * 40, "ref": "release/v7.3-live-github-mutation"},
        },
    }


def test_live_receipt_validates_real_shape():
    result = receipt_from_event(_event())
    assert result["status"] == "PASSED"
    assert result["version"] == "7.3"
    assert result["pr_number"] == 14


def test_head_must_differ_from_base():
    event = _event()
    event["pull_request"]["head"]["sha"] = event["pull_request"]["base"]["sha"]
    with pytest.raises(ValueError, match="head_equals_base"):
        receipt_from_event(event)


def test_branch_is_bounded_to_v73_release():
    event = _event()
    event["pull_request"]["head"]["ref"] = "main"
    with pytest.raises(ValueError, match="unexpected_head_branch"):
        receipt_from_event(event)


def test_invalid_sha_fails_closed():
    with pytest.raises(ValueError, match="invalid_head_sha"):
        PullRequestEventV73("watt188/first", 1, "9" * 40, "bad", "release/v7.3-x").validate()
