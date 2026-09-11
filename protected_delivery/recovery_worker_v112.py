import hashlib
import json
import os
import time
from pathlib import Path

from protected_delivery.recovery_control_plane_v1010 import ProductionRecoveryControlPlaneV1010
from protected_delivery.recovery_durable_gateway_v112 import GitHubDurableMutationGatewayV112
from protected_delivery.recovery_worker_v111 import _validate_event

VERSION = "11.2"


def _derive_secret(token: str) -> bytes:
    if not isinstance(token, str) or not token:
        raise ValueError("worker_token_missing")
    return hashlib.sha256(("recovery-worker-v11.2\0" + token).encode()).digest()


def run_worker(*, event: dict, repository_full_name: str, token: str, root, journal_branch="recovery-state-v112") -> dict:
    target = _validate_event(event)
    if target is None:
        return {"worker_version": VERSION, "status": "NO_ACTION"}
    if not token:
        raise RuntimeError("worker_token_missing")

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
        owner="github-actions-worker-v112",
        nonce=f"run-{target['run_id']}",
        secrets={"worker-v112": secret},
        repository_full_name=repository_full_name,
        gateway_capabilities=gateway.capabilities,
        github_token=token,
    )
    plane.activate_key(key_id="worker-v112", not_before=now - 1)
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
    nonce = hashlib.sha256(f"v112:{target['run_id']}:{target['head_sha']}".encode()).hexdigest()[:32]
    envelope = plane.sign(key_id="worker-v112", timestamp=now, nonce=nonce, payload=payload)
    result = plane.handle(envelope, now=now)
    return {
        "worker_version": VERSION,
        "status": "RECOVERY_DISPATCHED",
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
    root = Path(os.environ.get("RUNNER_TEMP", ".")) / "recovery-worker-v112"
    root.mkdir(parents=True, exist_ok=True)
    result = run_worker(
        event=event,
        repository_full_name=os.environ["GITHUB_REPOSITORY"],
        token=os.environ.get("GITHUB_TOKEN", ""),
        root=root,
        journal_branch=os.environ.get("RECOVERY_JOURNAL_BRANCH", "recovery-state-v112"),
    )
    output = root / "v112-recovery-worker-report.json"
    output.write_text(json.dumps(result, sort_keys=True, indent=2), encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
