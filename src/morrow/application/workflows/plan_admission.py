"""Task-plan start: compile without publishing, then admit onto the unique Scheduler."""

from dataclasses import dataclass
from datetime import timedelta

from morrow.application.tasks import TaskService
from morrow.application.workflows.artifacts import ensure_workflow_payload
from morrow.application.workflows.compiler import WorkflowCompilationError
from morrow.application.workflows.queries import node_execution_wire, project_node_execution
from morrow.application.workflows.session_ownership import SessionWorkflowOwnership
from morrow.application.workflows.start import WorkflowStartResult
from morrow.core.application import (
    ApplicationCommandReceipt,
    ApplicationError,
    ApplicationErrorCode,
)
from morrow.core.domain import (
    TaskRunPurpose,
    TaskRunStatus,
    canonical_json_bytes,
    session_can_start_work,
    sha256_digest,
)
from morrow.core.execution import StaleRowVersionError
from morrow.core.models import ModelRef
from morrow.core.workflows.contracts import (
    ArtifactBinding,
    ContractRef,
    TaskContract,
    workflow_input_artifact_id,
)
from morrow.core.workflows.definitions import WorkflowRevision, compiled_content_hash
from morrow.core.workflows.drafts import WorkflowDraftStatus
from morrow.core.workflows.planning import (
    FrozenNodeSelection,
    PlanDecision,
    StartWorkflowPlanRequest,
    TaskPlanProvenance,
    change_candidate_digest,
    task_plan_execution_digest,
    visible_version_digest,
)
from morrow.core.workflows.runs import NodeRun, WorkflowRun

START_PLAN_OPERATION = "start_workflow_plan"

# The single server-side control contract (D01). Every consumer — the typed
# control service and the GUI composer — reads this projection instead of
# re-deriving its own state machine, so a historical terminal run can never
# shadow an executable repair draft.
CONTROL_INTENTS_BY_STATE = {
    "running": ("progress_question", "pause_run", "revise", "cancel_run", "steer"),
    # Repeating pause on an already paused/draining run is an idempotent
    # acknowledgement of the durable fact, never an error.
    "draining": (
        "progress_question",
        "pause_run",
        "resume_run",
        "revise",
        "cancel_run",
        "steer",
    ),
    "paused": ("progress_question", "pause_run", "resume_run", "revise", "cancel_run"),
    "repair_ready": ("start", "revise", "clarify"),
    "repair_draft": ("revise", "clarify"),
    "terminal": ("repair", "none"),
    "generating": ("progress_question",),
    # A terminal generation left no draft: nothing is in flight, so no
    # generation-scoped intent applies; regeneration rides a fresh task send.
    "generate_failed": ("ordinary_send",),
    "draft": ("start", "revise", "clarify"),
    "none": ("ordinary_send",),
}

CONTROL_STATE_HINTS = {
    "running": "运行中：可暂停、修改后续计划、取消或纠偏。",
    "draining": "正在暂停：后续节点不再准入，进行中的步骤收尾中；可继续恢复。",
    "paused": "已暂停：可继续原运行，或修改尚未开始的后续计划。",
    "repair_ready": "修复计划已就绪：明确开始后创建新的运行；历史取消运行仅供查看。",
    "repair_draft": "修复草稿尚未通过校验：请先修改或重新生成。",
    "terminal": "原运行已结束，无法原地恢复；可生成修复计划作为新的运行。",
    "generating": "计划生成中：可查看进度或取消生成。",
    "generate_failed": "上次计划生成未完成：重新发送任务描述即可重新生成。",
    "draft": "计划已就绪但尚未开始：明确开始后创建运行。",
    "none": "没有待恢复的运行；普通对话请直接发送消息。",
}


@dataclass(frozen=True)
class AdmissionPreview:
    allowed: bool
    digest: str | None
    draft_id: str | None
    draft_version: int | None
    frozen_selections: tuple[FrozenNodeSelection, ...]
    blockers: tuple[str, ...]
    source_hash: str | None
    settings_digest: str | None
    context_digest: str | None
    candidate: object | None = None
    contract: TaskContract | None = None
    binding: object | None = None
    version: object | None = None


