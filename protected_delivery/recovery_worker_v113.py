import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from protected_delivery.recovery_control_plane_v1010 import ProductionRecoveryControlPlaneV1010
from protected_delivery.recovery_durable_gateway_v112 import GitHubDurableMutationGatewayV112
from protected_delivery.recovery_v82 import AutonomousRecoveryV82, FailureSignal
from protected_delivery.recovery_worker_v111 import _validate_event

VERSION = "11.3"
_MAX_JOBS = 10
_MAX_LOG_BYTES = 262144


def _derive_secret(token: str) -> bytes:
    if not isinstance(token, str) or not token:
        raise ValueError("worker_token_missing")
    return hashlib.sha256(("recovery-worker-v11.3\0" + token).encode()).digest()


class GitHubFailureEvidenceProbeV113:
    """Read real failed-job evidence for one workflow run before mutation."""

    def __init__(self, *, repository_full_name: str, token: str, timeout: float = 10.0, opener=None):
        if not isinstance(repository_full_name, str) or repository_full_name.count("/") != 1:
            raise ValueError("invalid_repository_full_name")
        if not isinstance(token, str) or not token:
            raise ValueError("github_token_missing")
        self.repository = repository_full_name
        self.token = token
        self.timeout = float(timeout)
        self.opener = opener or urllib.request.urlopen

    def _request_bytes(self, url: str):
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "first-recovery-worker-v11.3",
            },
        )
        try:
            with self.opener(req, timeout=self.timeout) as response:
                return response.status, response.read(_MAX_LOG_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"failure_evidence_http_{exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError("failure_evidence_transport_error") from exc

    def collect(self, run_id: int) -> str:
        if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id < 1:
            raise ValueError("invalid_run_id")
        jobs_url = f"https://api.github.com/repos/{self.repository}/actions/runs/{run_id}/jobs?per_page={_MAX_JOBS}"
        status, raw = self._request_bytes(jobs_url)
        if status != 200:
            raise RuntimeError("failure_evidence_jobs_unavailable")
        try:
            payload = json.loads(raw.decode())
        except Exception as exc:
            raise RuntimeError("failure_evidence_jobs_invalid") from exc
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs, list):
            raise RuntimeError("failure_evidence_jobs_invalid")

        chunks = []
        failed = 0
        for job in jobs[:_MAX_JOBS]:
            if not isinstance(job, dict) or job.get("conclusion") not in {"failure", "timed_out"}:
                continue
            failed += 1
            job_id = job.get("id")
            name = job.get("name", "unknown")
            if not isinstance(job_id, int) or job_id < 1:
                raise RuntimeError("failure_evidence_job_identity_invalid")
            log_url = f"https://api.github.com/repos/{self.repository}/actions/jobs/{job_id}/logs"
            log_status, log_raw = self._request_bytes(log_url)
            if log_status != 200:
                raise RuntimeError("failure_evidence_logs_unavailable")
            if len(log_raw) > _MAX_LOG_BYTES:
                log_raw = log_raw[:_MAX_LOG_BYTES]
            text = log_raw.decode("utf-8", errors="replace")
            chunks.append(f"job={name}\n{text}")

        if failed == 0:
            raise RuntimeError("failure_evidence_no_failed_jobs")
        excerpt = "\n\n".join(chunks)
        return excerpt[-_MAX_LOG_BYTES:]


def _decision_for(target: dict, evidence: str):
    signal = FailureSignal(
        workflow=target["workflow"],
        conclusion=target["conclusion"],
        log_excerpt=evidence,
    )
    return AutonomousRecoveryV82.next_action(signal, attempts=0, max_attempts=1)


def run_worker(*, event: dict, repository_full_name: str, token: str, root, journal_branch="recovery-state-v112", probe=None) -> dict:
    target = _validate_event(event)
    if target is None:
        return {"worker_version": VERSION, "status": "NO_ACTION", "reason": "event_not_eligible"}
    if not token:
        raise RuntimeError("worker_token_missing")

    evidence_probe = probe or GitHubFailureEvidenceProbeV113(
        repository_full_name=repository_full_name,
        token=token,
    )
    evidence = evidence_probe.collect(target["run_id"])
    decision = _decision_for(target, evidence)
    if decision.action != "rerun_failed_job" or not decision.retryable or decision.requires_human:
        return {
            "worker_version": VERSION,
            "status": "NO_ACTION",
            "reason": "failure_not_safely_retryable",
            "category": decision.category,
            "action": decision.action,
            "run_id": target["run_id"],
            "pr_number": target["pr_number"],
        }

    gateway = GitHubDurableMutationGatewayV112(
        repository_full_name=repository_full_name,
        token=token,
        run_id=target["run_id"],
        journal_branch=journal_branch,
    )
    now = int(time.time())
    secret = _derive_secret(token)
    plane = ProductionRecoveryControlPlaneV1010(
        root=root,
        gateway=gateway,
        owner="github-actions-worker-v113",
        nonce=f"run-{target['run_id']}",
        secrets={"worker-v113": secret},
        repository_full_name=repository_full_name,
        gateway_capabilities=gateway.capabilities,
        github_token=token,
    )
    plane.activate_key(key_id="worker-v113", not_before=now - 1)
    payload = {
        "operation": "recover",
        "pr_number": target["pr_number"],
        "expected_head_sha": target["head_sha"],
        "current_head_sha": target["head_sha"],
        "workflow": target["workflow"],
        "job_id": target["run_id"],
        "conclusion": target["conclusion"],
        "log_excerpt": evidence,
        "now": now,
        "max_attempts": 1,
    }
    nonce = hashlib.sha256(f"v113:{target['run_id']}:{target['head_sha']}".encode()).hexdigest()[:32]
    envelope = plane.sign(key_id="worker-v113", timestamp=now, nonce=nonce, payload=payload)
    result = plane.handle(envelope, now=now)
    return {
        "worker_version": VERSION,
        "status": "RECOVERY_DISPATCHED",
        "category": decision.category,
        "repository": repository_full_name,
        "run_id": target["run_id"],
        "pr_number": target["pr_number"],
        "head_sha": target["head_sha"],
        "gateway": gateway.capabilities(),
        "result": result,
    }


def main():
    with open(os.environ["GITHUB_EVENT_PATH"], "r", encoding="utf-8") as handle:
        event = json.load(handle)
    root = Path(os.environ.get("RUNNER_TEMP", ".")) / "recovery-worker-v113"
    root.mkdir(parents=True, exist_ok=True)
    result = run_worker(
        event=event,
        repository_full_name=os.environ["GITHUB_REPOSITORY"],
        token=os.environ.get("GITHUB_TOKEN", ""),
        root=root,
        journal_branch=os.environ.get("RECOVERY_JOURNAL_BRANCH", "recovery-state-v112"),
    )
    output = root / "v113-recovery-worker-report.json"
    output.write_text(json.dumps(result, sort_keys=True, indent=2), encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
