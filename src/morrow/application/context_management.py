"""Bounded workbench queries over the existing Context and Learning authorities."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import redact_workflow_text
from morrow.core.learning import LearningCandidateStatus
from morrow.core.preference_documents import PreferenceDocument
from morrow.core.preference_persistence_models import PreferenceProposalStatus


def management_wire(value):
    """Serialize only explicitly selected domain projections, redacting display text."""
    if hasattr(value, "model_dump"):
        return management_wire(value.model_dump(mode="json"))
    if is_dataclass(value):
        return management_wire(asdict(value))
    if isinstance(value, dict):
        return {str(key): management_wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [management_wire(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, str):
        return redact_workflow_text(value)[0]
    return value


class ContextManagementQueries:
    def __init__(self, api, project_store) -> None:
        self.api = api
        self.journal = api.journal
        self.workspace_id = api.workspace_id
        self.project_store = project_store

    def resolved(self, *, task_run_id: str | None = None, agent_run_id: str | None = None):
        """Use the selected task/run only; never substitute another task's last run."""
        runs = ()
        if task_run_id is not None:
            task = self.api.get_task(task_run_id)
            if task is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "task is missing")
            runs = tuple(
                run
                for run in self.journal.list_session_agent_runs(self.workspace_id, task.session_id)
                if run.task_run_id == task_run_id
            )
            workflow_runs = self.journal.workflows.list_runs(self.workspace_id)
            extra = []
            for workflow in workflow_runs:
                if workflow.root_task_run_id != task_run_id:
                    continue
                for node in self.journal.workflows.list_nodes(
                    self.workspace_id, workflow.workflow_run_id
                ):
                    if node.agent_run_id:
                        leaf = self.journal.get_agent_run(self.workspace_id, node.agent_run_id)
                        if leaf is not None:
                            extra.append(leaf)
            runs = tuple(
                sorted(
                    {r.agent_run_id: r for r in (*runs, *extra)}.values(),
                    key=lambda r: (r.created_at, r.agent_run_id),
                )
            )
        run = None
        if agent_run_id is not None:
            run = self.journal.get_agent_run(self.workspace_id, agent_run_id)
            if run is None or (
                task_run_id is not None and run.agent_run_id not in {r.agent_run_id for r in runs}
            ):
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "agent run is missing")
        elif runs:
            run = runs[-1]
        snapshot = run.snapshot if run is not None else None
        selected = []
        selection = None
        if snapshot is not None and snapshot.memory_selection_id:
            selection = self.journal.get_memory_selection(
                self.workspace_id, snapshot.memory_selection_id
            )
            if selection is None:
                raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "selection is missing")
            for item in selection.selected_items:
                # Immutable revision, even if the current head was edited/disabled/deleted.
                revision = self.journal.get_project_knowledge_revision(
                    self.workspace_id, item.record_revision_id
                )
                selected.append({"selection": item, "revision": revision})
        pending_preferences = self.journal.preference_journal.count_preference_proposals(
            self.workspace_id, status=PreferenceProposalStatus.PROPOSED
        )
        return management_wire(
            {
                "workspace_id": self.workspace_id,
                "task_run_id": task_run_id,
                "agent_run_id": run.agent_run_id if run else None,
                "available_runs": [{"agent_run_id": r.agent_run_id} for r in runs],
                "status": "resolved" if snapshot else "not_started",
                "preferences": snapshot.frozen_preferences if snapshot else (),
                "profile": snapshot.profile if snapshot else None,
                "source_revisions": snapshot.source_revisions if snapshot else (),
                "preference_digest": snapshot.preference_projection_digest if snapshot else None,
                "omitted_count": snapshot.preference_omitted_count if snapshot else 0,
                "refresh_status": snapshot.preference_refresh_status if snapshot else "ok",
                "knowledge": selected,
                "memory_selection_id": selection.selection_id if selection else None,
                "pending_learning_count": self.journal.count_learning_candidates(
                    self.workspace_id,
                    status=LearningCandidateStatus.PROPOSED,
                    expires_after=self.api.clock(),
                )
                + pending_preferences,
                "convention_count": len(snapshot.profile.conventions)
                if snapshot and snapshot.profile
                else 0,
                # Generic preferences have no authoritative language/verbosity keys.
                "language": None,
                "verbosity": None,
            }
        )

    def preferences(self, scope: str):
        document = self.api.preference_queries.document(scope)
        batches = self.journal.preference_journal.list_preference_write_batches(
            self.workspace_id, limit=500
        )
        history = []
        for batch in batches:
            if batch.scope.value != scope:
                continue
            # Writer images are typed Preference documents. Never expose a raw
            # global config image or any unknown fields from persisted JSON.
            before = (
                PreferenceDocument.model_validate_json(batch.before_document_json).entries
                if batch.before_document_json
                else ()
            )
            after = (
                PreferenceDocument.model_validate_json(batch.after_document_json).entries
                if batch.after_document_json
                else ()
            )
            history.append(
                {
                    "batch_id": batch.batch_id,
                    "status": batch.status,
                    "expected_document_revision": batch.expected_document_revision,
                    "operations": (*batch.operations, *batch.lifecycle_operations),
                    "before": before,
                    "after": after,
                    "created_at": batch.created_at,
                }
            )
        return management_wire(
            {
                "document": document.model_dump(mode="json", exclude={"updated_at"}),
                "history": history,
            }
        )

    def profile(self):
        loaded = self.project_store.load_profile(self.workspace_id)
        if loaded.status.value != "ok":
            raise ApplicationError(ApplicationErrorCode.UNAVAILABLE, "Profile is unavailable")
        return management_wire(
            {
                "profile": loaded.value.profile if loaded.value else None,
                "revision": loaded.revision or 0,
                "scope": "workspace",
            }
        )

    def learning(self, page=0):
        # Management GETs never trigger the legacy Inbox's lazy expiry writes.
        cursor = str(page * 50)
        candidates = self.api.learning.list_candidates(
            status=None, limit=50, cursor=cursor, expire_due=False
        )
        proposals = self.api.list_preference_proposal_views(status=None, limit=50, cursor=cursor)
        views = []
        for candidate in candidates.items:
            view = self.api.learning.get_candidate(candidate.candidate_id, expire_due=False)
            if view is not None:
                value = management_wire(view)
                value["expired"] = view.candidate.expires_at <= self.api.clock()
                views.append(value)
        return management_wire(
            {
                "candidates": views,
                "proposals": proposals.items,
                "next_cursor": candidates.next_cursor or proposals.next_cursor,
                "limit": 50,
            }
        )

    def knowledge(self, page=0):
        page = self.api.list_project_knowledge(
            include_deleted=True, limit=50, cursor=str(page * 50)
        )
        return management_wire(
            {
                "items": [self.api.get_project_knowledge(k.head.knowledge_id) for k in page.items],
                "next_cursor": page.next_cursor,
                "limit": 50,
            }
        )
