from protected_delivery.recovery_evidence_v85 import RecoveryEvidenceLedgerV85
from protected_delivery.recovery_lease_v89 import RecoveryLeaseGuardV89
from protected_delivery.recovery_replay_v88 import replay_key

VERSION = "9.9"


class RecoveryRuntimeAuditV99:
    """Read-only convergence audit for one exact-head logical recovery action.

    The auditor never mutates durable state, the pending-action journal, or the
    external provider. It derives the canonical replay identity, verifies the
    exact-head state and journal record, and reports whether the action is idle,
    converged, needs V9.8 reconciliation, is blocked by an active foreign lease,
    or terminated by an ABORTED journal record.
    """

    def __init__(self, *, store, journal, owner: str, nonce: str):
        if not owner.strip() or not nonce.strip():
            raise ValueError("invalid_audit_identity")
        self.store = store
        self.journal = journal
        self.owner = owner.strip()
        self.nonce = nonce.strip()

    @staticmethod
    def _ledger_match(ledger, *, key: str):
        match = None
        for event in ledger:
            existing = replay_key(
                pr_number=event.pr_number,
                head_sha=event.head_sha,
                workflow=event.workflow,
                job_id=event.job_id,
                action=event.action,
                attempt=event.attempt,
            )
            if existing == key:
                if event.accepted is not True:
                    raise RuntimeError("audit_unaccepted_evidence")
                if match is not None:
                    raise RuntimeError("audit_duplicate_evidence")
                match = event
        return match

    def audit(
        self,
        *,
        pr_number: int,
        expected_head_sha: str,
        current_head_sha: str,
        workflow: str,
        job_id: int,
        action: str,
        attempt: int,
        now: int,
    ) -> dict:
        if expected_head_sha != current_head_sha:
            raise RuntimeError("stale_pr_head")
        if now < 0:
            raise ValueError("invalid_audit_time")
        if pr_number < 1 or job_id < 1 or attempt < 0:
            raise ValueError("invalid_audit_identity")
        if not workflow.strip() or not action.strip():
            raise ValueError("invalid_audit_identity")

        key = replay_key(
            pr_number=pr_number,
            head_sha=expected_head_sha,
            workflow=workflow.strip(),
            job_id=job_id,
            action=action.strip(),
            attempt=attempt,
        )
        state = self.store.read(pr_number=pr_number, head_sha=expected_head_sha)
        journaled = self.journal.read(key)

        if state is None:
            if journaled is None:
                return {
                    "status": "HEALTHY_IDLE",
                    "version": VERSION,
                    "pr_number": pr_number,
                    "head_sha": expected_head_sha,
                    "replay_key": key,
                    "needs_reconciliation": False,
                }
            raise RuntimeError("journal_without_recovery_state")

        verified = RecoveryEvidenceLedgerV85.verify(
            state.ledger,
            expected_head_sha=expected_head_sha,
            pr_number=pr_number,
        )
        evidence = self._ledger_match(state.ledger, key=key)

        lease_status = "NONE"
        if state.lease is not None:
            lease = state.lease
            RecoveryLeaseGuardV89.verify(
                lease, pr_number=pr_number, head_sha=expected_head_sha
            )
            owned = lease.owner == self.owner and lease.nonce == self.nonce
            expired = now >= lease.expires_at
            if owned:
                lease_status = "OWNED_ACTIVE" if not expired else "OWNED_EXPIRED"
            elif expired:
                lease_status = "FOREIGN_EXPIRED"
            else:
                lease_status = "FOREIGN_ACTIVE"

        if journaled is None:
            if evidence is not None:
                return {
                    "status": "LEGACY_EVIDENCE_ONLY",
                    "version": VERSION,
                    "pr_number": pr_number,
                    "head_sha": expected_head_sha,
                    "replay_key": key,
                    "lease_status": lease_status,
                    "needs_reconciliation": state.lease is not None,
                    "ledger_chain_head": verified["chain_head"],
                }
            if state.lease is not None and now >= state.lease.expires_at:
                return {
                    "status": "STALE_LEASE_NEEDS_RECONCILIATION",
                    "version": VERSION,
                    "pr_number": pr_number,
                    "head_sha": expected_head_sha,
                    "replay_key": key,
                    "lease_status": lease_status,
                    "needs_reconciliation": True,
                    "ledger_chain_head": verified["chain_head"],
                }
            return {
                "status": "HEALTHY_IDLE",
                "version": VERSION,
                "pr_number": pr_number,
                "head_sha": expected_head_sha,
                "replay_key": key,
                "lease_status": lease_status,
                "needs_reconciliation": False,
                "ledger_chain_head": verified["chain_head"],
            }

        if (
            journaled.pr_number != pr_number
            or journaled.head_sha != expected_head_sha
            or journaled.workflow != workflow.strip()
            or journaled.job_id != job_id
            or journaled.action != action.strip()
            or journaled.attempt != attempt
            or journaled.replay_key != key
        ):
            raise RuntimeError("audit_journal_identity_mismatch")

        if journaled.status == "ABORTED":
            return {
                "status": "TERMINAL_ABORTED",
                "version": VERSION,
                "pr_number": pr_number,
                "head_sha": expected_head_sha,
                "replay_key": key,
                "lease_status": lease_status,
                "needs_reconciliation": False,
                "ledger_chain_head": verified["chain_head"],
            }

        if lease_status == "FOREIGN_ACTIVE":
            return {
                "status": "BLOCKED_ACTIVE_FOREIGN_LEASE",
                "version": VERSION,
                "pr_number": pr_number,
                "head_sha": expected_head_sha,
                "replay_key": key,
                "lease_status": lease_status,
                "needs_reconciliation": False,
                "ledger_chain_head": verified["chain_head"],
            }

        if journaled.status == "PREPARED":
            return {
                "status": "PREPARED_NEEDS_RECONCILIATION",
                "version": VERSION,
                "pr_number": pr_number,
                "head_sha": expected_head_sha,
                "replay_key": key,
                "lease_status": lease_status,
                "needs_reconciliation": True,
                "ledger_chain_head": verified["chain_head"],
            }

        if journaled.status != "COMMITTED" or not journaled.receipt_id.strip():
            raise RuntimeError("audit_invalid_journal_status")

        if evidence is None or state.lease is not None:
            return {
                "status": "COMMITTED_NEEDS_RECONCILIATION",
                "version": VERSION,
                "pr_number": pr_number,
                "head_sha": expected_head_sha,
                "replay_key": key,
                "receipt_id": journaled.receipt_id,
                "lease_status": lease_status,
                "needs_reconciliation": True,
                "ledger_chain_head": verified["chain_head"],
            }

        return {
            "status": "CONVERGED",
            "version": VERSION,
            "pr_number": pr_number,
            "head_sha": expected_head_sha,
            "replay_key": key,
            "receipt_id": journaled.receipt_id,
            "lease_status": "NONE",
            "needs_reconciliation": False,
            "ledger_chain_head": verified["chain_head"],
        }
