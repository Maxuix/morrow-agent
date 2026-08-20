"""Validation helpers for frozen AgentRun-to-MemorySelection references."""

from __future__ import annotations

from morrow.core.domain import AgentRunSnapshot
from morrow.core.memory_selection import MemorySelection
from morrow.core.store import StorageError, StorageErrorCode


def load_frozen_memory_selection(
    txn,
    workspace_id: str,
    snapshot: AgentRunSnapshot,
) -> MemorySelection | None:
    """Load and verify the exact selection referenced by a durable snapshot."""

    if snapshot.memory_selection_id is None:
        return None
    selection = txn.get_memory_selection(workspace_id, snapshot.memory_selection_id)
    if selection is None:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "frozen memory selection is missing")
    if (
        selection.workspace_id != workspace_id
        or selection.selection_digest != snapshot.memory_selection_digest
        or selection.source_memory_revision != snapshot.memory_snapshot_revision
    ):
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR,
            "frozen memory selection does not match the AgentRun snapshot",
        )
    return selection


__all__ = ["load_frozen_memory_selection"]
