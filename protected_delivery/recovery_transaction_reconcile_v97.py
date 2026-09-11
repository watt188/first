from protected_delivery.recovery_evidence_v85 import RecoveryEvidenceLedgerV85
from protected_delivery.recovery_lease_v89 import RecoveryLeaseGuardV89
from protected_delivery.recovery_replay_v88 import replay_key
from protected_delivery.recovery_state_v90 import RecoveryStateV90

VERSION = "9.7"


class RecoveryTransactionReconcilerV97:
    """Repair crash-left recovery transactions without replaying side effects.

    V9.7 handles the two post-mutation crash windows left after V9.6:
    journal COMMITTED before evidence CAS, and evidence CAS before lease clear.
    It requires a durable COMMITTED V9.3 journal receipt, verifies exact-head
    identity, appends missing evidence once, and clears only a provably owned or
    expired lease. PREPARED/ABORTED/ambiguous records fail closed.
    """

    def __init__(self, *, store, journal, owner: str, nonce: str):
        if not owner.strip() or not nonce.strip():
            raise ValueError("invalid_reconciler_identity")
        self.store = store
        self.journal = journal
        self.owner = owner.strip()
        self.nonce = nonce.strip()

    @staticmethod
    def _ledger_has_replay(ledger, *, key: str, category: str) -> bool:
        for event in ledger:
            event_key = replay_key(
                pr_number=event.pr_number,
                head_sha=event.head_sha,
                workflow=event.workflow,
                job_id=event.job_id,
                action=event.action,
                attempt=event.attempt,
            )
            if event_key == key:
                if event.category != category or event.accepted is not True:
                    raise RuntimeError("reconciled_evidence_mismatch")
                return True
        return False

    def reconcile(
        self,
        *,
        pr_number: int,
        expected_head_sha: str,
        current_head_sha: str,
        workflow: str,
        job_id: int,
        category: str,
        action: str,
        attempt: int,
        now: int,
    ) -> dict:
        if expected_head_sha != current_head_sha:
            raise RuntimeError("stale_pr_head")
        if now < 0:
            raise ValueError("invalid_reconcile_time")

        key = replay_key(
            pr_number=pr_number,
            head_sha=expected_head_sha,
            workflow=workflow.strip(),
            job_id=job_id,
            action=action.strip(),
            attempt=attempt,
        )
        journaled = self.journal.read(key)
        if journaled is None:
            raise RuntimeError("recovery_journal_missing")
        if journaled.pr_number != pr_number or journaled.head_sha != expected_head_sha:
            raise RuntimeError("recovery_journal_binding_mismatch")
        if (
            journaled.workflow != workflow.strip()
            or journaled.job_id != job_id
            or journaled.action != action.strip()
            or journaled.attempt != attempt
        ):
            raise RuntimeError("recovery_journal_identity_mismatch")
        if journaled.status == "PREPARED":
            raise RuntimeError("pending_recovery_unresolved")
        if journaled.status == "ABORTED":
            raise RuntimeError("recovery_action_aborted")
        if journaled.status != "COMMITTED" or not journaled.receipt_id.strip():
            raise RuntimeError("recovery_journal_not_committed")

        state = self.store.read(pr_number=pr_number, head_sha=expected_head_sha)
        if state is None:
            raise RuntimeError("recovery_state_unavailable")

        appended = False
        if not self._ledger_has_replay(state.ledger, key=key, category=category.strip()):
            ledger = RecoveryEvidenceLedgerV85.append(
                state.ledger,
                pr_number=pr_number,
                expected_head_sha=expected_head_sha,
                current_head_sha=current_head_sha,
                workflow=workflow,
                job_id=job_id,
                category=category,
                action=action,
                attempt=attempt,
                accepted=True,
            )
            replacement = RecoveryStateV90(
                pr_number=state.pr_number,
                head_sha=state.head_sha,
                generation=state.generation + 1,
                lease=state.lease,
                ledger=ledger,
            ).seal()
            state = self.store.compare_and_swap(
                expected_generation=state.generation,
                expected_state_hash=state.state_hash,
                replacement=replacement,
            )
            appended = True

        lease_cleared = False
        if state.lease is not None:
            lease = state.lease
            RecoveryLeaseGuardV89.verify(lease, pr_number=pr_number, head_sha=expected_head_sha)
            owned = lease.owner == self.owner and lease.nonce == self.nonce
            expired = now >= lease.expires_at
            if not owned and not expired:
                raise RuntimeError("recovery_lease_held")
            if owned:
                RecoveryLeaseGuardV89.release(
                    lease, owner=self.owner, nonce=self.nonce, now=now
                )
            replacement = RecoveryStateV90(
                pr_number=state.pr_number,
                head_sha=state.head_sha,
                generation=state.generation + 1,
                lease=None,
                ledger=state.ledger,
            ).seal()
            state = self.store.compare_and_swap(
                expected_generation=state.generation,
                expected_state_hash=state.state_hash,
                replacement=replacement,
            )
            lease_cleared = True

        verified = RecoveryEvidenceLedgerV85.verify(
            state.ledger,
            expected_head_sha=expected_head_sha,
            pr_number=pr_number,
        )
        return {
            "status": "RECOVERY_TRANSACTION_RECONCILED",
            "version": VERSION,
            "pr_number": pr_number,
            "head_sha": expected_head_sha,
            "replay_key": key,
            "receipt_id": journaled.receipt_id,
            "evidence_appended": appended,
            "lease_cleared": lease_cleared,
            "state_generation": state.generation,
            "state_hash": state.state_hash,
            "ledger_chain_head": verified["chain_head"],
        }
