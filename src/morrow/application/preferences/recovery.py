"""Deterministic recovery for prepared Preference Writer batches."""

from __future__ import annotations

from dataclasses import dataclass

from morrow.application.preferences.writer import (
    PreferenceWriter,
    PreferenceWriterError,
    PreferenceWriteResult,
    PreferenceWriterNeedsResolution,
)
from morrow.core.preference_persistence_models import PreferenceWriteBatchStatus


@dataclass(frozen=True)
class PreferenceRecoveryReport:
    """Bounded outcome for one recovery sweep."""

    finalized_batch_ids: tuple[str, ...] = ()
    unresolved_batch_ids: tuple[str, ...] = ()


class PreferenceWriteRecovery:
    """Retry only durable Writer states whose outcome is still deterministic."""

    def __init__(self, writer: PreferenceWriter) -> None:
        self.writer = writer

    def recover_batch(self, batch_id: str) -> PreferenceWriteResult:
        batch = self.writer.journal.get_preference_write_batch(self.writer.workspace_id, batch_id)
        if batch is None:
            raise PreferenceWriterError("not_found", "Preference write batch is missing")
        return self.writer.apply(batch)

    def recover_pending(self, *, limit: int = 100) -> PreferenceRecoveryReport:
        batches = self.writer.journal.list_preference_write_batches(
            self.writer.workspace_id, limit=limit
        )
        finalized: list[str] = []
        unresolved: list[str] = []
        for batch in batches:
            if batch.status not in {
                PreferenceWriteBatchStatus.PREPARED,
                PreferenceWriteBatchStatus.YAML_APPLIED,
            } and not (batch.status is PreferenceWriteBatchStatus.FINALIZED and batch.proposal_ids):
                continue
            try:
                result = self.writer.apply(batch)
            except PreferenceWriterNeedsResolution:
                unresolved.append(batch.batch_id)
            except PreferenceWriterError:
                unresolved.append(batch.batch_id)
            else:
                if result.batch.status is PreferenceWriteBatchStatus.FINALIZED:
                    finalized.append(batch.batch_id)
        return PreferenceRecoveryReport(tuple(finalized), tuple(unresolved))


__all__ = ["PreferenceRecoveryReport", "PreferenceWriteRecovery"]
