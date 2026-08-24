"""Build bounded in-process context projections from durable AgentRuns."""

from __future__ import annotations

from morrow.application.preferences.run_projection import render_frozen_run_preferences
from morrow.core.context import FrozenProjectKnowledge, RunContextProjection
from morrow.core.domain import AgentRunSnapshot, canonical_json_bytes, sha256_digest
from morrow.core.memory_rendering import (
    project_knowledge_block_digest,
    project_knowledge_content_digest,
    render_project_knowledge,
    render_project_knowledge_block,
)
from morrow.core.memory_selection import MemorySelection, MemorySelectionItem
from morrow.core.skills.context import SkillContextProjection, skill_context_projection_digest
from morrow.core.skills.selection import SkillSelection
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

    preference_block = ""
    preference_digest = snapshot.preference_projection_digest
    if preference_digest is not None:
        preference_block = render_frozen_run_preferences(snapshot.frozen_preferences)
        if sha256_digest(preference_block) != preference_digest:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR,
                "frozen Preference projection digest is invalid",
            )

    selection = load_frozen_memory_selection(txn, workspace_id, snapshot)
    skill_context = load_frozen_skill_context(txn, workspace_id, snapshot)
    if selection is None:
        return RunContextProjection(
            snapshot=snapshot,
            preference_block=preference_block,
            preference_content_digest=preference_digest,
            preference_omitted_count=snapshot.preference_omitted_count,
            preference_source_scopes=snapshot.preference_source_scopes,
            skill_context=skill_context,
        )
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
        preference_block=preference_block,
        preference_content_digest=preference_digest,
        preference_omitted_count=snapshot.preference_omitted_count,
        preference_source_scopes=snapshot.preference_source_scopes,
        skill_context=skill_context,
    )


def load_frozen_skill_context(
    txn,
    workspace_id: str,
    snapshot: AgentRunSnapshot,
) -> SkillContextProjection | None:
    """Load Skill rows by snapshot references, never by current Catalog/Binding."""

    if not snapshot.skill_selection_ids and snapshot.skill_omitted_count == 0:
        if snapshot.skill_context_ids or snapshot.skill_context_digest is not None:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, "Skill context references are incomplete"
            )
        return None
    selections: list[SkillSelection] = []
    for selection_id in snapshot.skill_selection_ids:
        selection = txn.get_skill_selection(workspace_id, selection_id)
        if selection is None or selection.agent_run_id != _snapshot_agent_run_id(
            txn, workspace_id, snapshot
        ):
            raise StorageError(StorageErrorCode.NEEDS_REPAIR, "frozen Skill selection is missing")
        selections.append(selection)
    expected_selection_digest = sha256_digest(
        canonical_json_bytes(
            {
                "selections": [item.model_dump(mode="json") for item in selections],
                "omitted_count": snapshot.skill_omitted_count,
            }
        )
    )
    if snapshot.skill_selection_digest != expected_selection_digest:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "frozen Skill selection digest is invalid"
        )
    if snapshot.skill_selected_count != len(selections):
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "frozen Skill selection count is invalid")
    if len(snapshot.skill_context_ids) != len(selections):
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "frozen Skill context count is invalid")
    by_selection = {item.selection_id: item for item in selections}
    contexts = []
    for context_id in snapshot.skill_context_ids:
        context = txn.get_skill_context(workspace_id, context_id)
        if context is None:
            raise StorageError(StorageErrorCode.NEEDS_REPAIR, "frozen Skill context is missing")
        selection = by_selection.get(context.selection_id)
        if selection is None or context.agent_run_id != selection.agent_run_id:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, "Skill context selection reference is invalid"
            )
        if (
            context.skill_id != selection.skill_id
            or context.version_id != selection.version_id
            or context.scope != selection.scope
            or context.scope_id != selection.scope_id
            or context.tree_digest != "0" * 64
            and context.tree_digest != selection.tree_digest
        ):
            raise StorageError(StorageErrorCode.NEEDS_REPAIR, "Skill context identity is invalid")
        contexts.append(context.model_copy(update={"tree_digest": selection.tree_digest}))
    ordered = tuple(contexts)
    expected_context_digest = skill_context_projection_digest(ordered)
    if snapshot.skill_context_digest != expected_context_digest:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "frozen Skill context digest is invalid")
    return SkillContextProjection(
        entries=ordered,
        omitted_count=sum(item.omitted_count for item in ordered),
        projection_digest=expected_context_digest,
    )


def _snapshot_agent_run_id(txn, workspace_id: str, snapshot: AgentRunSnapshot) -> str:
    # The snapshot intentionally does not carry its owner ID. Resolve it only
    # through the immutable references already supplied by the journal.
    if not snapshot.skill_selection_ids:
        return ""
    first = txn.get_skill_selection(workspace_id, snapshot.skill_selection_ids[0])
    return first.agent_run_id if first is not None else ""


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
