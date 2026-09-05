"""Read-only Project Knowledge inspection for the Stage 5 Memory surface."""

from __future__ import annotations

from morrow.application.api_context import ApplicationCommandContext
from morrow.application.learning.inbox import _offset
from morrow.application.learning.lifecycle import MemoryLifecycleService
from morrow.core.application import ApplicationError, ApplicationErrorCode, QueryPage
from morrow.core.domain import validate_prefixed_id
from morrow.core.learning_memory import ProjectKnowledgeCategory, ProjectKnowledgeStatus
from morrow.core.learning_views import (
    LEARNING_QUERY_MAX_EVIDENCE,
    LEARNING_QUERY_MAX_TIMELINE,
    LearningEvidenceSummary,
    ProjectKnowledgeSummary,
    ProjectKnowledgeView,
)
from morrow.core.memory_selection import MEMORY_SELECTION_ID_PREFIX, MemorySelection
from morrow.core.memory_views import MemorySelectionSummary, MemorySelectionView


class MemoryApplicationService:
    """Expose Project Knowledge without exposing the SQLite memory repository."""

    def __init__(self, context: ApplicationCommandContext) -> None:
        self.context = context
        self.lifecycle = MemoryLifecycleService(context)

    @property
    def workspace_id(self) -> str:
        return self.context.workspace_id

    @property
    def journal(self):
        return self.context.journal

    def list_knowledge(
        self,
        *,
        status: ProjectKnowledgeStatus | str | None = None,
        category: ProjectKnowledgeCategory | str | None = None,
        include_deleted: bool = False,
        cursor: str | None = None,
        limit: int = 50,
    ) -> QueryPage[ProjectKnowledgeSummary]:
        selected_status = self._status(status)
        selected_category = self._category(category)
        offset = _offset(cursor, limit)
        heads = self.context._query(
            lambda: self.journal.list_project_knowledge_heads(
                self.workspace_id,
                status=selected_status,
                category=selected_category,
                include_deleted=include_deleted,
                limit=limit,
                offset=offset,
            )
        )
        page = tuple(
            ProjectKnowledgeSummary(
                head=head,
                current_revision=self._current_revision(head.current_revision_id),
            )
            for head in heads
        )
        next_cursor = str(offset + len(page)) if len(page) == limit else None
        return QueryPage(page, next_cursor)

    def get_knowledge(
        self, knowledge_id: str, *, revision: int | None = None
    ) -> ProjectKnowledgeView | None:
        head = self.context._query(
            lambda: self.journal.get_project_knowledge_head(self.workspace_id, knowledge_id)
        )
        if head is None:
            return None
        if revision is not None and (
            isinstance(revision, bool) or not isinstance(revision, int) or revision < 1
        ):
            raise ApplicationError(ApplicationErrorCode.INVALID, "knowledge revision is invalid")
        revisions = self.context._query(
            lambda: self.journal.list_project_knowledge_revisions(
                self.workspace_id,
                head.knowledge_id,
                limit=500 if revision is not None else LEARNING_QUERY_MAX_TIMELINE,
            )
        )
        selected = (
            next((item for item in revisions if item.revision == revision), None)
            if revision is not None
            else self._current_revision(head.current_revision_id, revisions=revisions)
        )
        if revision is not None and selected is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "knowledge revision is missing")
        timeline = revisions[-LEARNING_QUERY_MAX_TIMELINE:]
        evidence = ()
        if selected is not None:
            links = self.context._query(
                lambda: self.journal.list_project_knowledge_evidence(
                    self.workspace_id, selected.knowledge_revision_id
                )
            )[:LEARNING_QUERY_MAX_EVIDENCE]
            evidence = tuple(
                LearningEvidenceSummary.from_evidence(evidence_item)
                for link in links
                for evidence_item in (
                    self.context._query(
                        lambda link=link: self.journal.get_learning_evidence(
                            self.workspace_id, link.evidence_id
                        )
                    ),
                )
                if evidence_item is not None
            )
        return ProjectKnowledgeView(
            head=head,
            revision=selected,
            timeline=tuple(timeline),
            evidence=evidence,
        )

    def list_selections(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> QueryPage[MemorySelectionSummary]:
        offset = _offset(cursor, limit)
        selections = self.context._query(
            lambda: self.journal.list_memory_selections(
                self.workspace_id,
                limit=min(500, offset + limit),
            )
        )
        refs = self._selection_agent_runs(selections)
        page = tuple(
            MemorySelectionSummary.from_selection(
                selection,
                agent_run_ids=refs.get(selection.selection_id, ()),
            )
            for selection in selections[offset : offset + limit]
        )
        next_cursor = str(offset + len(page)) if offset + len(page) < len(selections) else None
        return QueryPage(page, next_cursor)

    def get_selection(self, selection_id: str) -> MemorySelectionView | None:
        try:
            selection_id = validate_prefixed_id(selection_id, MEMORY_SELECTION_ID_PREFIX)
        except ValueError as exc:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "memory selection ID is invalid"
            ) from exc
        selection = self.context._query(
            lambda: self.journal.get_memory_selection(self.workspace_id, selection_id)
        )
        if selection is None:
            return None
        refs = self._selection_agent_runs((selection,))
        return MemorySelectionView(
            selection=selection,
            agent_run_ids=refs.get(selection.selection_id, ()),
        )

    def disable_knowledge(self, command):
        return self.lifecycle.disable_knowledge(command)

    def enable_knowledge(self, command):
        return self.lifecycle.enable_knowledge(command)

    def mark_disputed(self, command):
        return self.lifecycle.mark_disputed(command)

    def delete_knowledge(self, command):
        return self.lifecycle.delete_knowledge(command)

    def _selection_agent_runs(
        self, selections: tuple[MemorySelection, ...]
    ) -> dict[str, tuple[str, ...]]:
        selection_ids = {selection.selection_id for selection in selections}
        refs: dict[str, list[str]] = {selection_id: [] for selection_id in selection_ids}
        if not selection_ids:
            return {}
        sessions = self.context._query(lambda: self.journal.list_sessions(self.workspace_id))
        for session in sessions:
            runs = self.context._query(
                lambda session=session: self.journal.list_session_agent_runs(
                    self.workspace_id, session.session_id
                )
            )
            for run in runs:
                selection_id = run.snapshot.memory_selection_id
                if selection_id in refs:
                    refs[selection_id].append(run.agent_run_id)
        return {selection_id: tuple(values) for selection_id, values in refs.items()}

    def _current_revision(self, revision_id: str | None, *, revisions=()):
        if revision_id is None:
            return None
        found = next(
            (item for item in revisions if item.knowledge_revision_id == revision_id), None
        )
        if found is not None:
            return found
        return self.context._query(
            lambda: self.journal.get_project_knowledge_revision(self.workspace_id, revision_id)
        )

    @staticmethod
    def _status(value: ProjectKnowledgeStatus | str | None) -> ProjectKnowledgeStatus | None:
        if value is None or isinstance(value, ProjectKnowledgeStatus):
            return value
        try:
            return ProjectKnowledgeStatus(value)
        except (TypeError, ValueError) as exc:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "knowledge status is invalid"
            ) from exc

    @staticmethod
    def _category(value: ProjectKnowledgeCategory | str | None) -> ProjectKnowledgeCategory | None:
        if value is None or isinstance(value, ProjectKnowledgeCategory):
            return value
        try:
            return ProjectKnowledgeCategory(value)
        except (TypeError, ValueError) as exc:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "knowledge category is invalid"
            ) from exc


__all__ = ["MemoryApplicationService"]
