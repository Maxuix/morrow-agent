"""Read-only invariants for v12 Memory selections and derived terms."""

from __future__ import annotations

from collections.abc import Callable

from morrow.application.learning.memory_run_projection import load_run_context_projection
from morrow.application.learning.memory_selector import memory_selection_digest
from morrow.application.learning.memory_terms import terms_for_project_knowledge_revision
from morrow.core.doctor import DoctorIssue, DoctorSeverity
from morrow.core.learning import LearningSensitivity
from morrow.core.learning_memory import ProjectKnowledgeStatus
from morrow.core.memory_rendering import project_knowledge_content_digest, render_project_knowledge
from morrow.core.store import StorageError


def inspect_memory(
    journal,
    workspace_id: str,
    counts,
    issues: list[DoctorIssue],
    *,
    issue_factory: Callable[..., DoctorIssue],
) -> None:
    """Check durable selection references without changing the store."""

    selections = journal.list_memory_selections(workspace_id, limit=500)
    counts["memory_selections"] = len(selections)
    counts["memory_selection_items"] = sum(selection.item_count for selection in selections)
    for selection in selections:
        _inspect_selection(journal, workspace_id, selection, issues, issue_factory)

    heads = journal.list_project_knowledge_heads(
        workspace_id,
        include_deleted=True,
        limit=500,
    )
    counts["memory_knowledge_heads"] = len(heads)
    _inspect_terms(journal, workspace_id, heads, issues, issue_factory)
    terms = journal.list_memory_search_terms(workspace_id, limit=500)
    counts["memory_search_terms"] = len(terms)
    current_revision_ids = {
        head.current_revision_id for head in heads if head.current_revision_id is not None
    }
    for term in terms:
        revision = journal.get_project_knowledge_revision(workspace_id, term.knowledge_revision_id)
        if revision is None or term.knowledge_revision_id not in current_revision_ids:
            issues.append(
                issue_factory(
                    "memory_terms_stale",
                    DoctorSeverity.ERROR,
                    "derived Memory term references a non-current Knowledge revision",
                )
            )

    counts["memory_agent_runs"] = 0
    for session in journal.list_sessions(workspace_id):
        for run in journal.list_session_agent_runs(workspace_id, session.session_id):
            if run.snapshot.memory_selection_id is None:
                continue
            counts["memory_agent_runs"] += 1
            try:
                load_run_context_projection(journal, workspace_id, run.agent_run_id)
            except StorageError:
                issues.append(
                    issue_factory(
                        "memory_agent_run_projection",
                        DoctorSeverity.ERROR,
                        "AgentRun frozen Memory projection is inconsistent",
                    )
                )
            except Exception:
                issues.append(
                    issue_factory(
                        "memory_agent_run_projection",
                        DoctorSeverity.ERROR,
                        "AgentRun frozen Memory projection is unreadable",
                    )
                )


def _inspect_selection(journal, workspace_id, selection, issues, issue_factory) -> None:
    if memory_selection_digest(selection) != selection.selection_digest:
        issues.append(
            issue_factory(
                "memory_selection_digest",
                DoctorSeverity.ERROR,
                "Memory selection digest does not match its immutable contents",
            )
        )
    rendered_chars = 0
    for item in selection.selected_items:
        head = journal.get_project_knowledge_head(workspace_id, item.record_id)
        revision = journal.get_project_knowledge_revision(workspace_id, item.record_revision_id)
        if (
            head is None
            or revision is None
            or head.workspace_id != workspace_id
            or revision.workspace_id != workspace_id
            or revision.knowledge_id != head.knowledge_id
            or revision.revision != item.revision
        ):
            issues.append(
                issue_factory(
                    "memory_selection_reference",
                    DoctorSeverity.ERROR,
                    "Memory selection references a missing or cross-workspace Knowledge revision",
                )
            )
            continue
        rendered_chars += len(render_project_knowledge(head, revision))
        if project_knowledge_content_digest(head, revision) != item.rendered_content_digest:
            issues.append(
                issue_factory(
                    "memory_selection_content_digest",
                    DoctorSeverity.ERROR,
                    "Memory selection content digest does not match Knowledge",
                )
            )
        if (
            head.status is not ProjectKnowledgeStatus.ACTIVE
            and selection.created_at > head.updated_at
        ):
            issues.append(
                issue_factory(
                    "memory_selection_inactive_reference",
                    DoctorSeverity.ERROR,
                    "new Memory selection includes inactive Project Knowledge",
                )
            )
    if rendered_chars != selection.rendered_chars:
        issues.append(
            issue_factory(
                "memory_selection_rendered_chars",
                DoctorSeverity.ERROR,
                "Memory selection rendered-character total is inconsistent",
            )
        )


def _inspect_terms(journal, workspace_id, heads, issues, issue_factory) -> None:
    for head in heads:
        if head.current_revision_id is None:
            continue
        revision = journal.get_project_knowledge_revision(workspace_id, head.current_revision_id)
        if revision is None:
            issues.append(
                issue_factory(
                    "memory_current_revision",
                    DoctorSeverity.ERROR,
                    "Project Knowledge head points to a missing current revision",
                )
            )
            continue
        expected = ()
        if (
            head.status is ProjectKnowledgeStatus.ACTIVE
            and revision.sensitivity is not LearningSensitivity.PROHIBITED
        ):
            expected = terms_for_project_knowledge_revision(revision, head)
        actual = journal.list_memory_search_terms(
            workspace_id,
            knowledge_revision_id=revision.knowledge_revision_id,
            limit=500,
        )
        expected_keys = {
            (term.token_kind.value, term.token, term.weight_band.value) for term in expected
        }
        actual_keys = {
            (term.token_kind.value, term.token, term.weight_band.value) for term in actual
        }
        if actual_keys != expected_keys:
            issues.append(
                issue_factory(
                    "memory_terms_rebuild",
                    DoctorSeverity.ERROR,
                    "derived Memory terms do not match immutable Knowledge",
                )
            )


__all__ = ["inspect_memory"]
