"""Bounded workbench queries over the existing Context and Learning authorities."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.context import (
    ContextRunLabel,
    PromptConstraintSection,
    PromptConstraintSource,
    PromptConstraintsProjection,
)
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

    def resolved(
        self,
        *,
        task_run_id: str | None = None,
        agent_run_id: str | None = None,
        session_id: str | None = None,
    ):
        """Use the selected task/run only; never substitute another task's last run."""
        runs = ()
        session = None
        if session_id is not None:
            session = self.journal.get_session(self.workspace_id, session_id)
            if session is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "session is missing")
        if task_run_id is not None:
            task = self.api.get_task(task_run_id)
            if task is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "task is missing")
            if session_id is not None and task.session_id != session_id:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "task is missing")
            session_id = task.session_id
            task_turn_ids = {
                turn.turn_id
                for turn in self.journal.list_task_turns(self.workspace_id, task_run_id)
            }
            runs = tuple(
                run
                for run in self.journal.list_session_agent_runs(self.workspace_id, task.session_id)
                if run.turn_id in task_turn_ids
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
                    # Preserve journal admission order when timestamps share a second.
                    key=lambda r: r.created_at,
                )
            )
        elif session_id is not None:
            runs = self.journal.list_session_agent_runs(self.workspace_id, session_id)
        run = None
        if agent_run_id is not None:
            run = self.journal.get_agent_run(self.workspace_id, agent_run_id)
            if run is None or (
                task_run_id is not None and run.agent_run_id not in {r.agent_run_id for r in runs}
            ):
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "agent run is missing")
            if session_id is not None and run.session_id != session_id and run not in runs:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "agent run is missing")
        elif runs:
            run = runs[-1]
        if run is not None and session_id is None:
            session_id = run.session_id
        if run is not None and task_run_id is None:
            turn = self.journal.get_turn(self.workspace_id, run.turn_id)
            task_run_id = turn.task_run_id if turn is not None else None
        if run is not None and not runs:
            runs = (run,)
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
                "session_id": session_id,
                "task_run_id": task_run_id,
                "agent_run_id": run.agent_run_id if run else None,
                "available_runs": [self._run_label(r) for r in runs],
                "status": "resolved" if snapshot else "not_started",
                "preferences": snapshot.frozen_preferences if snapshot else (),
                "profile": snapshot.profile if snapshot else None,
                "source_revisions": snapshot.source_revisions if snapshot else (),
                "preference_digest": snapshot.preference_projection_digest if snapshot else None,
                "omitted_count": snapshot.preference_omitted_count if snapshot else 0,
                "refresh_status": snapshot.preference_refresh_status if snapshot else "ok",
                "prompt_constraints": self._prompt_constraints(snapshot),
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

    def _session_title(self, session_id: str) -> str:
        metadata = getattr(self.journal, "session_metadata", None)
        if metadata is not None:
            try:
                value = metadata.get(self.workspace_id, session_id)
                title = value.get("title") if isinstance(value, dict) else None
                if isinstance(title, str) and title.strip():
                    return title
            except Exception:
                pass
        return "当前会话"

    def _run_label(self, run) -> ContextRunLabel:
        task_title = self._session_title(run.session_id)
        node_id = None
        node_title = None
        reference = getattr(run, "workflow_ref", None)
        if reference is not None:
            node_id = str(reference.node_id)
            try:
                revision = self.journal.workflows.get_revision(
                    self.workspace_id, reference.workflow_revision_id
                )
                node = next(
                    (item for item in revision.nodes if item.node_id == reference.node_id), None
                )
                if node is not None:
                    node_title = str(node.task_contract.objective)
            except Exception:
                node_title = None
        label = f"{task_title} · {node_title or '对话运行'} · {run.created_at:%Y-%m-%d %H:%M}"
        return ContextRunLabel(
            agent_run_id=run.agent_run_id,
            label=label,
            task_title=task_title,
            node_id=node_id,
            node_title=node_title,
            created_at=run.created_at,
        )

    @staticmethod
    def _prompt_constraints(snapshot):
        if snapshot is None:
            return PromptConstraintsProjection(
                availability="missing", message="本轮尚未生成执行上下文"
            )
        profile_available = snapshot.prompt_profile_id is not None
        role_available = snapshot.role_prompt_digest is not None
        project_available = snapshot.project_instruction_resolver_version is not None or bool(
            snapshot.project_instruction_sources
        )
        sources = tuple(
            PromptConstraintSource(path=item.path, scope=item.scope, byte_count=item.byte_count)
            for item in snapshot.project_instruction_sources
        )
        return PromptConstraintsProjection(
            availability="available"
            if profile_available or role_available or project_available
            else "missing",
            message=None
            if profile_available or role_available or project_available
            else "此历史运行未保存此项约束明细",
            sections=(
                PromptConstraintSection(
                    kind="profile",
                    label="提示配置",
                    available=profile_available,
                    summary="已冻结提示配置引用；正文不在历史投影中。"
                    if profile_available
                    else "此历史运行未保存提示配置引用。",
                ),
                PromptConstraintSection(
                    kind="role",
                    label="角色约束",
                    available=role_available,
                    summary="已冻结角色约束摘要；正文不在历史投影中。"
                    if role_available
                    else "此历史运行未保存角色约束摘要。",
                ),
                PromptConstraintSection(
                    kind="project_instructions",
                    label="项目指令",
                    available=project_available,
                    summary="已记录项目指令来源元数据；正文需在当前项目页面查看。"
                    if project_available
                    else "此历史运行未保存项目指令来源。",
                    sources=sources,
                ),
            ),
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

    def _learning_task_scope(
        self, *, session_id: str | None = None, task_run_id: str | None = None
    ) -> tuple[str, ...] | None:
        """Resolve the durable task set before any learning page is sliced."""

        if session_id is None and task_run_id is None:
            return None
        if (
            session_id is not None
            and self.journal.get_session(self.workspace_id, session_id) is None
        ):
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "session is missing")
        if task_run_id is not None:
            task = self.api.get_task(task_run_id)
            if task is None or (session_id is not None and task.session_id != session_id):
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "task is missing")
            tasks = (task,)
        else:
            tasks = self.journal.list_task_runs(self.workspace_id, session_id)
        task_ids = {task.task_run_id for task in tasks}
        for workflow in self.journal.workflows.list_runs(self.workspace_id):
            if workflow.root_task_run_id not in task_ids:
                continue
            for node in self.journal.workflows.list_nodes(
                self.workspace_id, workflow.workflow_run_id
            ):
                if node.leaf_task_run_id is not None:
                    task_ids.add(node.leaf_task_run_id)
        return tuple(sorted(task_ids))

    def _learning_source(self, review_id: str):
        review = self.journal.get_learning_review(self.workspace_id, review_id)
        if review is None:
            return {"kind": "unknown", "known": False, "review_id": review_id}
        task = self.api.get_task(review.task_run_id)
        if task is None:
            return {
                "kind": "unknown",
                "known": False,
                "review_id": review.review_id,
                "task_run_id": review.task_run_id,
            }
        return {
            "kind": "task",
            "known": True,
            "review_id": review.review_id,
            "task_run_id": task.task_run_id,
            "session_id": task.session_id,
        }

    def _preference_source(self, job_id: str):
        job = self.journal.get_preference_review_job(self.workspace_id, job_id)
        if job is None:
            return {"kind": "unknown", "known": False}
        turn = self.journal.get_turn(self.workspace_id, job.turn_id)
        task_run_id = turn.task_run_id if turn is not None else None
        task = self.api.get_task(task_run_id) if task_run_id is not None else None
        if task is None:
            return {
                "kind": "unknown",
                "known": False,
                "session_id": job.session_id,
                "task_run_id": task_run_id,
            }
        return {
            "kind": "task",
            "known": True,
            "session_id": task.session_id,
            "task_run_id": task.task_run_id,
        }

    def learning(
        self,
        page=0,
        *,
        session_id: str | None = None,
        task_run_id: str | None = None,
    ):
        # Management GETs never trigger Inbox lazy expiry writes.
        cursor = str(page * 50)
        task_run_ids = self._learning_task_scope(session_id=session_id, task_run_id=task_run_id)
        candidates = self.api.learning.list_candidates(
            status=None,
            limit=50,
            cursor=cursor,
            expire_due=False,
            task_run_ids=task_run_ids,
        )
        proposals = self.api.list_preference_proposal_views(
            status=None,
            limit=50,
            cursor=cursor,
            session_id=None,
            task_run_ids=task_run_ids,
        )
        views = []
        for candidate in candidates.items:
            view = self.api.learning.get_candidate(candidate.candidate_id, expire_due=False)
            if view is not None:
                value = management_wire(view)
                value["expired"] = view.candidate.expires_at <= self.api.clock()
                value["source"] = self._learning_source(view.candidate.origin_review_id)
                views.append(value)
        proposal_values = []
        for proposal in proposals.items:
            value = management_wire(proposal)
            value["source"] = self._preference_source(proposal.job_id)
            proposal_values.append(value)
        preference_journal = getattr(self.journal, "preference_journal", None)
        proposal_count = (
            preference_journal.count_preference_proposals(
                self.workspace_id,
                status=None,
                session_id=None,
                task_run_ids=task_run_ids,
            )
            if preference_journal is not None
            else len(proposals.items)
        )
        candidate_count = self.journal.count_learning_candidates(
            self.workspace_id, task_run_ids=task_run_ids
        )
        missing_source_count = getattr(
            self.journal, "count_learning_candidates_with_missing_source", lambda _workspace: 0
        )(self.workspace_id)
        missing_source_count += (
            getattr(
                preference_journal,
                "count_preference_proposals_with_missing_source",
                lambda _workspace: 0,
            )(self.workspace_id)
            if preference_journal is not None
            else 0
        )
        return management_wire(
            {
                "candidates": views,
                "proposals": proposal_values,
                "next_cursor": candidates.next_cursor or proposals.next_cursor,
                "limit": 50,
                "candidate_count": candidate_count,
                "proposal_count": proposal_count,
                "total_count": candidate_count + proposal_count,
                "unknown_source_count": missing_source_count,
                "source_scope": "task"
                if task_run_id is not None
                else "session"
                if session_id
                else "workspace",
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
