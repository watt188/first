import dataclasses

VERSION = "8.2"


@dataclasses.dataclass(frozen=True)
class FailureSignal:
    workflow: str
    conclusion: str
    log_excerpt: str = ""


@dataclasses.dataclass(frozen=True)
class RecoveryDecision:
    category: str
    action: str
    retryable: bool
    requires_human: bool


class AutonomousRecoveryV82:
    """Fail-closed recovery classifier for protected delivery failures.

    The controller may retry transient provider/infra failures or request an
    autonomous code rework. Policy drift, permission failures and ambiguous
    conditions are escalated instead of bypassed.
    """

    PROVIDER_MARKERS = (
        "provider_circuit_open",
        "http_429",
        "http_500",
        "http_502",
        "http_503",
        "http_504",
        "transport_timeout_or_network",
        "timeout",
    )
    EVIDENCE_MARKERS = (
        "manifest",
        "source_sha_mismatch",
        "evidence",
        "sha256",
    )
    POLICY_MARKERS = (
        "ruleset",
        "policy drift",
        "strict_status_checks_disabled",
        "missing_required_checks",
        "repository rule violations",
    )
    PERMISSION_MARKERS = (
        "resource not accessible by integration",
        "permission",
        "forbidden",
        "requires authentication",
    )

    @classmethod
    def classify(cls, signal: FailureSignal) -> RecoveryDecision:
        text = f"{signal.workflow}\n{signal.log_excerpt}".lower()
        if signal.conclusion not in {"failure", "cancelled", "timed_out", "action_required"}:
            raise ValueError("not_a_failure")

        if any(marker in text for marker in cls.PERMISSION_MARKERS):
            return RecoveryDecision("permission", "escalate_human", False, True)
        if any(marker in text for marker in cls.POLICY_MARKERS):
            return RecoveryDecision("policy_drift", "escalate_human", False, True)
        if any(marker in text for marker in cls.PROVIDER_MARKERS):
            return RecoveryDecision("provider_transient", "rerun_failed_job", True, False)
        if any(marker in text for marker in cls.EVIDENCE_MARKERS):
            return RecoveryDecision("evidence_inconsistency", "autonomous_rework", False, False)
        if signal.workflow in {"ci", "v6-production-baseline", "v66-pep-hardening", "v68-execution-isolation"}:
            return RecoveryDecision("code_or_test_defect", "autonomous_rework", False, False)
        return RecoveryDecision("ambiguous", "escalate_human", False, True)

    @classmethod
    def next_action(cls, signal: FailureSignal, attempts: int, max_attempts: int = 2) -> RecoveryDecision:
        if attempts < 0 or max_attempts < 1:
            raise ValueError("invalid_attempt_budget")
        decision = cls.classify(signal)
        if decision.retryable and attempts >= max_attempts:
            return RecoveryDecision(decision.category, "escalate_human", False, True)
        return decision
