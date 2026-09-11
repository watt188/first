import copy
import pytest
from github_delivery.server_policy_v76 import REQUIRED_CHECKS, validate_ruleset


def payload():
    return {
        "id": 22864994,
        "name": "main-production-gate",
        "enforcement": "active",
        "conditions": {"ref_name": {"exclude": [], "include": ["refs/heads/main"]}},
        "bypass_actors": [],
        "current_user_can_bypass": "never",
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "pull_request", "parameters": {"required_approving_review_count": 0}},
            {"type": "required_status_checks", "parameters": {
                "strict_required_status_checks_policy": True,
                "required_status_checks": [{"context": name} for name in REQUIRED_CHECKS],
            }},
        ],
    }


def test_valid_server_policy_passes():
    result = validate_ruleset(payload())
    assert result["status"] == "PASSED"
    assert result["strict"] is True
    assert result["bypass"] is False
    assert result["required_checks"] == 17


def test_strict_sync_is_required():
    data = payload()
    data["rules"][3]["parameters"]["strict_required_status_checks_policy"] = False
    with pytest.raises(ValueError, match="strict_status_checks_disabled"):
        validate_ruleset(data)


def test_missing_required_check_fails_closed():
    data = payload()
    data["rules"][3]["parameters"]["required_status_checks"].pop()
    with pytest.raises(ValueError, match="missing_required_checks"):
        validate_ruleset(data)


def test_bypass_actor_fails_closed():
    data = payload()
    data["bypass_actors"] = [{"actor_id": 1}]
    with pytest.raises(ValueError, match="bypass_not_allowed"):
        validate_ruleset(data)


def test_only_main_may_be_targeted():
    data = payload()
    data["conditions"]["ref_name"]["include"] = ["~ALL"]
    with pytest.raises(ValueError, match="unexpected_target"):
        validate_ruleset(data)
