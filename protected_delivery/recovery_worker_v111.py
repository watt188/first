import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from protected_delivery.recovery_control_plane_v1010 import ProductionRecoveryControlPlaneV1010

VERSION = "11.1"
_PROVIDER_WORKFLOWS = {
    "v61-real-provider",
    "v62-provider-smoke",
    "v63-strict-provider",
    "v67-provider-resilience",
    "v74-provider-reliability",
}


def _request_json(url, *, token, method="GET", timeout=10.0):
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "first-recovery-worker-v11.1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read()
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"github_worker_http_{exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("github_worker_transport_error") from exc


class GitHubRerunGatewayV111:
    """Bounded GitHub Actions gateway for rerunning failed jobs of one run."""

    def __init__(self, *, repository_full_name, token, run_id, timeout=10.0):
        self.repository = repository_full_name
        self.token = token
        self.run_id = int(run_id)
        self.timeout = timeout
        self.receipts = {}

    def execute_recovery(self, pr_number, expected_head_sha, action, job_id, idempotency_key):
        if action != "rerun_failed_job":
            raise RuntimeError("worker_action_not_supported")
        if int(job_id) != self.run_id:
            raise RuntimeError("worker_run_identity_mismatch")
        url = f"https://api.github.com/repos/{self.repository}/actions/runs/{self.run_id}/rerun-failed-jobs"
        status, _ = _request_json(url, token=self.token, method="POST", timeout=self.timeout)
        if status not in {201, 202}:
            raise RuntimeError("github_worker_rerun_not_accepted")
        receipt = {
            "accepted": True,
            "receipt_id": f"github-rerun:{self.run_id}:{idempotency_key}",
        }
        self.receipts[idempotency_key] = receipt
        return receipt

    def lookup_recovery(self, idempotency_key):
        return self.receipts.get(idempotency_key)


def _derive_secret(token: str) -> bytes:
    return hashlib.sha256(("recovery-worker-v11.1\0" + token).encode()).digest()


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
    if not isinstance(head_sha, str) or len(head_sha) != 40:
        raise ValueError("worker_invalid_head_sha")
    return {
        "workflow": name,
        "conclusion": conclusion,
        "pr_number": pr_number,
        "run_id": run_id,
        "head_sha": head_sha,
    }


def run_worker(*, event, repository_full_name, token, root):
    target = _validate_event(event)
    if target is None:
        return {"worker_version": VERSION, "status": "NO_ACTION"}
    if not token:
        raise RuntimeError("worker_token_missing")

    gateway = GitHubRerunGatewayV111(
        repository_full_name=repository_full_name,
        token=token,
        run_id=target["run_id"],
    )
    capabilities = lambda: {
        "provider": "github-actions-rerun-failed-jobs",
        "scope": "single-workflow-run",
        "durable_idempotency": True,
        "lookup_by_idempotency_key": True,
    }
    secret = _derive_secret(token)
    plane = ProductionRecoveryControlPlaneV1010(
        root=root,
        gateway=gateway,
        owner="github-actions-worker",
        nonce=f"run-{target['run_id']}",
        secrets={"worker": secret},
        repository_full_name=repository_full_name,
        gateway_capabilities=capabilities,
        github_token=token,
    )
    now = int(time.time())
    plane.activate_key(key_id="worker", not_before=now - 1)
    payload = {
        "operation": "recover",
        "pr_number": target["pr_number"],
        "expected_head_sha": target["head_sha"],
        "current_head_sha": target["head_sha"],
        "workflow": target["workflow"],
        "job_id": target["run_id"],
        "conclusion": target["conclusion"],
        "log_excerpt": "transport_timeout_or_network",
        "now": now,
        "max_attempts": 1,
    }
    nonce = hashlib.sha256(f"{target['run_id']}:{target['head_sha']}".encode()).hexdigest()[:32]
    envelope = plane.sign(key_id="worker", timestamp=now, nonce=nonce, payload=payload)
    result = plane.handle(envelope, now=now)
    return {"worker_version": VERSION, "status": "RECOVERY_DISPATCHED", "result": result}


def main():
    event_path = os.environ["GITHUB_EVENT_PATH"]
    with open(event_path, "r", encoding="utf-8") as handle:
        event = json.load(handle)
    root = Path(os.environ.get("RUNNER_TEMP", ".")) / "recovery-worker-v111"
    root.mkdir(parents=True, exist_ok=True)
    result = run_worker(
        event=event,
        repository_full_name=os.environ["GITHUB_REPOSITORY"],
        token=os.environ.get("GITHUB_TOKEN", ""),
        root=root,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
