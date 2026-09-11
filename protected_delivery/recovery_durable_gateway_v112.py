import base64
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request

VERSION = "11.2"


class GitHubDurableMutationGatewayV112:
    """Durable fail-closed gateway for GitHub Actions reruns.

    A dedicated repository branch stores one JSON journal record per idempotency
    key. The gateway writes PREPARED before calling GitHub's rerun endpoint and
    writes COMMITTED after GitHub accepts it. If execution crashes after the
    external mutation but before COMMIT, the durable PREPARED record makes the
    next attempt fail closed instead of dispatching the mutation again.

    This provides durable logical idempotency / at-most-once dispatch from this
    gateway. It does not claim provider-level physical exactly-once because the
    GitHub rerun API does not accept a caller supplied idempotency key.
    """

    def __init__(
        self,
        *,
        repository_full_name: str,
        token: str,
        run_id: int,
        journal_branch: str = "recovery-state-v112",
        api_base: str = "https://api.github.com",
        timeout: float = 10.0,
        opener=None,
    ):
        if not isinstance(repository_full_name, str) or repository_full_name.count("/") != 1:
            raise ValueError("invalid_repository_full_name")
        if not isinstance(token, str) or not token:
            raise ValueError("github_token_missing")
        if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id < 1:
            raise ValueError("invalid_run_id")
        if not isinstance(journal_branch, str) or not journal_branch.strip():
            raise ValueError("invalid_journal_branch")
        self.repository = repository_full_name
        self.token = token
        self.run_id = run_id
        self.journal_branch = journal_branch
        self.api_base = api_base.rstrip("/")
        self.timeout = float(timeout)
        self.opener = opener or urllib.request.urlopen

    @staticmethod
    def _key_hash(idempotency_key: str) -> str:
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ValueError("invalid_idempotency_key")
        return hashlib.sha256(idempotency_key.encode()).hexdigest()

    def _journal_path(self, idempotency_key: str) -> str:
        digest = self._key_hash(idempotency_key)
        return f".recovery-journal/v112/{digest[:2]}/{digest}.json"

    def _request(self, url: str, *, method="GET", body=None):
        payload = None if body is None else json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        req = urllib.request.Request(
            url,
            method=method,
            data=payload,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "first-recovery-gateway-v11.2",
                **({"Content-Type": "application/json"} if payload is not None else {}),
            },
        )
        try:
            with self.opener(req, timeout=self.timeout) as response:
                raw = response.read()
                data = json.loads(raw) if raw else {}
                return response.status, data
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            data = json.loads(raw) if raw else {}
            return exc.code, data
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError("github_gateway_transport_error") from exc

    def _contents_url(self, path: str) -> str:
        quoted = "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))
        return f"{self.api_base}/repos/{self.repository}/contents/{quoted}"

    def _read_record(self, idempotency_key: str):
        path = self._journal_path(idempotency_key)
        query = urllib.parse.urlencode({"ref": self.journal_branch})
        status, data = self._request(f"{self._contents_url(path)}?{query}")
        if status == 404:
            return None
        if status != 200 or not isinstance(data, dict):
            raise RuntimeError("durable_journal_read_failed")
        encoded = data.get("content")
        sha = data.get("sha")
        if not isinstance(encoded, str) or not isinstance(sha, str):
            raise RuntimeError("durable_journal_invalid_record")
        try:
            record = json.loads(base64.b64decode(encoded).decode())
        except Exception as exc:
            raise RuntimeError("durable_journal_invalid_record") from exc
        if not isinstance(record, dict):
            raise RuntimeError("durable_journal_invalid_record")
        return record, sha

    def _write_record(self, idempotency_key: str, record: dict, *, expected_sha=None):
        path = self._journal_path(idempotency_key)
        body = {
            "message": f"recovery journal {record['state'].lower()} {self._key_hash(idempotency_key)[:12]}",
            "content": base64.b64encode(
                json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
            ).decode(),
            "branch": self.journal_branch,
        }
        if expected_sha is not None:
            body["sha"] = expected_sha
        status, data = self._request(self._contents_url(path), method="PUT", body=body)
        if status not in {200, 201}:
            if status in {409, 422}:
                raise RuntimeError("durable_journal_cas_conflict")
            raise RuntimeError("durable_journal_write_failed")
        content = data.get("content") if isinstance(data, dict) else None
        sha = content.get("sha") if isinstance(content, dict) else None
        if not isinstance(sha, str):
            raise RuntimeError("durable_journal_write_invalid_receipt")
        return sha

    def lookup_recovery(self, idempotency_key: str):
        loaded = self._read_record(idempotency_key)
        if loaded is None:
            return None
        record, _ = loaded
        state = record.get("state")
        if state == "COMMITTED":
            receipt = record.get("receipt")
            if not isinstance(receipt, dict) or receipt.get("accepted") is not True:
                raise RuntimeError("durable_journal_invalid_commit")
            return receipt
        if state == "PREPARED":
            raise RuntimeError("durable_mutation_ambiguous_prepared")
        if state == "ABORTED":
            raise RuntimeError("durable_mutation_aborted")
        raise RuntimeError("durable_journal_unknown_state")

    def execute_recovery(self, pr_number, expected_head_sha, action, job_id, idempotency_key):
        if action != "rerun_failed_job":
            raise RuntimeError("durable_gateway_action_not_supported")
        if not isinstance(pr_number, int) or isinstance(pr_number, bool) or pr_number < 1:
            raise ValueError("invalid_pr_number")
        if not isinstance(expected_head_sha, str) or len(expected_head_sha) != 40:
            raise ValueError("invalid_expected_head_sha")
        if int(job_id) != self.run_id:
            raise RuntimeError("durable_gateway_run_identity_mismatch")

        existing = self._read_record(idempotency_key)
        if existing is not None:
            return self.lookup_recovery(idempotency_key)

        prepared = {
            "version": VERSION,
            "state": "PREPARED",
            "repository": self.repository,
            "pr_number": pr_number,
            "expected_head_sha": expected_head_sha,
            "action": action,
            "run_id": self.run_id,
            "idempotency_key_hash": self._key_hash(idempotency_key),
        }
        prepared_sha = self._write_record(idempotency_key, prepared)

        rerun_url = f"{self.api_base}/repos/{self.repository}/actions/runs/{self.run_id}/rerun-failed-jobs"
        status, _ = self._request(rerun_url, method="POST")
        if status not in {201, 202}:
            aborted = {**prepared, "state": "ABORTED", "github_status": status}
            try:
                self._write_record(idempotency_key, aborted, expected_sha=prepared_sha)
            finally:
                raise RuntimeError("github_rerun_failed_jobs_not_accepted")

        receipt = {
            "accepted": True,
            "receipt_id": f"github-rerun:{self.run_id}:{self._key_hash(idempotency_key)[:16]}",
            "provider_status": status,
            "physical_exactly_once": False,
        }
        committed = {**prepared, "state": "COMMITTED", "receipt": receipt}
        self._write_record(idempotency_key, committed, expected_sha=prepared_sha)
        return receipt

    def capabilities(self) -> dict:
        return {
            "provider": "github-actions-rerun-failed-jobs+journal",
            "scope": f"repository:{self.repository}:branch:{self.journal_branch}",
            "durable_idempotency": True,
            "lookup_by_idempotency_key": True,
            "physical_exactly_once": False,
            "ambiguity_policy": "fail_closed_on_prepared",
        }
