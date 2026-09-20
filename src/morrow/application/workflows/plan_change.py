"""Pause a live task-plan and open a change-mode planning binding.

Subsequent user edits never rewrite the running initial draft. Pause is the
durable `pause_requested` fact: admission closes immediately, Active nodes
drain, and the run is not marked paused until that set is empty.
"""

from dataclasses import dataclass

from morrow.application.api_context import request_digest
from morrow.core.application import (
    ApplicationCommandDisposition,
    ApplicationCommandReceipt,
    ApplicationError,
    ApplicationErrorCode,
)
from morrow.core.workflows.patches import FutureGraphPatch
from morrow.core.workflows.planning import (
    ApplyWorkflowChangeRequest,
    PauseWorkflowPlanRequest,
    PlanDecision,
    PlanningBinding,
    PlanningContextRef,
    PrepareWorkflowChangeRequest,
    PrepareWorkflowRepairRequest,
    ResumeWorkflowPlanRequest,
    visible_version_digest,
)
from morrow.core.workflows.runs import WorkflowRun, WorkflowStatus

PAUSE_OPERATION = "request_workflow_pause"
CHANGE_OPERATION = "prepare_workflow_change"
APPLY_OPERATION = "apply_workflow_change"
RESUME_OPERATION = "resume_workflow"
REPAIR_OPERATION = "prepare_workflow_repair"
REPAIRABLE = frozenset({"failed", "cancelled"})
LIVE_STATUSES = frozenset({"queued", "running", "blocked", "draining", "paused"})


@dataclass(frozen=True)
class PausePlanResult:
    run: WorkflowRun
    replayed: bool
    receipt: ApplicationCommandReceipt


@dataclass(frozen=True)
class PrepareChangeResult:
    run: WorkflowRun
    binding: PlanningBinding
    replayed: bool
    receipt: ApplicationCommandReceipt


@dataclass(frozen=True)
class ApplyChangeResult:
    run: WorkflowRun
    child: WorkflowRun | None
    binding: PlanningBinding | None
    decision: str
    replayed: bool
    receipt: ApplicationCommandReceipt


