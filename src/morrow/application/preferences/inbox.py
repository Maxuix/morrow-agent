"""Typed Preference Inbox queries and explicit decision flows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from morrow.application.preferences.writer import PreferenceWriter, PreferenceWriterError
from morrow.core.application import QueryPage
from morrow.core.learning_safety import scan_learning_text
from morrow.core.ports import IdSource
from morrow.core.preference_documents import PreferenceDocument
from morrow.core.preference_models import PreferenceOperation, PreferenceOperationKind
from morrow.core.preference_persistence_models import (
    PreferenceProposal,
    PreferenceProposalStatus,
)


@dataclass(frozen=True)
class PreferenceProposalView:
    proposal_id: str
    job_id: str
    evidence_id: str
    operation: PreferenceOperation
    status: PreferenceProposalStatus
    expected_target_revision: int | None
    expected_document_revision: int
    row_version: int
    evidence_excerpt: str | None
    evidence_source_kind: str | None
    stale: bool
    stale_reason: str | None
    current_target_revision: int | None
    current_document_revision: int | None


@dataclass(frozen=True)
class PreferenceDecisionPreview:
    proposal: PreferenceProposalView
    operation: PreferenceOperation
    expected_row_version: int
    expected_target_revision: int | None
    expected_document_revision: int
    available: bool
    reason: str | None = None
    edited: bool = False


@dataclass(frozen=True)
class PreferenceInboxDecisionResult:
    proposals: tuple[PreferenceProposal, ...]
    batch_id: str | None = None
    document_revision: int | None = None
    replayed: bool = False

    @property
    def proposal(self) -> PreferenceProposal:
        if len(self.proposals) != 1:
            raise AttributeError("decision contains more than one proposal")
        return self.proposals[0]


class PreferenceInboxError(RuntimeError):
    """Sanitized error returned by a Preference Inbox decision boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PreferenceInbox:
    """Keep list/show/preview and decisions separate from the legacy Learning Inbox."""

    def __init__(
        self,
        *,
        journal,
        workspace_id: str,
        writer: PreferenceWriter | None = None,
        id_source: IdSource | None = None,
        clock=None,
    ) -> None:
        self.writer = writer
        self.journal = getattr(journal, "preference_journal", journal)
        if writer is not None:
            self.journal = writer.journal
        self.workspace_id = workspace_id
        self.id_source = id_source or (writer.id_source if writer is not None else None)
        self.clock = clock or (writer.clock if writer is not None else None)

    def list(
        self,
        *,
        status: PreferenceProposalStatus | str | None = PreferenceProposalStatus.PROPOSED,
        job_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> QueryPage[PreferenceProposalView]:
        selected_status = self._status(status)
        offset = self._offset(cursor, limit)
        proposals = self.journal.list_preference_proposals(
            self.workspace_id,
            status=selected_status,
            job_id=job_id,
            limit=min(500, offset + limit),
        )
        page = tuple(self._view(item) for item in proposals[offset : offset + limit])
        next_cursor = str(offset + len(page)) if offset + len(page) < len(proposals) else None
        return QueryPage(page, next_cursor)

    list_proposals = list

    def get(self, proposal_id: str) -> PreferenceProposalView | None:
        proposal = self.journal.get_preference_proposal(self.workspace_id, proposal_id)
        return None if proposal is None else self._view(proposal)

    show = get

    def preview(
        self,
        proposal_id: str,
        *,
        edit: str | PreferenceOperation | Mapping[str, object] | None = None,
    ) -> PreferenceDecisionPreview:
        proposal = self.journal.get_preference_proposal(self.workspace_id, proposal_id)
        if proposal is None:
            raise PreferenceInboxError("not_found", "Preference proposal is missing")
        view = self._view(proposal)
        operation = self._operation(proposal, edit)
        expected_document_revision = (
            view.current_document_revision
            if view.current_document_revision is not None
            else proposal.expected_document_revision
        )
        expected_target_revision = view.current_target_revision
        if expected_target_revision is None and proposal.operation.preference_id is not None:
            expected_target_revision = proposal.expected_target_revision
        available = proposal.status is PreferenceProposalStatus.PROPOSED and not view.stale
        reason = None if available else (view.stale_reason or "proposal_not_proposed")
        return PreferenceDecisionPreview(
            proposal=view,
            operation=operation,
            expected_row_version=proposal.row_version,
            expected_target_revision=expected_target_revision,
            expected_document_revision=expected_document_revision,
            available=available,
            reason=reason,
            edited=edit is not None,
        )

    def accept(
        self,
        proposal_id: str,
        *,
        command_id: str | None = None,
        expected_row_version: int | None = None,
        expected_document_revision: int | None = None,
        expected_target_revision: int | None = None,
        edit: str | PreferenceOperation | Mapping[str, object] | None = None,
    ) -> PreferenceInboxDecisionResult:
        preview = self.preview(proposal_id, edit=edit)
        self._assert_preview_tokens(
            preview,
            expected_row_version=expected_row_version,
            expected_document_revision=expected_document_revision,
            expected_target_revision=expected_target_revision,
        )
        if not preview.available:
            raise PreferenceInboxError(
                preview.reason or "stale", "Preference proposal is stale or unavailable"
            )
        return self._accept_previews((preview,), command_id=command_id)

    def edit_and_accept(
        self, proposal_id: str, statement: str, **kwargs
    ) -> PreferenceInboxDecisionResult:
        return self.accept(proposal_id, edit=statement, **kwargs)

    def accept_many(
        self,
        proposal_ids: Sequence[str],
        *,
        command_id: str | None = None,
        edits: Mapping[str, str | PreferenceOperation | Mapping[str, object]] | None = None,
        expected_row_versions: Mapping[str, int] | None = None,
        expected_document_revision: int | None = None,
    ) -> PreferenceInboxDecisionResult:
        if not 1 <= len(proposal_ids) <= 8 or len(set(proposal_ids)) != len(proposal_ids):
            raise PreferenceInboxError("operation_count", "Preference accept-many batch is invalid")
        previews = tuple(
            self.preview(proposal_id, edit=(edits or {}).get(proposal_id))
            for proposal_id in proposal_ids
        )
        scopes = {preview.operation.scope for preview in previews}
        if len(scopes) != 1:
            raise PreferenceInboxError("mixed_scope", "Preference accept-many must use one scope")
        if expected_document_revision is not None and any(
            preview.expected_document_revision != expected_document_revision for preview in previews
        ):
            raise PreferenceInboxError("stale", "Preference document revision is stale")
        for preview in previews:
            self._assert_preview_tokens(
                preview,
                expected_row_version=(expected_row_versions or {}).get(
                    preview.proposal.proposal_id
                ),
                expected_document_revision=expected_document_revision,
                expected_target_revision=None,
            )
            if not preview.available:
                raise PreferenceInboxError(
                    preview.reason or "stale", "Preference proposal is stale or unavailable"
                )
        return self._accept_previews(previews, command_id=command_id)

    def reject(
        self,
        proposal_id: str,
        *,
        command_id: str | None = None,
        expected_row_version: int | None = None,
        reason: str | None = None,
        suppress: bool = False,
    ) -> PreferenceInboxDecisionResult:
        proposal = self.journal.get_preference_proposal(self.workspace_id, proposal_id)
        if proposal is None:
            raise PreferenceInboxError("not_found", "Preference proposal is missing")
        if proposal.status is not PreferenceProposalStatus.PROPOSED:
            raise PreferenceInboxError("resolved", "Preference proposal is already resolved")
        if expected_row_version is not None and expected_row_version != proposal.row_version:
            raise PreferenceInboxError("stale", "Preference proposal row is stale")
        command = self._command(command_id, "reject", proposal_id)
        resolved = proposal.model_copy(
            update={
                "status": PreferenceProposalStatus.SUPPRESSED
                if suppress
                else PreferenceProposalStatus.REJECTED,
                "decision_command_id": command,
                "decision_reason": self._reason(reason),
                "resolved_at": self._now(),
                "row_version": proposal.row_version + 1,
            }
        )
        saved = self.journal.save_preference_proposal(
            self.workspace_id, resolved, expected_row_version=proposal.row_version
        )
        return PreferenceInboxDecisionResult((saved,))

    def reject_and_suppress(self, proposal_id: str, **kwargs) -> PreferenceInboxDecisionResult:
        kwargs["suppress"] = True
        return self.reject(proposal_id, **kwargs)

    def _accept_previews(
        self,
        previews: tuple[PreferenceDecisionPreview, ...],
        *,
        command_id: str | None,
    ) -> PreferenceInboxDecisionResult:
        if self.writer is None:
            raise PreferenceInboxError("unavailable", "Preference Writer is unavailable")
        if any(
            item.expected_document_revision != previews[0].expected_document_revision
            for item in previews[1:]
        ):
            raise PreferenceInboxError("stale", "Preference proposals have different OCC revisions")
        for preview in previews:
            current = self.journal.get_preference_proposal(
                self.workspace_id, preview.proposal.proposal_id
            )
            if (
                current is None
                or current.status is not PreferenceProposalStatus.PROPOSED
                or current.row_version != preview.expected_row_version
            ):
                raise PreferenceInboxError("stale", "Preference proposal row is stale")
        command = self._command(
            command_id, "accept", "+".join(item.proposal.proposal_id for item in previews)
        )
        scope = previews[0].operation.scope
        try:
            prepared = self.writer.prepare(
                scope,
                previews[0].expected_document_revision,
                command,
                tuple(item.operation for item in previews),
                proposal_ids=tuple(item.proposal.proposal_id for item in previews),
            )
            result = self.writer.apply(prepared)
        except PreferenceWriterError as exc:
            raise PreferenceInboxError(
                exc.code, "Preference proposal could not be applied"
            ) from exc
        saved: list[PreferenceProposal] = []
        for preview in previews:
            current = self.journal.get_preference_proposal(
                self.workspace_id, preview.proposal.proposal_id
            )
            if current is None:
                raise PreferenceInboxError("missing_reference", "Preference proposal disappeared")
            saved.append(current)
        return PreferenceInboxDecisionResult(
            tuple(saved),
            batch_id=result.batch.batch_id,
            document_revision=result.document.revision,
            replayed=result.replayed,
        )

    def _view(self, proposal: PreferenceProposal) -> PreferenceProposalView:
        evidence = self.journal.get_preference_evidence(self.workspace_id, proposal.evidence_id)
        current_document = self._current_document(proposal.operation.scope)
        current_target_revision = None
        current_document_revision = None
        stale_reason = None
        if current_document is None:
            stale_reason = "authority_unavailable"
        else:
            current_document_revision = current_document.revision
            target = next(
                (
                    entry
                    for entry in current_document.entries
                    if entry.preference_id == proposal.operation.preference_id
                ),
                None,
            )
            current_target_revision = target.revision if target is not None else None
            if current_document.revision != proposal.expected_document_revision:
                stale_reason = "document_revision"
            elif proposal.operation.preference_id is not None and (
                target is None or target.revision != proposal.expected_target_revision
            ):
                stale_reason = "target_revision" if target is not None else "target_missing"
        return PreferenceProposalView(
            proposal_id=proposal.proposal_id,
            job_id=proposal.job_id,
            evidence_id=proposal.evidence_id,
            operation=proposal.operation,
            status=proposal.status,
            expected_target_revision=proposal.expected_target_revision,
            expected_document_revision=proposal.expected_document_revision,
            row_version=proposal.row_version,
            evidence_excerpt=evidence.excerpt_redacted if evidence is not None else None,
            evidence_source_kind=evidence.source_kind if evidence is not None else None,
            stale=stale_reason is not None,
            stale_reason=stale_reason,
            current_target_revision=current_target_revision,
            current_document_revision=current_document_revision,
        )

    def _current_document(self, scope) -> PreferenceDocument | None:
        if self.writer is None:
            return None
        try:
            loaded = self.writer._load_authority(scope)
            if loaded.value is None:
                return None
            return self.writer._document_from_value(scope, loaded.value)
        except Exception:
            return None

    @staticmethod
    def _operation(
        proposal: PreferenceProposal,
        edit: str | PreferenceOperation | Mapping[str, object] | None,
    ) -> PreferenceOperation:
        if edit is None:
            return proposal.operation
        if isinstance(edit, str):
            if proposal.operation.operation is PreferenceOperationKind.REMOVE:
                raise PreferenceInboxError(
                    "invalid_edit", "remove proposals cannot add a statement"
                )
            operation = proposal.operation.model_copy(update={"statement": edit})
            if scan_learning_text(operation.statement or ""):
                raise PreferenceInboxError(
                    "safety_rejected", "edited Preference statement is unsafe"
                )
            return operation
        try:
            operation = (
                edit
                if isinstance(edit, PreferenceOperation)
                else PreferenceOperation.model_validate(edit, strict=True)
            )
        except (TypeError, ValueError) as exc:
            raise PreferenceInboxError(
                "invalid_edit", "edited Preference operation is invalid"
            ) from exc
        if (
            operation.scope is not proposal.operation.scope
            or operation.preference_id != proposal.operation.preference_id
            or operation.evidence_ids != proposal.operation.evidence_ids
            or operation.operation is not proposal.operation.operation
        ):
            raise PreferenceInboxError(
                "invalid_edit", "edited Preference operation changed its identity"
            )
        if operation.statement is not None and scan_learning_text(operation.statement):
            raise PreferenceInboxError("safety_rejected", "edited Preference statement is unsafe")
        return operation

    @staticmethod
    def _assert_preview_tokens(
        preview: PreferenceDecisionPreview,
        *,
        expected_row_version: int | None,
        expected_document_revision: int | None,
        expected_target_revision: int | None,
    ) -> None:
        if (
            expected_row_version is not None
            and expected_row_version != preview.expected_row_version
        ):
            raise PreferenceInboxError("stale", "Preference proposal row is stale")
        if (
            expected_document_revision is not None
            and expected_document_revision != preview.expected_document_revision
        ):
            raise PreferenceInboxError("stale", "Preference document revision is stale")
        if (
            expected_target_revision is not None
            and expected_target_revision != preview.expected_target_revision
        ):
            raise PreferenceInboxError("stale", "Preference target revision is stale")

    @staticmethod
    def _status(value):
        if value is None:
            return None
        try:
            return (
                value
                if isinstance(value, PreferenceProposalStatus)
                else PreferenceProposalStatus(value)
            )
        except (TypeError, ValueError) as exc:
            raise PreferenceInboxError(
                "invalid_status", "Preference proposal status is invalid"
            ) from exc

    @staticmethod
    def _offset(cursor: str | None, limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise PreferenceInboxError("invalid_page", "Preference Inbox page size is invalid")
        if cursor is None:
            return 0
        try:
            value = int(cursor)
        except (TypeError, ValueError) as exc:
            raise PreferenceInboxError(
                "invalid_cursor", "Preference Inbox cursor is invalid"
            ) from exc
        if value < 0:
            raise PreferenceInboxError("invalid_cursor", "Preference Inbox cursor is invalid")
        return value

    def _command(self, command_id: str | None, operation: str, proposal_id: str) -> str:
        if command_id is not None:
            return command_id
        if self.id_source is not None:
            return self.id_source.new_id("cmd")
        import hashlib

        return "cmd_" + hashlib.sha256(f"{operation}:{proposal_id}".encode()).hexdigest()[:32]

    def _now(self) -> datetime:
        if self.clock is None:
            return datetime.now(UTC)
        value = self.clock() if callable(self.clock) else self.clock.now()
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _reason(reason: str | None) -> str | None:
        if reason is None:
            return None
        if not isinstance(reason, str):
            raise PreferenceInboxError("invalid_reason", "Preference decision reason is invalid")
        cleaned = " ".join(reason.split())
        if not cleaned:
            return None
        return cleaned[:256]


PreferenceProposalInbox = PreferenceInbox


__all__ = [
    "PreferenceDecisionPreview",
    "PreferenceInbox",
    "PreferenceInboxDecisionResult",
    "PreferenceInboxError",
    "PreferenceProposalInbox",
    "PreferenceProposalView",
]
