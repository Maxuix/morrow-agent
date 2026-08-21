"""Deterministic validation and persistence of semantic Preference operations."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from morrow.core.learning_safety import scan_learning_text
from morrow.core.ports import IdSource
from morrow.core.preference_documents import PreferenceReviewSnapshot
from morrow.core.preference_models import (
    PreferenceOperation,
    PreferenceOperationKind,
    PreferenceScope,
    PreferenceStatus,
    preference_operation_fingerprint,
)
from morrow.core.preference_persistence_models import (
    PreferenceEvidence,
    PreferenceProposal,
    PreferenceProposalStatus,
    PreferenceReviewJob,
)
from morrow.core.preference_review import PreferenceReviewOutput


@dataclass(frozen=True)
class PreferenceProposalRejection:
    operation_index: int
    code: str


@dataclass(frozen=True)
class PreferenceProposalPipelineResult:
    proposals: tuple[PreferenceProposal, ...] = ()
    duplicate_count: int = 0
    suppressed_count: int = 0
    rejected_count: int = 0
    rejections: tuple[PreferenceProposalRejection, ...] = ()

    @property
    def proposal_ids(self) -> tuple[str, ...]:
        return tuple(item.proposal_id for item in self.proposals)


class PreferenceProposalPipelineError(ValueError):
    """Sanitized failure for a Review source or batch boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PreferenceProposalPipeline:
    """Turn one valid Reviewer result into independent, immutable Inbox proposals."""

    def __init__(
        self,
        *,
        journal,
        workspace_id: str,
        id_source: IdSource,
        clock: Callable[[], datetime] | object,
    ) -> None:
        self.journal = getattr(journal, "preference_journal", journal)
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.clock = clock

    def persist(
        self,
        job: PreferenceReviewJob,
        evidence: PreferenceEvidence,
        result: PreferenceReviewOutput | Iterable[PreferenceOperation],
    ) -> PreferenceProposalPipelineResult:
        """Validate all operations and persist only the valid independent proposals."""

        stored_job = self.journal.get_preference_review_job(self.workspace_id, job.job_id)
        stored_evidence = self.journal.get_preference_evidence(
            self.workspace_id, evidence.evidence_id
        )
        self._assert_source(stored_job, stored_evidence, job, evidence)
        operations = self._operations(result)
        snapshot = self._snapshot(stored_job)
        return self._in_transaction(
            lambda txn: self._persist_in_txn(
                txn,
                stored_job,
                stored_evidence,
                operations,
                snapshot,
            )
        )

    process = persist

    def _persist_in_txn(
        self,
        txn,
        job: PreferenceReviewJob,
        evidence: PreferenceEvidence,
        operations: tuple[PreferenceOperation, ...],
        snapshot: PreferenceReviewSnapshot,
    ) -> PreferenceProposalPipelineResult:
        if len(operations) > 8:
            raise PreferenceProposalPipelineError(
                "operation_count", "Preference Review returned too many operations"
            )
        existing = txn.list_preference_proposals(self.workspace_id, job_id=job.job_id, limit=500)
        by_job_fingerprint = {item.fingerprint for item in existing if item.job_id == job.job_id}
        entries = {entry.preference_id: entry for entry in snapshot.entries}
        proposed_keys = {"global": {}, "workspace": {}}
        for entry in snapshot.entries:
            if entry.status not in {PreferenceStatus.ACTIVE, PreferenceStatus.DISABLED}:
                continue
            proposed_keys[entry.scope.value][self._statement_key(entry.statement)] = (
                "disabled_duplicate" if entry.status is PreferenceStatus.DISABLED else "duplicate"
            )
        targets: set[str] = set()
        proposals: list[PreferenceProposal] = []
        rejections: list[PreferenceProposalRejection] = []
        duplicate_count = 0
        suppressed_count = 0
        for index, operation in enumerate(operations):
            code = self._validate_operation(
                operation,
                evidence=evidence,
                entries=entries,
                targets=targets,
                proposed_keys=proposed_keys,
            )
            if code is not None:
                rejections.append(PreferenceProposalRejection(index, code))
                continue
            fingerprint = preference_operation_fingerprint(operation)
            if txn.has_preference_proposal_fingerprint(
                self.workspace_id,
                fingerprint,
                status=PreferenceProposalStatus.SUPPRESSED,
            ):
                suppressed_count += 1
                continue
            if fingerprint in by_job_fingerprint:
                duplicate_count += 1
                continue
            proposal = PreferenceProposal(
                proposal_id=self.id_source.new_id("pprop"),
                workspace_id=self.workspace_id,
                job_id=job.job_id,
                evidence_id=evidence.evidence_id,
                operation=operation,
                fingerprint=fingerprint,
                expected_target_revision=(
                    entries[operation.preference_id].entry_revision
                    if operation.preference_id is not None
                    else None
                ),
                expected_document_revision=self._document_revision(snapshot, operation.scope),
                created_at=self._now(),
            )
            proposals.append(txn.put_preference_proposal(self.workspace_id, proposal))
            by_job_fingerprint.add(fingerprint)
        return PreferenceProposalPipelineResult(
            proposals=tuple(proposals),
            duplicate_count=duplicate_count,
            suppressed_count=suppressed_count,
            rejected_count=len(rejections),
            rejections=tuple(rejections),
        )

    def _in_transaction(self, work):
        if hasattr(self.journal, "transact"):
            return self.journal.transact(work)
        backend = getattr(self.journal, "backend", None)
        if backend is not None and hasattr(backend, "transact"):
            return backend.transact(lambda: work(self.journal))
        return work(self.journal)

    def _assert_source(
        self,
        stored_job: PreferenceReviewJob | None,
        stored_evidence: PreferenceEvidence | None,
        job: PreferenceReviewJob,
        evidence: PreferenceEvidence,
    ) -> None:
        if stored_job is None or stored_evidence is None:
            raise PreferenceProposalPipelineError(
                "missing_reference", "Preference Review source is missing"
            )
        if job.workspace_id != self.workspace_id or evidence.workspace_id != self.workspace_id:
            raise PreferenceProposalPipelineError(
                "cross_workspace", "Preference Review source is outside the workspace"
            )
        if stored_job.job_id != job.job_id or stored_evidence.evidence_id != evidence.evidence_id:
            raise PreferenceProposalPipelineError(
                "source_mismatch", "Preference Review source changed"
            )
        if (
            stored_evidence.job_id != stored_job.job_id
            or stored_evidence.turn_id != stored_job.turn_id
            or stored_evidence.source_kind != "user_turn"
            or stored_evidence.actor != "user"
            or stored_evidence.safety_rejection_code is not None
        ):
            raise PreferenceProposalPipelineError(
                "evidence_invalid", "Preference Evidence is not eligible"
            )

    @staticmethod
    def _operations(
        result: PreferenceReviewOutput | Iterable[PreferenceOperation],
    ) -> tuple[PreferenceOperation, ...]:
        if isinstance(result, Mapping):
            try:
                result = PreferenceReviewOutput.model_validate(result, strict=True)
            except (TypeError, ValueError):
                raise PreferenceProposalPipelineError(
                    "malformed_output", "Preference Review output is invalid"
                ) from None
        raw = result.operations if isinstance(result, PreferenceReviewOutput) else tuple(result)
        operations: list[PreferenceOperation] = []
        for item in raw:
            if not isinstance(item, PreferenceOperation):
                try:
                    item = PreferenceOperation.model_validate(item, strict=True)
                except (TypeError, ValueError):
                    raise PreferenceProposalPipelineError(
                        "malformed_output", "Preference Review operation is invalid"
                    ) from None
            operations.append(item)
        return tuple(operations)

    @staticmethod
    def _snapshot(job: PreferenceReviewJob | None) -> PreferenceReviewSnapshot:
        if job is None:
            raise PreferenceProposalPipelineError(
                "missing_reference", "Preference Review job is missing"
            )
        try:
            snapshot = PreferenceReviewSnapshot.model_validate_json(
                job.active_snapshot_json, strict=True
            )
        except ValueError:
            raise PreferenceProposalPipelineError(
                "snapshot_invalid", "frozen Preference snapshot is invalid"
            ) from None
        if snapshot.global_document_revision != job.source_global_revision:
            raise PreferenceProposalPipelineError(
                "snapshot_invalid", "global Preference snapshot revision is invalid"
            )
        if snapshot.workspace_document_revision != job.source_workspace_revision:
            raise PreferenceProposalPipelineError(
                "snapshot_invalid", "workspace Preference snapshot revision is invalid"
            )
        return snapshot

    @staticmethod
    def _validate_operation(
        operation: PreferenceOperation,
        *,
        evidence: PreferenceEvidence,
        entries,
        targets: set[str],
        proposed_keys: dict[str, dict[str, str]],
    ) -> str | None:
        if operation.scope is PreferenceScope.SESSION:
            return "session_scope"
        if operation.evidence_ids != (evidence.evidence_id,):
            return "invented_evidence"
        if operation.preference_id is not None:
            if operation.preference_id in targets:
                return "duplicate_target"
            targets.add(operation.preference_id)
        if operation.statement is not None:
            if scan_learning_text(operation.statement):
                return "safety_rejected"
        scope_key = operation.scope.value
        if operation.operation is PreferenceOperationKind.ADD:
            assert operation.statement is not None
            key = PreferenceProposalPipeline._statement_key(operation.statement)
            duplicate_code = proposed_keys[scope_key].get(key)
            if duplicate_code is not None:
                return duplicate_code
            proposed_keys[scope_key][key] = "duplicate"
            return None
        target = entries.get(operation.preference_id)
        if target is None:
            return "target_missing"
        if target.scope is not operation.scope:
            return "target_scope"
        if target.status is PreferenceStatus.DELETED:
            return "target_deleted"
        if operation.operation is PreferenceOperationKind.REPLACE and operation.statement is None:
            return "operation_shape"
        return None

    @staticmethod
    def _statement_key(statement: str) -> str:
        return " ".join(statement.split()).casefold()

    @staticmethod
    def _document_revision(snapshot: PreferenceReviewSnapshot, scope: PreferenceScope) -> int:
        return (
            snapshot.global_document_revision
            if scope is PreferenceScope.GLOBAL
            else snapshot.workspace_document_revision
        )

    def _now(self) -> datetime:
        value = self.clock() if callable(self.clock) else self.clock.now()
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


PreferenceProposalService = PreferenceProposalPipeline


__all__ = [
    "PreferenceProposalPipeline",
    "PreferenceProposalPipelineError",
    "PreferenceProposalPipelineResult",
    "PreferenceProposalRejection",
    "PreferenceProposalService",
]
