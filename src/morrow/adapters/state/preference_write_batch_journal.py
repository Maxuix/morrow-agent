"""SQLite repository methods for prepared Preference Writer batches."""

from __future__ import annotations

from morrow.adapters.state.preference_journal_codec import (
    _BATCH_COLUMNS,
    _batch_from_row,
    _canonical_json,
    _missing,
    _optional_unix,
    _unix,
    _workspace_error,
)
from morrow.core.preference_persistence_models import (
    PreferenceWriteBatch,
    PreferenceWriteBatchStatus,
)
from morrow.core.store import StorageError, StorageErrorCode


class PreferenceWriteBatchJournalMixin:
    """Writer-batch persistence surface mixed into the public journal."""

    def put_preference_write_batch(
        self, workspace_id: str, batch: PreferenceWriteBatch
    ) -> PreferenceWriteBatch:
        if batch.workspace_id != workspace_id:
            raise _workspace_error("write batch")
        operations_json, operations_bytes = _canonical_json(
            [operation.model_dump(mode="json") for operation in batch.operations],
            maximum=196608,
            label="write batch operations",
        )
        allocated_json, _ = _canonical_json(
            list(batch.allocated_add_ids), maximum=4096, label="allocated Preference IDs"
        )
        proposal_json, _ = _canonical_json(
            list(batch.proposal_ids), maximum=8192, label="proposal IDs"
        )

        def work() -> PreferenceWriteBatch:
            if (
                self.backend.read_one(
                    "SELECT batch_id FROM preference_write_batches WHERE batch_id = ?",
                    (batch.batch_id,),
                )
                is not None
            ):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "Preference write batch already exists"
                )
            for proposal_id in batch.proposal_ids:
                proposal = self.get_preference_proposal(workspace_id, proposal_id)
                if proposal is None:
                    raise _missing("write batch proposal")
                if proposal.operation.scope is not batch.scope:
                    raise _workspace_error("write batch proposal scope")
            self.backend.executor().execute(
                f"INSERT INTO preference_write_batches({_BATCH_COLUMNS}) VALUES ({','.join('?' for _ in range(22))})",
                (
                    batch.batch_id,
                    batch.workspace_id,
                    batch.scope.value,
                    batch.command_id,
                    operations_json,
                    operations_bytes,
                    allocated_json,
                    proposal_json,
                    batch.expected_document_revision,
                    batch.before_document_revision,
                    batch.before_document_digest,
                    batch.after_document_revision,
                    batch.after_document_digest,
                    batch.before_document_json,
                    batch.after_document_json,
                    batch.status.value,
                    batch.recovery_code,
                    batch.row_version,
                    _unix(batch.created_at),
                    _optional_unix(batch.prepared_at),
                    _optional_unix(batch.applied_at),
                    _optional_unix(batch.finalized_at),
                ),
            )
            for proposal_id in batch.proposal_ids:
                self.backend.executor().execute(
                    "INSERT INTO preference_write_batch_proposals(workspace_id, batch_id, proposal_id) VALUES (?, ?, ?)",
                    (workspace_id, batch.batch_id, proposal_id),
                )
            loaded = self.get_preference_write_batch(workspace_id, batch.batch_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "Preference write batch could not be read"
                )
            return loaded

        return self.backend.transact(work)

    def get_preference_write_batch(
        self, workspace_id: str, batch_id: str
    ) -> PreferenceWriteBatch | None:
        row = self.backend.read_one(
            f"SELECT {_BATCH_COLUMNS} FROM preference_write_batches WHERE batch_id = ?",
            (batch_id,),
        )
        if row is None:
            return None
        if str(row[1]) != workspace_id:
            raise _workspace_error("write batch")
        return _batch_from_row(row)

    def list_preference_write_batches(
        self,
        workspace_id: str,
        *,
        status: PreferenceWriteBatchStatus | None = None,
        limit: int = 100,
    ) -> tuple[PreferenceWriteBatch, ...]:
        if not 1 <= limit <= 500:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "Preference write batch page is invalid"
            )
        sql = f"SELECT {_BATCH_COLUMNS} FROM preference_write_batches WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if status is not None:
            sql += " AND status = ?"
            parameters.append(status.value)
        sql += " ORDER BY created_at_unix ASC, batch_id ASC LIMIT ?"
        parameters.append(limit)
        return tuple(_batch_from_row(row) for row in self.backend.read_all(sql, tuple(parameters)))


__all__ = ["PreferenceWriteBatchJournalMixin"]
