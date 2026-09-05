"""SQLite repository methods for Preference Inbox proposals."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from morrow.adapters.state.preference_journal_codec import (
    _PROPOSAL_COLUMNS,
    _canonical_json,
    _missing,
    _optional_unix,
    _proposal_from_row,
    _stale,
    _unix,
    _workspace_error,
)
from morrow.core.preference_models import PreferenceOperation
from morrow.core.preference_persistence_models import (
    PreferenceProposal,
    PreferenceProposalStatus,
    preference_operation_fingerprint,
)
from morrow.core.store import StorageError, StorageErrorCode


class PreferenceProposalJournalMixin:
    """Proposal persistence surface mixed into the public journal."""

    def get_preference_proposal(
        self, workspace_id: str, proposal_id: str
    ) -> PreferenceProposal | None:
        row = self.backend.read_one(
            f"SELECT {_PROPOSAL_COLUMNS} FROM preference_proposals WHERE proposal_id = ?",
            (proposal_id,),
        )
        if row is None:
            return None
        if str(row[1]) != workspace_id:
            raise _workspace_error("proposal")
        return _proposal_from_row(row)

    def put_preference_proposal(
        self, workspace_id: str, proposal: PreferenceProposal
    ) -> PreferenceProposal:
        if proposal.workspace_id != workspace_id:
            raise _workspace_error("proposal")
        if proposal.operation.evidence_ids != (proposal.evidence_id,):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "Preference proposal Evidence is not exact"
            )
        if preference_operation_fingerprint(proposal.operation) != proposal.fingerprint:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "Preference proposal fingerprint is invalid"
            )
        operation_json, operation_bytes = _canonical_json(
            proposal.operation.model_dump(mode="json"), maximum=8192, label="proposal operation"
        )
        final_json = final_bytes = None
        if proposal.final_operation is not None:
            if proposal.final_operation.evidence_ids != (proposal.evidence_id,):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "edited Preference Evidence is not exact"
                )
            final_json, final_bytes = _canonical_json(
                proposal.final_operation.model_dump(mode="json"),
                maximum=8192,
                label="final operation",
            )

        def work() -> PreferenceProposal:
            job = self.get_preference_review_job(workspace_id, proposal.job_id)
            evidence = self.get_preference_evidence(workspace_id, proposal.evidence_id)
            if job is None:
                raise _missing("Review job")
            if evidence is None or evidence.job_id != proposal.job_id:
                raise _missing("Preference Evidence")
            if self.get_preference_proposal(workspace_id, proposal.proposal_id) is not None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "Preference proposal already exists"
                )
            self.backend.executor().execute(
                f"INSERT INTO preference_proposals({_PROPOSAL_COLUMNS}) VALUES ({','.join('?' for _ in range(20))})",
                (
                    proposal.proposal_id,
                    proposal.workspace_id,
                    proposal.job_id,
                    proposal.evidence_id,
                    proposal.operation.operation.value,
                    proposal.operation.scope.value,
                    proposal.operation.preference_id,
                    operation_json,
                    operation_bytes,
                    proposal.fingerprint,
                    proposal.expected_target_revision,
                    proposal.expected_document_revision,
                    proposal.status.value,
                    final_json,
                    final_bytes,
                    proposal.decision_command_id,
                    proposal.decision_reason,
                    proposal.row_version,
                    _unix(proposal.created_at),
                    _optional_unix(proposal.resolved_at),
                ),
            )
            self.backend.executor().execute(
                "INSERT INTO preference_proposal_evidence(workspace_id, proposal_id, evidence_id) VALUES (?, ?, ?)",
                (workspace_id, proposal.proposal_id, proposal.evidence_id),
            )
            loaded = self.get_preference_proposal(workspace_id, proposal.proposal_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "Preference proposal could not be read"
                )
            return loaded

        return self.backend.transact(work)

    def list_preference_proposals(
        self,
        workspace_id: str,
        *,
        status: PreferenceProposalStatus | None = None,
        job_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[PreferenceProposal, ...]:
        if not 1 <= limit <= 500:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Preference proposal page is invalid")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "page offset is invalid")
        sql = f"SELECT {_PROPOSAL_COLUMNS} FROM preference_proposals WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if status is not None:
            sql += " AND status = ?"
            parameters.append(status.value)
        if job_id is not None:
            sql += " AND job_id = ?"
            parameters.append(job_id)
        sql += " ORDER BY created_at_unix ASC, proposal_id ASC LIMIT ? OFFSET ?"
        parameters.extend((limit, offset))
        return tuple(
            _proposal_from_row(row) for row in self.backend.read_all(sql, tuple(parameters))
        )

    def count_preference_proposals(self, workspace_id: str, *, status=None) -> int:
        sql = "SELECT COUNT(*) FROM preference_proposals WHERE workspace_id = ?"
        params: list[object] = [workspace_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status.value)
        return int(self.backend.read_one(sql, tuple(params))[0])

    def has_preference_proposal_fingerprint(
        self,
        workspace_id: str,
        fingerprint: str,
        *,
        status: PreferenceProposalStatus | None = None,
    ) -> bool:
        sql = "SELECT 1 FROM preference_proposals WHERE workspace_id = ? AND operation_digest = ?"
        parameters: list[object] = [workspace_id, fingerprint]
        if status is not None:
            sql += " AND status = ?"
            parameters.append(status.value)
        sql += " LIMIT 1"
        return self.backend.read_one(sql, tuple(parameters)) is not None

    def save_preference_proposal(
        self,
        workspace_id: str,
        proposal: PreferenceProposal,
        *,
        expected_row_version: int,
    ) -> PreferenceProposal:
        """Save one Inbox decision with immutable proposal identity and OCC."""

        if proposal.workspace_id != workspace_id:
            raise _workspace_error("proposal")
        if proposal.operation.evidence_ids != (proposal.evidence_id,):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "Preference proposal Evidence is not exact"
            )
        if preference_operation_fingerprint(proposal.operation) != proposal.fingerprint:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "Preference proposal fingerprint is invalid"
            )
        final_json = final_bytes = None
        if proposal.final_operation is not None:
            if proposal.final_operation.evidence_ids != (proposal.evidence_id,):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "edited operation Evidence is not exact"
                )
            final_json, final_bytes = _canonical_json(
                proposal.final_operation.model_dump(mode="json"),
                maximum=8192,
                label="final operation",
            )

        def work() -> PreferenceProposal:
            existing = self.get_preference_proposal(workspace_id, proposal.proposal_id)
            if existing is None:
                raise _missing("proposal")
            if (
                existing.row_version != expected_row_version
                or proposal.row_version != expected_row_version + 1
            ):
                raise _stale("proposal")
            immutable = (
                "proposal_id",
                "workspace_id",
                "job_id",
                "evidence_id",
                "operation",
                "fingerprint",
                "expected_target_revision",
                "expected_document_revision",
                "created_at",
            )
            if any(getattr(existing, name) != getattr(proposal, name) for name in immutable):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "Preference proposal identity is immutable"
                )
            if existing.status is not PreferenceProposalStatus.PROPOSED:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "Preference proposal is already resolved"
                )
            if proposal.status is PreferenceProposalStatus.PROPOSED:
                if proposal.final_operation is not None or proposal.resolved_at is not None:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE, "proposed Preference cannot have a decision"
                    )
            elif proposal.decision_command_id is None or proposal.resolved_at is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "resolved Preference proposal lacks decision metadata",
                )
            self.backend.executor().execute(
                """
                UPDATE preference_proposals
                SET status = ?, final_operation_json = ?, final_operation_bytes = ?,
                    decision_command_id = ?, decision_reason = ?, row_version = ?,
                    resolved_at_unix = ?
                WHERE proposal_id = ? AND workspace_id = ? AND row_version = ?
                """,
                (
                    proposal.status.value,
                    final_json,
                    final_bytes,
                    proposal.decision_command_id,
                    proposal.decision_reason,
                    proposal.row_version,
                    _optional_unix(proposal.resolved_at),
                    proposal.proposal_id,
                    workspace_id,
                    expected_row_version,
                ),
            )
            loaded = self.get_preference_proposal(workspace_id, proposal.proposal_id)
            if loaded is None or loaded.row_version != proposal.row_version:
                raise _stale("proposal")
            return loaded

        return self.backend.transact(work)

    def finalize_preference_proposals(
        self,
        workspace_id: str,
        proposal_ids: Sequence[str],
        *,
        command_id: str,
        operations: Sequence[PreferenceOperation],
        resolved_at: datetime,
    ) -> tuple[PreferenceProposal, ...]:
        """Finalize all Writer-linked proposals in one SQLite transaction.

        A finalized Writer batch can be replayed after a process crash.  Rows already carrying the
        same command and final operation are treated as idempotent; a different decision is stale.
        """

        proposal_tuple = tuple(proposal_ids)
        operation_tuple = tuple(operations)
        if not proposal_tuple or len(proposal_tuple) != len(operation_tuple):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "Preference write batch proposal mapping is invalid",
            )
        if len(set(proposal_tuple)) != len(proposal_tuple):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "Preference write batch proposals must be unique",
            )

        def work() -> tuple[PreferenceProposal, ...]:
            changes: list[
                tuple[PreferenceProposal, PreferenceProposal, str | None, int | None]
            ] = []
            resolved: dict[str, PreferenceProposal] = {}
            for proposal_id, operation in zip(proposal_tuple, operation_tuple, strict=True):
                existing = self.get_preference_proposal(workspace_id, proposal_id)
                if existing is None:
                    raise _missing("write batch proposal")
                if existing.status is not PreferenceProposalStatus.PROPOSED:
                    expected_final = None if operation == existing.operation else operation
                    expected_status = (
                        PreferenceProposalStatus.ACCEPTED
                        if expected_final is None
                        else PreferenceProposalStatus.EDITED_AND_ACCEPTED
                    )
                    if (
                        existing.status is expected_status
                        and existing.final_operation == expected_final
                        and existing.decision_command_id == command_id
                    ):
                        resolved[proposal_id] = existing
                        continue
                    raise _stale("proposal")
                if (
                    operation.scope is not existing.operation.scope
                    or operation.operation is not existing.operation.operation
                    or operation.preference_id != existing.operation.preference_id
                    or operation.evidence_ids != existing.operation.evidence_ids
                ):
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE,
                        "Preference write batch operation changed proposal identity",
                    )
                final_operation = None if operation == existing.operation else operation
                status = (
                    PreferenceProposalStatus.ACCEPTED
                    if final_operation is None
                    else PreferenceProposalStatus.EDITED_AND_ACCEPTED
                )
                changed = PreferenceProposal.model_validate(
                    existing.model_dump(mode="python")
                    | {
                        "status": status,
                        "final_operation": final_operation,
                        "decision_command_id": command_id,
                        "decision_reason": None,
                        "resolved_at": resolved_at,
                        "row_version": existing.row_version + 1,
                    }
                )
                final_json = final_bytes = None
                if final_operation is not None:
                    final_json, final_bytes = _canonical_json(
                        final_operation.model_dump(mode="json"),
                        maximum=8192,
                        label="final operation",
                    )
                changes.append((existing, changed, final_json, final_bytes))

            executor = self.backend.executor()
            for existing, changed, final_json, final_bytes in changes:
                executor.execute(
                    """
                    UPDATE preference_proposals
                    SET status = ?, final_operation_json = ?, final_operation_bytes = ?,
                        decision_command_id = ?, decision_reason = ?, row_version = ?,
                        resolved_at_unix = ?
                    WHERE proposal_id = ? AND workspace_id = ? AND row_version = ?
                    """,
                    (
                        changed.status.value,
                        final_json,
                        final_bytes,
                        changed.decision_command_id,
                        changed.decision_reason,
                        changed.row_version,
                        _optional_unix(changed.resolved_at),
                        existing.proposal_id,
                        workspace_id,
                        existing.row_version,
                    ),
                )
                loaded = self.get_preference_proposal(workspace_id, existing.proposal_id)
                if loaded is None or loaded.row_version != changed.row_version:
                    raise _stale("proposal")
                resolved[existing.proposal_id] = loaded
            return tuple(resolved[proposal_id] for proposal_id in proposal_tuple)

        return self.backend.transact(work)


__all__ = ["PreferenceProposalJournalMixin"]
