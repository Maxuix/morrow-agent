"""Build bounded in-process context projections from durable AgentRuns."""

from __future__ import annotations

from morrow.core.context import FrozenProjectKnowledge, RunContextProjection
from morrow.core.domain import AgentRunSnapshot
from morrow.core.memory_rendering import (
    project_knowledge_block_digest,
    project_knowledge_content_digest,
    render_project_knowledge,
    render_project_knowledge_block,
)
from morrow.core.memory_selection import MemorySelection, MemorySelectionItem
from morrow.core.store import StorageError, StorageErrorCode

from .memory_selector import memory_selection_digest


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


def load_run_context_projection(
    txn,
    workspace_id: str,
    agent_run_id: str,
) -> RunContextProjection:
    """Load and verify one durable AgentRun's complete prompt-facing baseline."""

    run = txn.get_agent_run(workspace_id, agent_run_id)
    if run is None:
        raise StorageError(StorageErrorCode.NOT_FOUND, "AgentRun is missing")
    return build_run_context_projection(txn, workspace_id, run.snapshot)


def build_run_context_projection(
    txn,
    workspace_id: str,
    snapshot: AgentRunSnapshot,
) -> RunContextProjection:
    """Resolve exact immutable Knowledge revisions without consulting live eligibility."""

    selection = load_frozen_memory_selection(txn, workspace_id, snapshot)
    if selection is None:
        return RunContextProjection(snapshot=snapshot)
    if memory_selection_digest(selection) != selection.selection_digest:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "memory selection digest is invalid")

    selected: list[FrozenProjectKnowledge] = []
    for item in selection.selected_items:
        head, revision = _load_selected_revision(txn, workspace_id, item)
        rendered = render_project_knowledge(head, revision)
        rendered_digest = project_knowledge_content_digest(head, revision)
        if item.rendered_content_digest != rendered_digest:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR,
                "memory selection content digest does not match Knowledge",
            )
        selected.append(
            FrozenProjectKnowledge(
                head=head,
                revision=revision,
                rendered_content=rendered,
                rendered_content_digest=rendered_digest,
            )
        )

    if sum(len(item.rendered_content) for item in selected) != selection.rendered_chars:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR,
            "memory selection rendered-character total is invalid",
        )
    records = tuple((item.head, item.revision) for item in selected)
    block = render_project_knowledge_block(records)
    return RunContextProjection(
        snapshot=snapshot,
        memory_selection=selection,
        selected_knowledge=tuple(selected),
        memory_block=block,
        memory_content_digest=project_knowledge_block_digest(records),
    )


def _load_selected_revision(
    txn,
    workspace_id: str,
    item: MemorySelectionItem,
):
    head = txn.get_project_knowledge_head(workspace_id, item.record_id)
    revision = txn.get_project_knowledge_revision(workspace_id, item.record_revision_id)
    if head is None or revision is None:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "frozen Knowledge revision is missing")
    if (
        head.workspace_id != workspace_id
        or revision.workspace_id != workspace_id
        or revision.knowledge_id != head.knowledge_id
        or revision.revision != item.revision
    ):
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR,
            "frozen Knowledge revision does not match the selection",
        )
    return head, revision


__all__ = [
    "build_run_context_projection",
    "load_frozen_memory_selection",
    "load_run_context_projection",
]
