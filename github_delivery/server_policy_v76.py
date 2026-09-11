import json
import urllib.request

REQUIRED_CHECKS = (
    "ci", "v6-production-baseline", "v61-real-provider", "v62-provider-smoke",
    "v63-strict-provider", "v64-real-codegen", "v65-multi-agent", "v66-pep-hardening",
    "v67-provider-resilience", "v68-execution-isolation", "v69-evidence-integrity",
    "v70-production-convergence", "v71-autonomous-delivery", "v72-github-delivery-bridge",
    "v73-live-github-mutation", "v74-provider-reliability", "v75-merge-authorization",
)


def validate_ruleset(payload: dict) -> dict:
    if payload.get("name") != "main-production-gate":
        raise ValueError("unexpected_ruleset_name")
    if payload.get("enforcement") != "active":
        raise ValueError("ruleset_not_active")
    refs = ((payload.get("conditions") or {}).get("ref_name") or {}).get("include") or []
    if refs != ["refs/heads/main"]:
        raise ValueError("unexpected_target")
    if payload.get("bypass_actors"):
        raise ValueError("bypass_not_allowed")
    if payload.get("current_user_can_bypass") not in (None, "never"):
        raise ValueError("current_user_can_bypass")

    rules = {rule.get("type"): rule for rule in payload.get("rules") or []}
    for required in ("deletion", "non_fast_forward", "pull_request", "required_status_checks"):
        if required not in rules:
            raise ValueError("missing_rule:" + required)

    pr = rules["pull_request"].get("parameters") or {}
    if int(pr.get("required_approving_review_count") or 0) != 0:
        raise ValueError("unexpected_review_requirement")

    status = rules["required_status_checks"].get("parameters") or {}
    if status.get("strict_required_status_checks_policy") is not True:
        raise ValueError("strict_status_checks_disabled")
    contexts = tuple(item.get("context") for item in status.get("required_status_checks") or [])
    missing = [name for name in REQUIRED_CHECKS if name not in contexts]
    if missing:
        raise ValueError("missing_required_checks:" + ",".join(missing))

    return {
        "status": "PASSED",
        "ruleset_id": payload.get("id"),
        "name": payload.get("name"),
        "target": "refs/heads/main",
        "required_checks": len(REQUIRED_CHECKS),
        "strict": True,
        "bypass": False,
    }


def fetch_and_validate(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return validate_ruleset(payload)
