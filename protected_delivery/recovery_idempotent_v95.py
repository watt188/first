import dataclasses
from typing import Protocol

from protected_delivery.recovery_pending_v93 import SQLitePendingActionJournalV93

VERSION = "9.5"


class IdempotentRecoveryGatewayV95(Protocol):
    def execute_recovery(
        self,
        *,
        pr_number: int,
        expected_head_sha: str,
        action: str,
        job_id: int,
        idempotency_key: str,
    ) -> dict: ...

    def lookup_recovery(self, *, idempotency_key: str) -> dict: ...


@dataclasses.dataclass(frozen=True)
class IdempotentRecoveryIntentV95:
    pr_number: int
    expected_head_sha: str
    current_head_sha: str
    workflow: str
    job_id: int
    action: str
    attempt: int
    replay_key: str
    owner: str
    lease_key: str


class IdempotentRecoveryExecutorV95:
    """Journaled recovery with a downstream idempotency contract.

    The V9.5 executor binds the durable V9.3 journal to a gateway that accepts
    the V8.8 replay key as an idempotency key. If a prior process crashed after
    an external mutation but before journal COMMIT, a later process reconciles
    the PREPARED record through lookup_recovery() instead of blindly replaying.

    This provides exactly-once *logical* recovery only when the downstream
    gateway durably honors the supplied idempotency key. V9.5 does not invent
    exactly-once semantics for providers that ignore or lose that key.
    """

    def __init__(self, *, journal: SQLitePendingActionJournalV93, gateway: IdempotentRecoveryGatewayV95):
        self.journal = journal
        self.gateway = gateway

    @staticmethod
    def _accepted_receipt(receipt: dict) -> str:
        if receipt.get("accepted") is not True:
            raise RuntimeError("recovery_mutation_not_accepted")
        receipt_id = str(receipt.get("receipt_id", "")).strip()
        if not receipt_id:
            raise RuntimeError("recovery_receipt_missing")
        return receipt_id

    def _result(self, intent: IdempotentRecoveryIntentV95, *, receipt_id: str, journal_hash: str, reconciled: bool) -> dict:
        return {
            "status": "IDEMPOTENT_RECOVERY_COMMITTED",
            "version": VERSION,
            "pr_number": intent.pr_number,
            "head_sha": intent.expected_head_sha,
            "action": intent.action,
            "idempotency_key": intent.replay_key,
            "receipt_id": receipt_id,
            "journal_hash": journal_hash,
            "reconciled": reconciled,
        }

    def execute(self, intent: IdempotentRecoveryIntentV95) -> dict:
        if intent.expected_head_sha != intent.current_head_sha:
            raise RuntimeError("stale_pr_head")

        prior = self.journal.read(intent.replay_key)
        if prior is not None:
            if prior.pr_number != intent.pr_number or prior.head_sha != intent.expected_head_sha:
                raise RuntimeError("pending_replay_binding_mismatch")
            if prior.action != intent.action or prior.job_id != intent.job_id or prior.attempt != intent.attempt:
                raise RuntimeError("pending_replay_identity_mismatch")
            if prior.status == "COMMITTED":
                return self._result(
                    intent,
                    receipt_id=prior.receipt_id,
                    journal_hash=prior.action_hash,
                    reconciled=True,
                )
            if prior.status == "ABORTED":
                raise RuntimeError("recovery_action_aborted")
            if prior.status != "PREPARED":
                raise RuntimeError("invalid_pending_status")

            lookup = self.gateway.lookup_recovery(idempotency_key=intent.replay_key)
            if lookup.get("found") is not True:
                raise RuntimeError("pending_recovery_unresolved")
            receipt_id = self._accepted_receipt(lookup)
            committed = self.journal.commit(prior, receipt_id=receipt_id)
            return self._result(
                intent,
                receipt_id=receipt_id,
                journal_hash=committed.action_hash,
                reconciled=True,
            )

        prepared = self.journal.prepare(
            pr_number=intent.pr_number,
            expected_head_sha=intent.expected_head_sha,
            current_head_sha=intent.current_head_sha,
            workflow=intent.workflow,
            job_id=intent.job_id,
            action=intent.action,
            attempt=intent.attempt,
            replay_key=intent.replay_key,
            owner=intent.owner,
            lease_key=intent.lease_key,
        )
        receipt = self.gateway.execute_recovery(
            pr_number=intent.pr_number,
            expected_head_sha=intent.expected_head_sha,
            action=intent.action,
            job_id=intent.job_id,
            idempotency_key=intent.replay_key,
        )
        receipt_id = self._accepted_receipt(receipt)
        committed = self.journal.commit(prepared, receipt_id=receipt_id)
        return self._result(
            intent,
            receipt_id=receipt_id,
            journal_hash=committed.action_hash,
            reconciled=False,
        )
