"""SQLite repository methods for Preference Inbox proposals."""

from __future__ import annotations

from morrow.adapters.state.preference_journal_codec import (
    _PROPOSAL_COLUMNS,
    _canonical_json,
    _missing,
    _optional_unix,
    _proposal_from_row,
    _unix,
    _workspace_error,
)
from morrow.core.preference_models import preference_operation_fingerprint
from morrow.core.preference_persistence_models import (
    PreferenceProposal,
    PreferenceProposalStatus,
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
    ) -> tuple[PreferenceProposal, ...]:
        if not 1 <= limit <= 500:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Preference proposal page is invalid")
        sql = f"SELECT {_PROPOSAL_COLUMNS} FROM preference_proposals WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if status is not None:
            sql += " AND status = ?"
            parameters.append(status.value)
        if job_id is not None:
            sql += " AND job_id = ?"
            parameters.append(job_id)
        sql += " ORDER BY created_at_unix ASC, proposal_id ASC LIMIT ?"
        parameters.append(limit)
        return tuple(
            _proposal_from_row(row) for row in self.backend.read_all(sql, tuple(parameters))
        )


__all__ = ["PreferenceProposalJournalMixin"]
