import json
import os
from pathlib import Path

VERSION = "11.1"
_PROVIDER_WORKFLOWS = {
    "v61-real-provider",
    "v62-provider-smoke",
    "v63-strict-provider",
    "v67-provider-resilience",
    "v74-provider-reliability",
}


def _validate_event(event: dict):
    run = event.get("workflow_run") if isinstance(event, dict) else None
    if not isinstance(run, dict):
        raise ValueError("worker_event_missing_workflow_run")
    conclusion = run.get("conclusion")
    if conclusion not in {"failure", "timed_out"}:
        return None
    name = run.get("name")
    if name not in _PROVIDER_WORKFLOWS:
        return None
    attempt = run.get("run_attempt", 1)
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt != 1:
        return None
    prs = run.get("pull_requests") or []
    if len(prs) != 1 or not isinstance(prs[0], dict):
        return None
    pr_number = prs[0].get("number")
    run_id = run.get("id")
    head_sha = run.get("head_sha")
    if not isinstance(pr_number, int) or pr_number < 1:
        raise ValueError("worker_invalid_pr_number")
    if not isinstance(run_id, int) or run_id < 1:
        raise ValueError("worker_invalid_run_id")
    if not isinstance(head_sha, str) or len(head_sha) != 40 or any(c not in "0123456789abcdef" for c in head_sha):
        raise ValueError("worker_invalid_head_sha")
    return {
        "workflow": name,
        "conclusion": conclusion,
        "pr_number": pr_number,
        "run_id": run_id,
        "head_sha": head_sha,
        "run_attempt": attempt,
    }


def observe_worker_event(*, event, repository_full_name: str) -> dict:
    """Turn one completed workflow_run into a safe recovery candidate.

    V11.1 deliberately performs no GitHub mutation. GitHub's rerun-failed-jobs
    endpoint does not accept an idempotency key, so treating it as a V10.9-ready
    durable gateway would violate the production readiness contract. The worker
    therefore watches continuously and emits a bounded recovery candidate until
    a durable idempotency/lookup transport is deployed.
    """
    target = _validate_event(event)
    if target is None:
        return {"worker_version": VERSION, "status": "NO_ACTION"}
    return {
        "worker_version": VERSION,
        "status": "RECOVERY_CANDIDATE",
        "repository": repository_full_name,
        "mutation_performed": False,
        "requires_durable_gateway": True,
        **target,
    }


def main():
    with open(os.environ["GITHUB_EVENT_PATH"], "r", encoding="utf-8") as handle:
        event = json.load(handle)
    result = observe_worker_event(
        event=event,
        repository_full_name=os.environ["GITHUB_REPOSITORY"],
    )
    output = Path(os.environ.get("RUNNER_TEMP", ".")) / "v111-recovery-worker-report.json"
    output.write_text(json.dumps(result, sort_keys=True, indent=2), encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"status={result['status']}\n")
            handle.write(f"report={output}\n")


if __name__ == "__main__":
    main()