class PlanChangeService:
    def __init__(self, context):
        self.context = context
        self.journal = context.journal
        self.workspace_id = context.workspace_id

    @property
    def planning(self):
        return self.context.chat.planning

    @property
    def admission(self):
        return self.context.chat.admission

    def pause(self, request: PauseWorkflowPlanRequest) -> PausePlanResult:
        run = self._require_live_run(request.session_id)
        digest = request_digest(
            PAUSE_OPERATION,
            {
                "session_id": request.session_id,
                "workflow_run_id": run.workflow_run_id,
                "expected_run_row_version": request.expected_run_row_version,
            },
        )
        # Replay first: a command that already succeeded must stay idempotent even
        # when its own effect (or any other change) moved the row version on.
        replayed = self._replay(request.command_id, PAUSE_OPERATION, digest)
        if replayed is not None:
            current = self.journal.workflows.get_run(self.workspace_id, replayed.result_id or "")
            if current is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "pause result is missing"
                )
            return PausePlanResult(current, True, replayed)
        self._check_row_version(run, request.expected_run_row_version)
        paused = self._request_pause(run.workflow_run_id, command_id=request.command_id)
        self._wake_leaf_drivers(paused.workflow_run_id)
        receipt = self._put_receipt(
            request.command_id,
            PAUSE_OPERATION,
            digest,
            request.session_id,
            "workflow_run",
            paused.workflow_run_id,
        )
        self._event(
            request.session_id,
            {
                "pause": True,
                "run_id": paused.workflow_run_id,
                "status": paused.status.value,
                "pause_requested": paused.pause_requested,
            },
        )
        return PausePlanResult(paused, False, receipt)

    def prepare(self, request: PrepareWorkflowChangeRequest) -> PrepareChangeResult:
        run = self._require_live_run(request.session_id)
        digest = request_digest(
            CHANGE_OPERATION,
            {
                "session_id": request.session_id,
                "workflow_run_id": run.workflow_run_id,
                "workflow_revision_id": run.workflow_revision_id,
                "action_source": request.action_source,
                "expected_run_row_version": request.expected_run_row_version,
            },
        )
        replayed = self._replay(request.command_id, CHANGE_OPERATION, digest)
        if replayed is not None:
            binding = self.planning.repo.get_binding(self.workspace_id, replayed.result_id or "")
            current = self.journal.workflows.get_run(self.workspace_id, run.workflow_run_id)
            if binding is None or current is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "change binding result is missing"
                )
            return PrepareChangeResult(current, binding, True, replayed)
        self._check_row_version(run, request.expected_run_row_version)
        paused = self._request_pause(run.workflow_run_id)
        self._wake_leaf_drivers(paused.workflow_run_id)
        binding, previous_draft_id = self._ensure_mode_binding(request, paused, mode="change")
        binding = self._attach_candidate(binding, paused, request, previous_draft_id)
        receipt = self._put_receipt(
            request.command_id,
            CHANGE_OPERATION,
            digest,
            request.session_id,
            "planning_binding",
            binding.planning_binding_id,
        )
        self._event(
            request.session_id,
            {
                "change": True,
                "binding_id": binding.planning_binding_id,
                "parent_run_id": paused.workflow_run_id,
                "status": paused.status.value,
                "pause_requested": paused.pause_requested,
            },
        )
        return PrepareChangeResult(paused, binding, False, receipt)

    def apply(self, request: ApplyWorkflowChangeRequest) -> ApplyChangeResult:
        digest = request_digest(
            APPLY_OPERATION,
            {
                "session_id": request.session_id,
                "decision": request.decision,
                "candidate_digest": request.candidate_digest,
                "expected_parent_row_version": request.expected_parent_row_version,
            },
        )
        replayed = self._replay(request.command_id, APPLY_OPERATION, digest)
        if replayed is not None:
            return self._replay_apply(request, replayed)
        run = self._require_live_run(request.session_id)
        binding = self.planning.repo.binding(self.workspace_id, request.session_id)
        if binding is None or binding.mode != "change" or not binding.current_draft_id:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "change candidate is missing")
        snapshot = self.admission.candidate_projection(binding, run)
        if snapshot is None or snapshot["digest"] != request.candidate_digest:
            raise ApplicationError(
                ApplicationErrorCode.STALE, "change candidate changed; refresh and retry"
            )
        if run.row_version != request.expected_parent_row_version:
            raise ApplicationError(
                ApplicationErrorCode.STALE, "Workflow run version changed; refresh and retry"
            )
        if request.decision == "reject_change":
            closed = self._close_binding(binding)
            receipt = self._decide(request, run, closed, result_id=None, digest=digest)
            return ApplyChangeResult(run, None, closed, request.decision, False, receipt)
        patch = self._patch_for(binding, run, request.command_id)
        runtime = self._runtime()
        if request.decision == "save_candidate":
            runtime.patches.save(patch, active_model=binding.context_ref.model)
            receipt = self._decide(request, run, binding, result_id=None, digest=digest)
            return ApplyChangeResult(run, None, binding, request.decision, False, receipt)
        if run.status is not WorkflowStatus.PAUSED or not run.pause_requested:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "accepting a change requires a fully paused parent; wait for draining to finish",
            )
        applied = runtime.patches.apply(
            patch, active_model=binding.context_ref.model, command_id=request.command_id + "_apply"
        )
        child = applied.child
        if child is None:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "change handoff did not create a child run"
            )
        closed = self._close_binding(binding)
        receipt = self._decide(request, run, closed, result_id=child.workflow_run_id, digest=digest)
        self.admission._ensure_driver(child.workflow_run_id)
        return ApplyChangeResult(run, child, closed, request.decision, False, receipt)

    def _replay_apply(self, request, replayed):
        if request.decision == "accept_change":
            child = self.journal.workflows.get_run(self.workspace_id, replayed.result_id or "")
            parent = (
                self.journal.workflows.get_run(self.workspace_id, child.parent_run_id)
                if child is not None and child.parent_run_id
                else None
            )
            return ApplyChangeResult(parent or child, child, None, request.decision, True, replayed)
        run = self.admission._current_run(request.session_id)
        binding = self.planning.repo.binding(self.workspace_id, request.session_id)
        return ApplyChangeResult(run, None, binding, request.decision, True, replayed)

    def resume(
        self,
        request: ResumeWorkflowPlanRequest,
        *,
        continuation_input: str | None = None,
    ) -> PausePlanResult:
        run = self._require_live_run(request.session_id)
        digest = request_digest(
            RESUME_OPERATION,
            {
                "session_id": request.session_id,
                "workflow_run_id": run.workflow_run_id,
                "expected_run_row_version": request.expected_run_row_version,
            },
        )
        replayed = self._replay(request.command_id, RESUME_OPERATION, digest)
        if replayed is not None:
            current = self.journal.workflows.get_run(self.workspace_id, replayed.result_id or "")
            if current is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "resume result is missing"
                )
            return PausePlanResult(current, True, replayed)
        self._check_row_version(run, request.expected_run_row_version)
        # The accepted continuation text (correction or pure continue word) is
        # bound to the suspended pause cycle before the run leaves PAUSED, so
        # the continuation Turn admits exactly once with this input (D07).
        self._pause_service().store_continuation_input(
            run.workflow_run_id,
            command_id=request.command_id,
            text=continuation_input,
        )
        resumed = self._runtime().transitions.resume_run(run.workflow_run_id)
        receipt = self._put_receipt(
            request.command_id,
            RESUME_OPERATION,
            digest,
            request.session_id,
            "workflow_run",
            resumed.workflow_run_id,
        )
        if not resumed.status.terminal:
            self.admission._ensure_driver(resumed.workflow_run_id)
        self._event(
            request.session_id,
            {
                "resume": True,
                "run_id": resumed.workflow_run_id,
                "status": resumed.status.value,
                "pause_requested": resumed.pause_requested,
            },
        )
        return PausePlanResult(resumed, False, receipt)

    def _pause_service(self):
        from morrow.application.execution_pause import ExecutionPauseService

        return ExecutionPauseService(self.journal, workspace_id=self.workspace_id)

    def _wake_leaf_drivers(self, workflow_run_id: str) -> None:
        """Best-effort in-process wake after the durable pause fact commits."""
        try:
            self._runtime().scheduler.pause_control.wake_run(workflow_run_id)
        except Exception:
            # The durable pause fact is authoritative; a missed hint only
            # means the driver notices at its next control check.
            return

    def prepare_repair(self, request: PrepareWorkflowRepairRequest) -> PrepareChangeResult:
        run = self._require_repair_run(request.session_id)
        self._refuse_unresolved_recovery(request.session_id)
        digest = request_digest(
            REPAIR_OPERATION,
            {
                "session_id": request.session_id,
                "workflow_run_id": run.workflow_run_id,
                "workflow_revision_id": run.workflow_revision_id,
                "action_source": request.action_source,
            },
        )
        replayed = self._replay(request.command_id, REPAIR_OPERATION, digest)
        if replayed is not None:
            binding = self.planning.repo.get_binding(self.workspace_id, replayed.result_id or "")
            current = self.journal.workflows.get_run(self.workspace_id, run.workflow_run_id)
            if binding is None or current is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "repair binding result is missing"
                )
            return PrepareChangeResult(current, binding, True, replayed)
        binding, previous_draft_id = self._ensure_mode_binding(request, run, mode="repair")
        binding = self._attach_candidate(binding, run, request, previous_draft_id)
        receipt = self._put_receipt(
            request.command_id,
            REPAIR_OPERATION,
            digest,
            request.session_id,
            "planning_binding",
            binding.planning_binding_id,
        )
        self._event(
            request.session_id,
            {
                "repair": True,
                "binding_id": binding.planning_binding_id,
                "parent_run_id": run.workflow_run_id,
                "status": run.status.value,
                "result_status": run.result_status,
            },
        )
        return PrepareChangeResult(run, binding, False, receipt)

    def _require_repair_run(self, session_id) -> WorkflowRun:
        self.planning._session(session_id)
        run = self.admission._current_run(session_id)
        if run is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Workflow run is missing")
        if not run.status.terminal:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "repair requires a finished run; pause a live run to change remaining work",
            )
        if run.status.value not in REPAIRABLE and run.result_status != "needs_revision":
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "a successful run does not open a repair plan; continue in ordinary chat",
            )
        return run

    def _refuse_unresolved_recovery(self, session_id) -> None:
        report = self.journal.get_open_report(self.workspace_id, session_id)
        if report is None:
            return
        if any(item.blocking and item.resolution is None for item in report.items):
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY,
                "Resolve unknown tool side effects before creating a repair plan",
            )

    def _require_live_run(self, session_id) -> WorkflowRun:
        self.planning._session(session_id)
        run = self.admission._current_run(session_id)
        if run is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Workflow run is missing")
        if run.status.terminal:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "a terminal Workflow cannot be paused; generate a repair plan instead",
            )
        if run.status.value not in LIVE_STATUSES:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                f"a {run.status.value} Workflow cannot be paused",
            )
        return run

    def _check_row_version(self, run: WorkflowRun, expected: int | None) -> None:
        if expected is not None and run.row_version != expected:
            raise ApplicationError(
                ApplicationErrorCode.STALE, "Workflow run version changed; refresh and retry"
            )

    def _request_pause(self, workflow_run_id: str, *, command_id: str | None = None) -> WorkflowRun:
        runtime = getattr(self.context, "runtime", None)
        if runtime is None:
            raise ApplicationError(ApplicationErrorCode.UNAVAILABLE, "Workflow runtime is missing")
        return runtime.transitions.request_pause(workflow_run_id, command_id=command_id)

    def _ensure_mode_binding(self, request, run: WorkflowRun, *, mode: str):
        current = self.planning.repo.binding(self.workspace_id, request.session_id)
        if (
            current is not None
            and current.mode == mode
            and current.parent_run_id == run.workflow_run_id
            and current.parent_revision_id == run.workflow_revision_id
            and current.status == "active"
        ):
            return current, current.current_draft_id
        now = self.journal.now()
        previous_draft_id = current.current_draft_id if current is not None else None
        context_ref = self._context_ref(request.session_id, current)
        created = PlanningBinding(
            planning_binding_id=self.context.application.id_source.new_id("wplan"),
            workspace_id=self.workspace_id,
            session_id=request.session_id,
            origin_interaction_id=request.origin_interaction_id,
            mode=mode,
            parent_run_id=run.workflow_run_id,
            parent_revision_id=run.workflow_revision_id,
            context_ref=context_ref,
            row_version=1,
            created_at=now,
            updated_at=now,
        )

        def work(_):
            active = self.planning.repo.binding(self.workspace_id, request.session_id)
            if (
                active is not None
                and active.mode == mode
                and active.parent_run_id == run.workflow_run_id
                and active.status == "active"
            ):
                return active, active.current_draft_id
            if active is not None and active.status == "active":
                self.planning.repo.save_binding(
                    active.model_copy(
                        update={
                            "status": "superseded",
                            "row_version": active.row_version + 1,
                            "updated_at": now,
                        }
                    ),
                    expected=active.row_version,
                )
            return self.planning.repo.save_binding(created, expected=0), previous_draft_id

        return self.journal.transact(work)

    def _attach_candidate(self, binding, run, request, previous_draft_id):
        if binding.current_draft_id:
            return binding
        revision = self.journal.workflows.get_revision(self.workspace_id, run.workflow_revision_id)
        if revision is None:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "parent Workflow revision is missing"
            )
        from morrow.application.workflows.replan import revision_source
        from morrow.core.workflows.planning import NodePlanningMetadata

        previous = {}
        source = revision_source(revision)
        if previous_draft_id:
            draft = self.planning.drafts.get(previous_draft_id)
            version = self.planning.repo.version(
                self.workspace_id, draft.draft.draft_id, draft.draft.row_version
            )
            if version is not None:
                previous = version.node_metadata
            # Keep the editor-facing source shape from the parent draft so later
            # typed node edits round-trip; Past checks still compile against the
            # frozen parent Revision.
            if draft is not None:
                source = draft.draft.source
        metadata = {}
        for node in source.nodes:
            metadata[node.node_id] = previous.get(node.node_id) or NodePlanningMetadata(
                title=(node.task_contract.objective[:128] or node.node_id),
                responsibility="general",
                agent_selection="preset:general",
            )
        extra = ()
        try:
            selections = self.planning._selections(
                source, binding.context_ref, metadata, request.session_id
            )
        except (ValueError, ApplicationError):
            selections = {}
            extra = ("execution_selection_incompatible",)
        draft, _version = self.planning._save(
            source,
            metadata,
            selections,
            request.command_id,
            "manual_edit",
            extra_errors=extra,
        )
        return self.planning.repo.save_binding(
            binding.model_copy(
                update={
                    "current_draft_id": draft.draft_id,
                    "row_version": binding.row_version + 1,
                    "updated_at": self.journal.now(),
                }
            ),
            expected=binding.row_version,
        )

    def _patch_for(self, binding, run, command_id):
        draft = self.planning.drafts.get(binding.current_draft_id)
        return FutureGraphPatch(
            workflow_patch_id=self.context.application.id_source.new_id("wpatch"),
            workspace_id=self.workspace_id,
            parent_run_id=run.workflow_run_id,
            base_workflow_revision_id=run.workflow_revision_id,
            expected_parent_row_version=run.row_version,
            source=draft.draft.source,
            requested_by=command_id,
        )

    def _close_binding(self, binding):
        if binding.status != "active":
            return binding
        return self.planning.repo.save_binding(
            binding.model_copy(
                update={
                    "status": "closed",
                    "row_version": binding.row_version + 1,
                    "updated_at": self.journal.now(),
                }
            ),
            expected=binding.row_version,
        )

    def _decide(self, request, run, binding, *, result_id, digest):
        draft = (
            self.planning.drafts.get(binding.current_draft_id)
            if binding is not None and binding.current_draft_id
            else None
        )
        self.planning.repo.save_decision(
            PlanDecision(
                plan_decision_id=self.context.application.id_source.new_id("wdec"),
                workspace_id=self.workspace_id,
                session_id=request.session_id,
                command_id=request.command_id,
                decision=request.decision,
                action_source=request.action_source,
                interaction_id=request.interaction_id,
                draft_id=draft.draft.draft_id if draft else None,
                draft_version=draft.draft.row_version if draft else None,
                parent_run_id=run.workflow_run_id,
                visible_version_binding=visible_version_digest(
                    draft.draft.draft_id if draft else binding.planning_binding_id,
                    draft.draft.row_version if draft else 1,
                ),
                execution_digest=request.candidate_digest,
                result_id=result_id,
                created_at=self.journal.now(),
            )
        )
        receipt = self._put_receipt(
            request.command_id,
            APPLY_OPERATION,
            digest,
            request.session_id,
            "workflow_run" if result_id else "planning_binding",
            result_id or binding.planning_binding_id,
        )
        self._event(
            request.session_id,
            {
                "decision": request.decision,
                "parent_run_id": run.workflow_run_id,
                "result_id": result_id,
            },
        )
        return receipt

    def _runtime(self):
        runtime = getattr(self.context, "runtime", None)
        if runtime is None:
            raise ApplicationError(ApplicationErrorCode.UNAVAILABLE, "Workflow runtime is missing")
        return runtime

    def _context_ref(self, session_id, current: PlanningBinding | None) -> PlanningContextRef:
        if current is not None:
            return current.context_ref
        settings, settings_digest = self.planning._settings(session_id)
        session = self.planning._session(session_id)
        return PlanningContextRef(
            conversation_position=session.conversation_position,
            constraints=self.context.products.graph_planner._constraints(),
            model=settings.model,
            generation=settings.generation,
            settings_digest=settings_digest,
        )

    def _replay(self, command_id, operation, digest) -> ApplicationCommandReceipt | None:
        existing = self.journal.get_application_command_receipt(self.workspace_id, command_id)
        if existing is None:
            return None
        if existing.operation != operation or existing.request_digest != digest:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "command ID was reused with a different request",
            )
        return existing.model_copy(update={"disposition": ApplicationCommandDisposition.REPLAY})

    def _event(self, session_id, payload):
        self.journal.transact(
            lambda _: self.planning.repo.event(self.workspace_id, session_id, payload)
        )

    def _put_receipt(self, command_id, operation, digest, session_id, result_kind, result_id):
        return self.journal.put_application_command_receipt(
            self.workspace_id,
            ApplicationCommandReceipt(
                command_id=command_id,
                workspace_id=self.workspace_id,
                session_id=session_id,
                operation=operation,
                request_digest=digest,
                result_kind=result_kind,
                result_id=result_id,
            ),
        )