class TaskPlanAdmissionService:
    def __init__(self, context):
        self.context = context
        self.journal = context.journal
        self.workspace_id = context.workspace_id
        self.ownership = SessionWorkflowOwnership(context.journal, context.workspace_id)

    @property
    def planning(self):
        return self.context.chat.planning

    def preview(self, session_id, *, draft_id=None, draft_version=None) -> AdmissionPreview:
        self.planning._session(session_id)
        binding = self.planning.repo.binding(self.workspace_id, session_id)
        empty = AdmissionPreview(False, None, None, None, (), ("plan_missing",), None, None, None)
        if binding is None or binding.status != "active" or not binding.current_draft_id:
            return empty
        draft = self.planning.drafts.get(binding.current_draft_id).draft
        version = self.planning.repo.version(self.workspace_id, draft.draft_id, draft.row_version)
        if version is None:
            return empty
        if draft_id is not None and (
            draft.draft_id != draft_id or draft.row_version != draft_version
        ):
            raise ApplicationError(ApplicationErrorCode.STALE, "Plan version changed")
        if draft.status is not WorkflowDraftStatus.VALID:
            return AdmissionPreview(
                False,
                None,
                draft.draft_id,
                draft.row_version,
                (),
                ("invalid_draft",),
                draft.source_hash,
                None,
                None,
                binding=binding,
                version=version,
            )
        try:
            settings, settings_digest = self.planning._settings(session_id)
            live = binding.context_ref.model_copy(
                update={
                    "model": settings.model,
                    "generation": settings.generation,
                    "settings_digest": settings_digest,
                }
            )
            selections = self.planning._selections(
                version.source, live, version.node_metadata, session_id
            )
            frozen = self._frozen(version.source, selections)
            publication = self.planning.drafts.management.workflow_publication
            result = publication.validate(version.source, active_model=frozen[0].resolved_model)
            if result.candidate is None:
                raise WorkflowCompilationError(result.diagnostics)
            nodes = []
            by_id = {item.node_id: item for item in frozen}
            for node in result.candidate.nodes:
                item = by_id[node.node_id]
                nodes.append(node.model_copy(update={"resolved_model_ref": item.resolved_model}))
            candidate = result.candidate.model_copy(update={"nodes": tuple(nodes)})
        except (ApplicationError, WorkflowCompilationError, ValueError, StaleRowVersionError):
            return AdmissionPreview(
                False,
                None,
                draft.draft_id,
                draft.row_version,
                (),
                ("execution_selection_incompatible",),
                draft.source_hash,
                None,
                None,
                binding=binding,
                version=version,
            )
        context_digest = sha256_digest(canonical_json_bytes(live.model_dump(mode="json")))
        digest = task_plan_execution_digest(
            source_hash=version.source_hash,
            frozen_selections=frozen,
            settings_digest=settings_digest,
            context_digest=context_digest,
        )
        return AdmissionPreview(
            True,
            digest,
            draft.draft_id,
            draft.row_version,
            frozen,
            (),
            version.source_hash,
            settings_digest,
            context_digest,
            candidate=candidate,
            contract=TaskContract(
                objective=version.source.name, constraints=binding.context_ref.constraints
            ),
            binding=binding,
            version=version,
        )

    def start(self, request: StartWorkflowPlanRequest) -> WorkflowStartResult:
        digest = sha256_digest(canonical_json_bytes(request.model_dump(mode="json")))
        replay = self._replay(self.journal, request.command_id, digest)
        if replay is not None:
            self._ensure_driver(replay.run.workflow_run_id)
            return replay
        if self._generation_in_flight(request.session_id):
            raise ApplicationError(ApplicationErrorCode.BUSY, "Plan generation is in progress")
        prepared = self.preview(
            request.session_id, draft_id=request.draft_id, draft_version=request.draft_version
        )
        if not prepared.allowed:
            code = (
                ApplicationErrorCode.STALE
                if "execution_selection_incompatible" in prepared.blockers
                else ApplicationErrorCode.INVALID
            )
            raise ApplicationError(code, prepared.blockers[0] if prepared.blockers else "invalid")
        if request.execution_digest != prepared.digest:
            raise ApplicationError(ApplicationErrorCode.STALE, "execution digest changed")
        if request.action_source not in {"button", "chat_command"} or not request.interaction_id:
            raise ApplicationError(ApplicationErrorCode.INVALID, "Start requires a user action")
        if prepared.binding is not None and getattr(prepared.binding, "mode", None) == "repair":
            root = (
                TaskService(
                    journal=self.journal,
                    workspace_id=self.workspace_id,
                    id_source=self.context.application.id_source,
                )
                .new_task(request.session_id, command_id=request.command_id + "_root")
                .task
            )
        else:
            root = self._select_root(request)
        artifact_id = workflow_input_artifact_id(request.command_id)
        ensure_workflow_payload(
            self.context.api.artifacts,
            prepared.contract,
            session_id=request.session_id,
            task_run_id=root.task_run_id,
            artifact_id=artifact_id,
        )
        revision_id = self.context.application.id_source.new_id("wrev")
        decision_id = self.context.application.id_source.new_id("wdec")
        run_id = self.context.application.id_source.new_id("wrun")

        def work(txn):
            replayed = self._replay(txn, request.command_id, digest)
            if replayed is not None:
                return replayed
            if self._generation_in_flight(request.session_id):
                raise ApplicationError(ApplicationErrorCode.BUSY, "Plan generation is in progress")
            current = self.preview(
                request.session_id, draft_id=request.draft_id, draft_version=request.draft_version
            )
            if (
                not current.allowed
                or current.digest != request.execution_digest
                or current.draft_id != request.draft_id
                or current.draft_version != request.draft_version
            ):
                raise ApplicationError(ApplicationErrorCode.STALE, "execution digest changed")
            session = txn.get_session(self.workspace_id, request.session_id)
            bound = txn.get_task_run(self.workspace_id, root.task_run_id)
            if (
                session is None
                or not session_can_start_work(session.lifecycle, session.health)
                or bound is None
                or bound.session_id != request.session_id
                or bound.purpose is not TaskRunPurpose.USER
                or bound.status is not TaskRunStatus.OPEN
                or bound.row_version != root.row_version
                or self._root_used(txn, bound.task_run_id)
                or txn.workflows.active_for_root(self.workspace_id, bound.task_run_id)
                or txn.has_open_turn_submission(self.workspace_id, request.session_id)
                or txn.has_nonterminal_agent_run(self.workspace_id, request.session_id)
            ):
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "Session or root task cannot start this plan"
                )
            started_at = txn.now()
            revision = WorkflowRevision(
                **current.candidate.model_dump(),
                workflow_revision_id=revision_id,
                workspace_id=self.workspace_id,
                revision=-1,
                content_hash=compiled_content_hash(current.candidate),
                source_revision=0,
                source_hash=current.source_hash,
                created_by=request.command_id,
                created_at=started_at,
            )
            decision = PlanDecision(
                plan_decision_id=decision_id,
                workspace_id=self.workspace_id,
                session_id=request.session_id,
                command_id=request.command_id,
                decision="start",
                action_source=request.action_source,
                interaction_id=request.interaction_id,
                draft_id=request.draft_id,
                draft_version=request.draft_version,
                visible_version_binding=visible_version_digest(
                    request.draft_id, request.draft_version
                ),
                execution_digest=request.execution_digest,
                result_id=run_id,
                created_at=started_at,
            )
            repairing = getattr(current.binding, "mode", None) == "repair"
            provenance = TaskPlanProvenance(
                workspace_id=self.workspace_id,
                workflow_revision_id=revision_id,
                origin="repair" if repairing else "initial",
                planning_binding_id=current.binding.planning_binding_id,
                draft_id=request.draft_id,
                draft_version=request.draft_version,
                plan_decision_id=decision_id,
                root_task_run_id=bound.task_run_id,
                context_digest=current.context_digest,
                settings_digest=current.settings_digest,
                frozen_selections=current.frozen_selections,
                repair_of_run_id=current.binding.parent_run_id if repairing else None,
                repair_of_revision_id=current.binding.parent_revision_id if repairing else None,
                created_at=started_at,
                command_id=request.command_id,
            )
            txn.workflows.planning.save_decision(decision)
            txn.workflows.store_detached_revision(revision, provenance=provenance)
            if repairing and current.binding.status == "active":
                # The draft is consumed by this start: a closed binding can
                # never advertise the same plan as startable a second time.
                txn.workflows.planning.save_binding(
                    current.binding.model_copy(
                        update={
                            "status": "closed",
                            "row_version": current.binding.row_version + 1,
                            "updated_at": started_at,
                        }
                    ),
                    expected=current.binding.row_version,
                )
            run = WorkflowRun(
                workflow_run_id=run_id,
                workspace_id=self.workspace_id,
                workflow_revision_id=revision_id,
                root_task_run_id=bound.task_run_id,
                budget_snapshot=revision.budget,
                started_at=started_at,
                admission_deadline_at=(
                    started_at + timedelta(seconds=revision.budget.admission_timeout_seconds)
                    if revision.budget.admission_timeout_seconds is not None
                    else None
                ),
                input_artifacts=(
                    ArtifactBinding(
                        name="task",
                        artifact_id=artifact_id,
                        contract=ContractRef(kind="TaskContract"),
                    ),
                ),
                lineage_budget_root_run_id=run_id,
            )
            nodes = tuple(
                NodeRun(
                    node_run_id=self.context.application.id_source.new_id("nrun"),
                    workspace_id=self.workspace_id,
                    workflow_run_id=run.workflow_run_id,
                    node_id=node.node_id,
                )
                for node in revision.nodes
            )
            txn.workflows.create_run(run, nodes)
            receipt = txn.put_application_command_receipt_in_txn(
                self.workspace_id,
                ApplicationCommandReceipt(
                    command_id=request.command_id,
                    workspace_id=self.workspace_id,
                    session_id=request.session_id,
                    operation=START_PLAN_OPERATION,
                    request_digest=digest,
                    result_kind="workflow_run",
                    result_id=run.workflow_run_id,
                    created_at=started_at,
                ),
            )
            txn.workflows.planning.event(
                self.workspace_id,
                request.session_id,
                {
                    "decision": "start",
                    "run_id": run.workflow_run_id,
                    "draft_version": request.draft_version,
                    "status": run.status.value,
                },
            )
            return WorkflowStartResult(run, receipt, False)

        started = self.journal.transact(work)
        self._ensure_driver(started.run.workflow_run_id)
        return started

    def _generation_in_flight(self, session_id) -> bool:
        return any(
            operation.status in {"running", "queued"}
            for operation in self.planning.repo.operations(self.workspace_id, session_id)
        )

    def view(self, session_id):
        ownership = self.ownership.resolve(session_id)
        if ownership["role"] == "node":
            return self._node_execution_view(session_id, ownership)
        base = self.planning.view(session_id)
        preview = self.preview(session_id)
        run = self._current_run(session_id)
        actions = list(base["allowed_actions"])
        binding = base.get("binding")
        if (
            preview.allowed
            and not self._generation_in_flight(session_id)
            and (
                run is None or (binding is not None and getattr(binding, "mode", None) == "repair")
            )
        ):
            actions.append("start")
        if run is not None and (
            run.status.value in {"failed", "cancelled"} or run.result_status == "needs_revision"
        ):
            actions.append("repair")
        if run is not None and not run.status.terminal:
            if not run.pause_requested:
                actions.append("pause")
            else:
                actions.append("resume")
            actions.append("change")
            if run.status.value in {"queued", "running", "blocked", "draining"}:
                actions.append("cancel")
            if (
                base.get("binding") is not None
                and getattr(base["binding"], "mode", None) == "change"
            ):
                actions.extend(("save_candidate", "accept_change", "reject_change"))
        projection = None
        if run is not None:
            projection = self.run_projection(run)
        candidate = self.candidate_projection(base.get("binding"), run)
        allowed_actions = tuple(dict.fromkeys(actions))
        return {
            **base,
            "execution": {
                "allowed": preview.allowed,
                "digest": preview.digest,
                "draft_id": preview.draft_id,
                "draft_version": preview.draft_version,
                "blockers": list(preview.blockers),
                "frozen_selections": [
                    item.model_dump(mode="json") for item in preview.frozen_selections
                ],
            },
            "run": projection,
            "candidate": candidate,
            "allowed_actions": allowed_actions,
            "control": self.control_projection(
                session_id,
                run=run,
                binding=binding,
                preview=preview,
                draft=base.get("draft"),
                allowed_actions=allowed_actions,
            ),
            "ownership": ownership,
            "plan": None,
        }

    def control_projection(
        self, session_id, *, run, binding, preview, draft, allowed_actions=()
    ) -> dict:
        """One state/allowed-intent projection for every control surface (D01)."""

        status = run.status.value if run is not None else None
        mode = getattr(binding, "mode", None)
        repair_ready = bool(preview.allowed and mode == "repair" and preview.draft_id)
        if run is not None and status not in {"completed", "failed", "cancelled", "superseded"}:
            if status == "paused":
                state = "paused"
            elif run.pause_requested or status == "draining":
                state = "draining"
            else:
                state = "running"
        elif run is not None:
            if repair_ready:
                state = "repair_ready"
            elif (
                mode == "repair"
                and binding is not None
                and binding.status == "active"
                and binding.current_draft_id
            ):
                state = "repair_draft"
            else:
                state = "terminal"
        elif binding is None or binding.status != "active":
            state = "none"
        elif binding.current_draft_id is None:
            # "generating" must mean a generation is actually open (queued or
            # running): a terminal operation without a draft never produced
            # one, and projecting it as in-flight blocks retry forever (D10).
            operations = self.planning.repo.operations(self.workspace_id, session_id)
            live = any(op.status in {"queued", "running"} for op in operations)
            if live:
                state = "generating"
            else:
                latest = (
                    max(operations, key=lambda op: (op.created_at, op.planning_operation_id))
                    if operations
                    else None
                )
                state = (
                    "generate_failed"
                    if latest is not None and latest.status in {"failed", "cancelled", "expired"}
                    else "generating"
                )
        else:
            state = "draft"
        return {
            "state": state,
            "allowed_intents": CONTROL_INTENTS_BY_STATE[state],
            "hint": CONTROL_STATE_HINTS[state],
            "resume_available": state in {"paused", "draining"},
            "cancel_available": state in {"running", "draining", "paused"},
            "start_available": state in {"draft", "repair_ready"} and bool(preview.allowed),
            "repair_available": state == "terminal",
            "target": {
                "workflow_run_id": run.workflow_run_id if run is not None else None,
                "run_status": status,
                "run_row_version": run.row_version if run is not None else None,
                "pause_requested": run.pause_requested if run is not None else False,
                "planning_operation_id": self._live_operation_id(session_id),
                "binding_id": binding.planning_binding_id if binding is not None else None,
                "binding_mode": mode,
                "draft_id": preview.draft_id,
                "draft_version": preview.draft_version,
                "execution_digest": preview.digest,
            },
            "allowed_actions": tuple(allowed_actions),
        }

    def _live_operation_id(self, session_id) -> str | None:
        for operation in self.planning.repo.operations(self.workspace_id, session_id):
            if operation.status in {"queued", "running"}:
                return operation.planning_operation_id
        return None

    def _node_execution_view(self, session_id, ownership):
        """Read-only execution view for an isolated node conversation.

        The panel shows the owning run's DAG and the frozen plan version the
        run was admitted with — never the root session's current draft, which
        may have been regenerated after this node executed. Control actions
        stay bound to the root run identity (run-level endpoints), so this
        view exposes none.
        """
        run = (
            self.journal.workflows.get_run(self.workspace_id, ownership["workflow_run_id"])
            if ownership["workflow_run_id"] is not None
            else None
        )
        plan = dict(ownership["plan"]) if ownership["plan"] else None
        if plan is not None:
            plan["source"] = None
            plan["node_metadata"] = {}
            if plan.get("draft_id") is not None and plan.get("draft_version") is not None:
                version = self.planning.repo.version(
                    self.workspace_id, plan["draft_id"], plan["draft_version"]
                )
                if version is None:
                    ownership["issues"].append("frozen_draft_missing")
                else:
                    plan["source"] = version.source
                    plan["node_metadata"] = version.node_metadata
        return {
            "binding": None,
            "draft": None,
            "version": None,
            "operations": (),
            "execution": {
                "allowed": False,
                "digest": None,
                "draft_id": None,
                "draft_version": None,
                "blockers": ["execution_node_session"],
                "frozen_selections": [],
            },
            "run": self.run_projection(run) if run is not None else None,
            "candidate": None,
            "allowed_actions": (),
            "control": {
                "state": "none",
                "allowed_intents": (),
                "hint": "工作流执行详情：控制操作请在主对话中完成。",
                "resume_available": False,
                "cancel_available": False,
                "start_available": False,
                "repair_available": False,
                "target": {
                    "workflow_run_id": ownership["workflow_run_id"],
                    "run_status": run.status.value if run is not None else None,
                    "run_row_version": run.row_version if run is not None else None,
                    "pause_requested": run.pause_requested if run is not None else False,
                    "planning_operation_id": None,
                    "binding_id": None,
                    "binding_mode": None,
                    "draft_id": None,
                    "draft_version": None,
                    "execution_digest": None,
                },
                "allowed_actions": (),
            },
            "ownership": ownership,
            "plan": plan,
        }

    def run_projection(self, run):
        nodes = self.journal.workflows.list_nodes(self.workspace_id, run.workflow_run_id)
        pause_point = self.journal.workflows.execution_pause.latest_pause_point(
            self.workspace_id, owner="workflow_run", owner_id=run.workflow_run_id
        )
        execution = {
            item.node_id
            for item in self.journal.workflows.list_execution_nodes(
                self.workspace_id, run.workflow_run_id
            )
        }
        inherited = tuple(
            node.node_id for node in nodes if execution and node.node_id not in execution
        )
        origin = self.journal.workflows.get_task_plan_provenance(
            self.workspace_id, run.workflow_revision_id
        )
        outcomes = self.journal.list_task_outcomes(self.workspace_id, run.root_task_run_id)
        latest = outcomes[-1] if outcomes else None
        return {
            "workflow_run_id": run.workflow_run_id,
            "status": run.status.value,
            "result_status": run.result_status,
            "pause_requested": run.pause_requested,
            "row_version": run.row_version,
            "workflow_revision_id": run.workflow_revision_id,
            "lineage_root_run_id": run.effective_lineage_budget_root_run_id,
            "origin": origin.origin if origin else None,
            "inherited_node_ids": inherited,
            "active_node_ids": tuple(
                n.node_id for n in nodes if n.status.value in {"running", "blocked"}
            ),
            "nodes": tuple(
                {
                    "node_id": n.node_id,
                    "status": n.status.value,
                    "inherited": n.node_id in inherited,
                    "execution": node_execution_wire(
                        project_node_execution(
                            run=run,
                            node=n,
                            segments=(
                                self.journal.workflows.segments_for_node(
                                    self.workspace_id, n.node_run_id
                                )
                                if n.node_run_id
                                else ()
                            ),
                            pause_point=pause_point,
                        )
                    ),
                }
                for n in nodes
            ),
            "agent_generation_request_count": self.journal.count_workflow_agent_requests(
                self.workspace_id, run.workflow_run_id
            ),
            "lineage_agent_generation_request_count": self.journal.count_lineage_agent_requests(
                self.workspace_id, run.effective_lineage_budget_root_run_id
            ),
            "outcome": {
                "summary": latest.summary,
                "task_status": latest.task_status.value,
                "completion_basis": latest.completion_basis,
            }
            if latest
            else None,
        }

    def candidate_projection(self, binding, run):
        if (
            binding is None
            or run is None
            or getattr(binding, "mode", None) not in {"change", "repair"}
            or not binding.parent_run_id
        ):
            return None
        draft = (
            self.planning.drafts.get(binding.current_draft_id) if binding.current_draft_id else None
        )
        past = self._past_node_ids(run) if getattr(binding, "mode", None) == "change" else ()
        source_hash = draft.draft.source_hash if draft else None
        digest = (
            change_candidate_digest(
                source_hash=source_hash,
                past_node_ids=past,
                parent_revision_id=run.workflow_revision_id,
                parent_row_version=run.row_version,
            )
            if source_hash
            else None
        )
        revision = self.journal.workflows.get_revision(self.workspace_id, run.workflow_revision_id)
        parent_ids = {node.node_id for node in revision.nodes} if revision is not None else set()
        draft_ids = (
            {node.node_id for node in draft.draft.source.nodes} if draft is not None else set()
        )
        changed = []
        if draft is not None and revision is not None:
            prior = {node.node_id: node for node in revision.nodes}
            for node in draft.draft.source.nodes:
                base = prior.get(node.node_id)
                if (
                    base is not None
                    and node.node_id not in past
                    and base.task_contract.objective != node.task_contract.objective
                ):
                    changed.append(node.node_id)
        events = self.planning.repo.events(self.workspace_id, binding.session_id)
        replan = next((item for item in reversed(events) if item.get("replan")), None)
        reason = None
        if replan is not None:
            if replan.get("review_blocking"):
                reason = "审查发现阻断问题，建议增加修复步骤和新的审查"
            elif replan.get("conflicting_signals"):
                reason = "多个调整信号存在矛盾，请核对待生效的差异"
            else:
                reason = "节点发现新证据，建议调整尚未开始的工作"
        return {
            "parent_run_id": binding.parent_run_id,
            "base_revision_id": binding.parent_revision_id,
            "expected_parent_row_version": run.row_version,
            "past_node_ids": past,
            "added_node_ids": tuple(sorted(draft_ids - parent_ids)),
            "removed_node_ids": tuple(sorted(parent_ids - draft_ids)),
            "changed_node_ids": tuple(changed),
            "origin": "replan" if replan is not None else "user",
            "reason": reason,
            "review_blocking": bool(replan and replan.get("review_blocking")),
            "source_hash": source_hash,
            "settings_digest": binding.context_ref.settings_digest,
            "stable": run.status.value == "paused" and run.pause_requested,
            "digest": digest,
        }

    def _past_node_ids(self, run):
        runtime = getattr(self.context, "runtime", None)
        if runtime is None or getattr(runtime, "patches", None) is None:
            return ()
        return tuple(sorted(runtime.patches._past_node_ids(run)))

    def _frozen(self, source, selections):
        items = []
        for node in source.nodes:
            row = selections[node.node_id]
            items.append(
                FrozenNodeSelection(
                    node_id=node.node_id,
                    version_id=node.agent_definition_ref.version_id,
                    content_hash=node.agent_definition_ref.content_hash,
                    resolved_model=ModelRef.model_validate(row["model"]),
                    resolved_generation=row["generation"],
                    model_source=row["model_source"],
                    generation_source=row["generation_source"],
                )
            )
        return tuple(items)

    def _replay(self, reader, command_id, digest):
        receipt = reader.get_application_command_receipt(self.workspace_id, command_id)
        if receipt is None:
            return None
        if receipt.operation != START_PLAN_OPERATION or receipt.request_digest != digest:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "start command ID was reused with a different request",
            )
        run = reader.workflows.get_run(self.workspace_id, receipt.result_id or "")
        if run is None:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "task-plan start result is missing"
            )
        return WorkflowStartResult(run, receipt, True)

    def _select_root(self, request):
        session = self.journal.get_session(self.workspace_id, request.session_id)
        if session is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Session is missing")
        if request.root_task_run_id:
            root = self.journal.get_task_run(self.workspace_id, request.root_task_run_id)
            if (
                root is None
                or root.session_id != request.session_id
                or root.purpose is not TaskRunPurpose.USER
                or root.status is not TaskRunStatus.OPEN
                or self._root_used(self.journal, root.task_run_id)
            ):
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "root TaskRun is not an empty user task"
                )
            if (
                request.expected_root_row_version is not None
                and root.row_version != request.expected_root_row_version
            ):
                raise ApplicationError(
                    ApplicationErrorCode.STALE, "root TaskRun row version is stale"
                )
            return root
        current = (
            self.journal.get_task_run(self.workspace_id, session.current_task_run_id)
            if session.current_task_run_id
            else None
        )
        if (
            current is not None
            and current.purpose is TaskRunPurpose.USER
            and current.status is TaskRunStatus.OPEN
            and not self._root_used(self.journal, current.task_run_id)
        ):
            return current
        return (
            TaskService(
                journal=self.journal,
                workspace_id=self.workspace_id,
                id_source=self.context.application.id_source,
            )
            .new_task(request.session_id, command_id=request.command_id + "_root")
            .task
        )

    def _root_used(self, reader, task_run_id):
        return (
            reader._backend.read_one(
                "SELECT 1 FROM workflow_runs WHERE workspace_id=? AND root_task_run_id=?",
                (self.workspace_id, task_run_id),
            )
            is not None
        )

    def _current_run(self, session_id):
        for decision in self.planning.repo.decisions(self.workspace_id, session_id):
            if decision.decision in {"start", "accept_change"} and decision.result_id:
                run = self.journal.workflows.get_run(self.workspace_id, decision.result_id)
                if run is not None:
                    return run
        # Direct runs (explicit_workflow) never had a planning decision; they
        # resolve from the Session root instead. Exactly one active run is
        # unambiguous — zero or several stay unresolved rather than guessing
        # or fabricating a planning binding (BUG-GUI-001).
        active = self.journal.workflows.active_runs_for_root_session(self.workspace_id, session_id)
        if len(active) == 1:
            return active[0]
        return None

    def _ensure_driver(self, workflow_run_id):
        runtime = getattr(self.context, "runtime", None)
        supervisor = getattr(self.context, "supervisor", None)
        if runtime is None or supervisor is None:
            raise ApplicationError(ApplicationErrorCode.UNAVAILABLE, "Workflow runtime is missing")
        supervisor.ensure_driver(
            workflow_run_id,
            lambda: runtime.scheduler.run(workflow_run_id, cancelled_is_user=False),
        )
