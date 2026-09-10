import dataclasses
import re

VERSION = "7.5"


@dataclasses.dataclass(frozen=True)
class MergeCandidateV75:
    pr_number: int
    head_sha: str
    expected_head_sha: str
    mergeable: bool
    draft: bool
    checks: dict[str, str]
    required_checks: tuple[str, ...]
    evidence_status: str
    evidence_source_sha: str

    def authorize(self) -> dict:
        if self.pr_number < 1:
            raise ValueError("invalid_pr_number")
        for value, error in ((self.head_sha, "invalid_head_sha"), (self.expected_head_sha, "invalid_expected_head_sha"), (self.evidence_source_sha, "invalid_evidence_source_sha")):
            if not re.fullmatch(r"[0-9a-f]{40}", value or ""):
                raise ValueError(error)
        if self.head_sha != self.expected_head_sha:
            raise RuntimeError("head_moved")
        if self.evidence_source_sha != self.head_sha:
            raise RuntimeError("evidence_head_mismatch")
        if self.draft:
            raise RuntimeError("draft_pr")
        if not self.mergeable:
            raise RuntimeError("pr_not_mergeable")
        if self.evidence_status != "PASSED":
            raise RuntimeError("evidence_not_passed")
        if not self.required_checks:
            raise ValueError("no_required_checks")
        missing = [name for name in self.required_checks if name not in self.checks]
        if missing:
            raise RuntimeError("missing_required_checks:" + ",".join(sorted(missing)))
        failed = [name for name in self.required_checks if self.checks.get(name) != "success"]
        if failed:
            raise RuntimeError("required_checks_not_green:" + ",".join(sorted(failed)))
        return {
            "status": "AUTHORIZED",
            "version": VERSION,
            "pr_number": self.pr_number,
            "head_sha": self.head_sha,
            "required_check_count": len(self.required_checks),
            "evidence_bound": True,
            "mergeable": True,
        }


def authorize_merge(payload: dict) -> dict:
    candidate = MergeCandidateV75(
        pr_number=int(payload.get("pr_number") or 0),
        head_sha=str(payload.get("head_sha") or ""),
        expected_head_sha=str(payload.get("expected_head_sha") or ""),
        mergeable=bool(payload.get("mergeable")),
        draft=bool(payload.get("draft")),
        checks=dict(payload.get("checks") or {}),
        required_checks=tuple(payload.get("required_checks") or ()),
        evidence_status=str(payload.get("evidence_status") or ""),
        evidence_source_sha=str(payload.get("evidence_source_sha") or ""),
    )
    return candidate.authorize()
