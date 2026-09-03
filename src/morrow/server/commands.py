"""Command and query handlers for the local Core API.

Every handler runs on the Core Host's single runtime loop. Mutations go
through the bounded serialized bus; each carries a client-supplied idempotent
Command ID end to end — natively where the underlying service owns receipts
(session/task commands, workflow start, approval resolution, rerun), or through
the post-commit receipt wrapper here for the naturally idempotent transition
commands. The replay path always rebuilds its answer from current durable
facts, so a retried command can never double-apply.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from morrow.application.api_context import request_digest
from morrow.core.application import (
    WORKFLOW_RUN_CREATED_EVENT,
    WORKFLOW_RUN_STATUS_EVENT,
    ApplicationCommandDisposition,
    ApplicationCommandReceipt,
    ApplicationError,
    ApplicationErrorCode,
)
from morrow.core.execution import ApprovalResolution, ToolExecutionState
from morrow.core.workflows.contracts import TaskContract
from morrow.core.workflows.runs import WorkflowStatus

from . import projections
from .protocol import (
    ApprovalResolveRequest,
    PatchCommandRequest,
    SessionCreateRequest,
    TaskCreateRequest,
    TaskTransitionRequest,
    WorkflowAbandonRequest,
    WorkflowControlRequest,
    WorkflowRerunRequest,
    WorkflowResumeRequest,
    WorkflowStartRequest,
)


@dataclass(frozen=True)
class CommandOutcome:
    result: dict[str, Any]
    receipt: ApplicationCommandReceipt | None

    def wire(self) -> dict[str, Any]:
        return {"result": self.result, "receipt": projections.receipt_wire(self.receipt)}


class ServerCommands:
    def __init__(self, context) -> None:
        self.context = context
        self.journal = context.journal
        self.workspace_id = context.workspace_id

    # Internal helpers ---------------------------------------------------------

    def _idempotent(
        self,
        operation: str,
        command_id: str | None,
        payload: dict,
        execute: Callable[[], tuple[Any, str]],
        rebuild: Callable[[ApplicationCommandReceipt], Any],
        *,
        result_kind: str = "workflow_run",
    ) -> tuple[Any, ApplicationCommandReceipt]:
        """Receipt wrapper for the naturally idempotent transition commands.

        The check, execution and receipt write serialize on the single-writer
        command bus. A crash between the service commit and this receipt write
        is safe because every wrapped command is service-level idempotent.
        """

        command_id = command_id or self.context.api.id_source.new_id("cmd")
        digest = request_digest(operation, payload)
        existing = self.journal.get_application_command_receipt(self.workspace_id, command_id)
        if existing is not None:
            if existing.operation != operation or existing.request_digest != digest:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    "command ID was reused with a different request",
                )
            return rebuild(existing), existing.model_copy(
                update={"disposition": ApplicationCommandDisposition.REPLAY}
            )
        value, result_id = execute()
        receipt = self.journal.put_application_command_receipt(
            self.workspace_id,
            ApplicationCommandReceipt(
                command_id=command_id,
                workspace_id=self.workspace_id,
                operation=operation,
                request_digest=digest,
                result_kind=result_kind,
                result_id=result_id,
            ),
        )
        return value, receipt

    def _require_run(self, workflow_run_id: str):
        run = self.context.runtime.transitions.get_run(workflow_run_id)
        if run is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "workflow run is missing")
        return run

    def _run_wire_by_id(self, workflow_run_id: str) -> dict[str, Any]:
        return projections.run_wire(self._require_run(workflow_run_id))

    def _emit_run_created(self, run, *, relation: str) -> None:
        self.context.emitter.emit(
            WORKFLOW_RUN_CREATED_EVENT,
            "workflow_run",
            run.workflow_run_id,
            {
                "workflow_revision_id": run.workflow_revision_id,
                "run_relation": run.run_relation,
                "parent_run_id": run.parent_run_id,
                "relation": relation,
            },
        )

    # Session / Task commands --------------------------------------------------

    def session_create(self, request: SessionCreateRequest) -> CommandOutcome:
        result = self.context.api.create_session(
            session_id=request.session_id, command_id=request.command_id
        )
        return CommandOutcome({"session": projections.session_wire(result.value)}, result.receipt)

    def task_create(self, request: TaskCreateRequest) -> CommandOutcome:
        result = self.context.api.task_new(request.session_id, command_id=request.command_id)
        return CommandOutcome({"task": projections.task_wire(result.value)}, result.receipt)

    def task_accept(self, task_run_id: str, request: TaskTransitionRequest) -> CommandOutcome:
        result = self.context.api.task_accept(
            task_run_id,
            command_id=request.command_id,
            expected_row_version=request.expected_row_version,
        )
        return CommandOutcome({"task": projections.task_wire(result.value)}, result.receipt)

    def task_cancel(self, task_run_id: str, request: TaskTransitionRequest) -> CommandOutcome:
        result = self.context.api.task_cancel(
            task_run_id,
            command_id=request.command_id,
            expected_row_version=request.expected_row_version,
        )
        return CommandOutcome({"task": projections.task_wire(result.value)}, result.receipt)

    def task_resume(self, task_run_id: str, request: TaskTransitionRequest) -> CommandOutcome:
        result = self.context.api.task_resume(
            task_run_id,
            command_id=request.command_id,
            expected_row_version=request.expected_row_version,
        )
        return CommandOutcome({"task": projections.task_wire(result.value)}, result.receipt)

    # Workflow run control ------------------------------------------------------

    def workflow_start(self, request: WorkflowStartRequest) -> CommandOutcome:
        from morrow.application.workflows.start import StartWorkflowCommand

        command_id = request.command_id or self.context.api.id_source.new_id("cmd")
        command = StartWorkflowCommand(
            workflow_definition_id=request.workflow_definition_id,
            workflow_revision_id=request.workflow_revision_id,
            session_id=request.session_id,
            root_task_run_id=request.root_task_run_id,
            expected_root_row_version=request.expected_root_row_version,
            contract=TaskContract(objective=request.objective),
            command_id=command_id,
            client_message_id=request.client_message_id,
        )
        started = self.context.management.start_foreground(command)
        if not started.replayed:
            self._emit_run_created(started.run, relation="start")
        driving = self.context.supervisor.ensure_driver(
            started.run.workflow_run_id,
            lambda: self.context.runtime.scheduler.run(started.run.workflow_run_id),
        )
        return CommandOutcome(
            {
                "run": projections.run_wire(started.run),
                "driving": driving,
                "replayed": started.replayed,
            },
            started.receipt,
        )

    def workflow_pause(self, workflow_run_id: str, request: WorkflowControlRequest):
        self._require_run(workflow_run_id)
        value, receipt = self._idempotent(
            "workflow_pause",
            request.command_id,
            {"workflow_run_id": workflow_run_id},
            lambda: (
                self.context.management.pause(workflow_run_id),
                workflow_run_id,
            ),
            lambda _existing: self._require_run(workflow_run_id),
        )
        return CommandOutcome({"run": projections.run_wire(value)}, receipt)

    def workflow_resume(self, workflow_run_id: str, request: WorkflowResumeRequest):
        run = self._require_run(workflow_run_id)
        value, receipt = self._idempotent(
            "workflow_resume",
            request.command_id,
            {"workflow_run_id": workflow_run_id},
            lambda: (
                self.context.runtime.transitions.resume_run(run.workflow_run_id),
                workflow_run_id,
            ),
            lambda _existing: self._require_run(workflow_run_id),
        )
        driving = False
        if request.drive and not value.status.terminal:
            driving = self.context.supervisor.ensure_driver(
                workflow_run_id,
                lambda: self.context.management.resume(workflow_run_id),
            )
        return CommandOutcome({"run": projections.run_wire(value), "driving": driving}, receipt)

    async def workflow_cancel(self, workflow_run_id: str, request: WorkflowControlRequest):
        """Record the user-cancel intent, stop the driver, settle via recovery."""

        run = self._require_run(workflow_run_id)
        if run.status.terminal:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "a terminal Workflow cannot be cancelled"
            )
        if run.status is WorkflowStatus.PAUSED:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "a paused Workflow must be resumed before cancellation",
            )

        def execute():
            return (
                self.context.runtime.transitions.set_pending_user_cancel(workflow_run_id),
                workflow_run_id,
            )

        value, receipt = self._idempotent(
            "workflow_cancel",
            request.command_id,
            {"workflow_run_id": workflow_run_id},
            execute,
            lambda _existing: self._require_run(workflow_run_id),
        )
        await self.context.supervisor.cancel_driver(workflow_run_id)
        driving = self.context.supervisor.ensure_driver(
            workflow_run_id, lambda: self._drive_cancel(workflow_run_id)
        )
        return CommandOutcome({"run": projections.run_wire(value), "driving": driving}, receipt)

    async def _drive_cancel(self, workflow_run_id: str) -> None:
        run = self.context.runtime.transitions.get_run(workflow_run_id)
        if run is None or run.status.terminal:
            return
        nodes = self.journal.workflows.list_nodes(self.workspace_id, workflow_run_id)
        active = any(
            node.status in (WorkflowStatus.RUNNING, WorkflowStatus.BLOCKED) for node in nodes
        )
        if active:
            # The fixed cancellation mapping: unknown evidence blocks first and
            # the recorded user-cancel intent owns the terminal transition.
            await self.context.runtime.scheduler.recover(workflow_run_id)
        else:
            self.context.runtime.finalizer.finalize_cancel(workflow_run_id, reason="user_cancelled")

    def workflow_rerun(self, workflow_run_id: str, request: WorkflowRerunRequest):
        self._require_run(workflow_run_id)
        command_id = request.command_id or self.context.api.id_source.new_id("cmd")
        replayed = (
            request.command_id is not None
            and self.journal.get_application_command_receipt(self.workspace_id, command_id)
            is not None
        )
        application = self.context.management.rerun(
            workflow_run_id, full=request.full, command_id=command_id
        )
        receipt = self.journal.get_application_command_receipt(self.workspace_id, command_id)
        if replayed and receipt is not None:
            receipt = receipt.model_copy(
                update={"disposition": ApplicationCommandDisposition.REPLAY}
            )
        if not replayed:
            self._emit_run_created(application.child, relation="rerun")
        driving = self.context.supervisor.ensure_driver(
            application.child.workflow_run_id,
            lambda: self.context.runtime.scheduler.run(application.child.workflow_run_id),
        )
        return CommandOutcome(
            {
                "parent": projections.run_wire(application.parent),
                "child": projections.run_wire(application.child),
                "full": application.full,
                "inherited_node_ids": list(application.inherited_node_ids),
                "execution_node_ids": list(application.execution_node_ids),
                "driving": driving,
            },
            receipt,
        )

    def workflow_abandon(self, workflow_run_id: str, request: WorkflowAbandonRequest):
        self._require_run(workflow_run_id)
        value, receipt = self._idempotent(
            "workflow_abandon",
            request.command_id,
            {
                "workflow_run_id": workflow_run_id,
                "expected_row_version": request.expected_row_version,
            },
            lambda: (
                self.context.management.abandon(
                    workflow_run_id, expected_row_version=request.expected_row_version
                ),
                workflow_run_id,
            ),
            lambda _existing: self._require_run(workflow_run_id),
        )
        return CommandOutcome({"run": projections.run_wire(value)}, receipt)

    # Future graph patches -------------------------------------------------------

    def patch_validate(self, request: PatchCommandRequest) -> dict[str, Any]:
        validation = self.context.management.validate_patch(request.patch)
        return {
            "valid": validation.compilation.candidate is not None,
            "diagnostics": [
                {
                    "severity": item.severity.value,
                    "code": item.code,
                    "message": item.message,
                }
                for item in validation.compilation.diagnostics
            ],
            "past_node_ids": list(validation.past_node_ids),
            "execution_node_ids": list(validation.execution_node_ids),
        }

    def patch_save(self, request: PatchCommandRequest) -> CommandOutcome:
        patch = request.patch
        value, receipt = self._idempotent(
            "patch_save",
            request.command_id,
            {"patch_digest": patch.source.content_hash, "patch_id": patch.workflow_patch_id},
            lambda: (self.context.management.save_patch(patch), patch.workflow_patch_id),
            lambda _existing: self.context.management.save_patch(patch),
            result_kind="workflow_patch",
        )
        return CommandOutcome(
            {
                "workflow_patch_id": value.patch.workflow_patch_id,
                "workflow_revision_id": value.revision.workflow_revision_id,
                "validation": {
                    "past_node_ids": list(value.validation.past_node_ids),
                    "execution_node_ids": list(value.validation.execution_node_ids),
                },
            },
            receipt,
        )

    def patch_apply(self, request: PatchCommandRequest) -> CommandOutcome:
        patch = request.patch

        def rebuild(existing: ApplicationCommandReceipt):
            child = (
                self.context.runtime.transitions.get_run(existing.result_id)
                if existing.result_id
                else None
            )
            return {
                "workflow_patch_id": patch.workflow_patch_id,
                "child_run": projections.run_wire(child) if child is not None else None,
            }

        def execute():
            application = self.context.management.apply_patch(patch)
            child_id = application.child.workflow_run_id if application.child is not None else None
            return application, child_id or patch.workflow_patch_id

        value, receipt = self._idempotent(
            "patch_apply",
            request.command_id,
            {"patch_digest": patch.source.content_hash, "patch_id": patch.workflow_patch_id},
            execute,
            rebuild,
            result_kind="workflow_patch",
        )
        if receipt.disposition is not ApplicationCommandDisposition.REPLAY:
            self.context.emitter.emit(
                WORKFLOW_RUN_STATUS_EVENT,
                "workflow_run",
                value.parent.workflow_run_id,
                {"status": "superseded", "superseded_reason": "continued_by_patch"},
            )
            child = value.child
            driving = False
            if child is not None:
                self._emit_run_created(child, relation="continuation")
                if not child.status.terminal:
                    driving = self.context.supervisor.ensure_driver(
                        child.workflow_run_id,
                        lambda: self.context.runtime.scheduler.run(child.workflow_run_id),
                    )
            return CommandOutcome(
                {
                    "workflow_patch_id": patch.workflow_patch_id,
                    "workflow_revision_id": value.revision.workflow_revision_id,
                    # The handoff supersedes the parent inside its transaction;
                    # re-read so the response reflects committed facts.
                    "parent_run": self._run_wire_by_id(value.parent.workflow_run_id),
                    "child_run": projections.run_wire(child) if child is not None else None,
                    "driving": driving,
                },
                receipt,
            )
        return CommandOutcome(value, receipt)

    # Approvals -------------------------------------------------------------------

    def approval_resolve(self, approval_id: str, request: ApprovalResolveRequest) -> CommandOutcome:
        approval = self.journal.get_approval(self.workspace_id, approval_id)
        if approval is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "approval is missing")
        execution = self.journal.get_execution(self.workspace_id, approval.tool_execution_id)
        if execution is None:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "approval execution is missing"
            )
        command_id = request.command_id or self.context.api.id_source.new_id("cmd")
        payload = {
            "approval_id": approval_id,
            "tool_execution_id": execution.tool_execution_id,
            "approved": request.approved,
        }
        digest = request_digest("approval_resolve", payload)
        existing = self.journal.get_application_command_receipt(self.workspace_id, command_id)
        if existing is not None:
            if existing.operation != "approval_resolve" or existing.request_digest != digest:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    "command ID was reused with a different request",
                )
            current = self.journal.get_approval(self.workspace_id, approval_id)
            if current is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "approval result is missing"
                )
            return CommandOutcome(
                {
                    "approval": projections.approval_wire(current, execution),
                    "delivery": "replay",
                },
                existing.model_copy(update={"disposition": ApplicationCommandDisposition.REPLAY}),
            )
        if approval.resolution is not ApprovalResolution.PENDING:
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "approval is already resolved")
        if self.context.approval_waiters.waiting(approval_id):
            if not self.context.approval_waiters.deliver(approval_id, approved=request.approved):
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "approval resolution is already in flight"
                )
            receipt = self.journal.put_application_command_receipt(
                self.workspace_id,
                ApplicationCommandReceipt(
                    command_id=command_id,
                    workspace_id=self.workspace_id,
                    session_id=execution.session_id,
                    operation="approval_resolve",
                    request_digest=digest,
                    result_kind="approval",
                    result_id=approval_id,
                ),
            )
            self.context.emitter.emit(
                "approval.resolved",
                "approval",
                approval_id,
                {
                    "resolution": "approved" if request.approved else "denied",
                    "delivery": "live",
                },
            )
            return CommandOutcome(
                {
                    "approval": projections.approval_wire(approval, execution),
                    "delivery": "live",
                },
                receipt,
            )
        resolved = self.context.api.resolve_approval(
            execution, approval, approved=request.approved, command_id=command_id
        )
        saved_execution, saved_approval, did_execute = resolved.value
        return CommandOutcome(
            {
                "approval": projections.approval_wire(saved_approval, saved_execution),
                "delivery": "durable",
                "executed": did_execute,
            },
            resolved.receipt,
        )

    # Queries -----------------------------------------------------------------

    def meta(self) -> dict[str, Any]:
        return {
            "protocol_version": 1,
            "workspace_id": self.workspace_id,
            "latest_cursor": self.journal.latest_application_event_cursor(self.workspace_id),
        }

    def events_page(self, *, after: int, limit: int) -> dict[str, Any]:
        page = self.context.api.list_events(after_cursor=after, limit=limit)
        return {
            "events": [projections.event_wire(item) for item in page.items],
            "latest_cursor": self.journal.latest_application_event_cursor(self.workspace_id),
            "has_more": page.next_cursor is not None,
        }

    def snapshot(self) -> dict[str, Any]:
        """One consistent read: max cursor, run summaries and pending approvals."""

        def work(txn) -> dict[str, Any]:
            cursor = txn.latest_application_event_cursor(self.workspace_id)
            runs = self.journal.workflows.list_runs(self.workspace_id)
            return {
                "cursor": cursor,
                "workflow_runs": [projections.run_wire(run) for run in runs],
                "pending_approvals": self._pending_approvals(),
            }

        return self.journal.transact(work)

    def list_sessions(self, *, cursor: str | None, limit: int) -> dict[str, Any]:
        page = self.context.api.list_sessions(cursor=cursor, limit=limit)
        return {
            "sessions": [projections.session_wire(item) for item in page.items],
            "next_cursor": page.next_cursor,
        }

    def get_session(self, session_id: str) -> dict[str, Any]:
        session = self.context.api.get_session(session_id)
        if session is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "session is missing")
        return {"session": projections.session_wire(session)}

    def list_tasks(self, session_id: str, *, cursor: str | None, limit: int) -> dict[str, Any]:
        page = self.context.api.list_tasks(session_id, cursor=cursor, limit=limit)
        return {
            "tasks": [projections.task_wire(item) for item in page.items],
            "next_cursor": page.next_cursor,
        }

    def get_task(self, task_run_id: str) -> dict[str, Any]:
        task = self.context.api.get_task(task_run_id)
        if task is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "task is missing")
        return {"task": projections.task_wire(task)}

    def list_runs(self, *, limit: int, after: str | None) -> dict[str, Any]:
        runs = self.context.runtime.queries.list_runs(limit=limit, after=after)
        return {"workflow_runs": [projections.run_wire(run) for run in runs]}

    def get_run_view(self, workflow_run_id: str) -> dict[str, Any]:
        view = self.context.runtime.queries.get_run_view(workflow_run_id)
        if view is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "workflow run is missing")
        return {"view": projections.run_view_wire(view)}

    def get_node_view(self, workflow_run_id: str, node_run_id: str) -> dict[str, Any]:
        view = self.context.runtime.queries.get_node_view(node_run_id)
        if view is None or view.node.workflow_run_id != workflow_run_id:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "workflow node is missing")
        return {"view": projections.node_view_wire(view)}

    def get_agent_run(self, agent_run_id: str) -> dict[str, Any]:
        observation = self.context.api.get_agent_run_observation(agent_run_id)
        if observation is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "agent run is missing")
        return {"observation": observation.model_dump(mode="json")}

    def list_artifacts(
        self, *, session_id: str | None, task_run_id: str | None, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        page = self.context.api.list_artifacts(
            session_id=session_id, task_run_id=task_run_id, cursor=cursor, limit=limit
        )
        return {
            "artifacts": [projections.artifact_wire(item) for item in page.items],
            "next_cursor": page.next_cursor,
        }

    def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        metadata = self.context.api.get_artifact(artifact_id)
        if metadata is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "artifact is missing")
        return {"artifact": projections.artifact_wire(metadata)}

    def list_approvals(self, *, pending_only: bool) -> dict[str, Any]:
        if pending_only:
            return {"approvals": self._pending_approvals()}
        approvals = []
        for session in self.journal.list_sessions(self.workspace_id):
            for execution in self.journal.list_session_executions(
                self.workspace_id, session.session_id
            ):
                approval = self.journal.get_approval_for_execution(
                    self.workspace_id, execution.tool_execution_id
                )
                if approval is not None:
                    approvals.append(projections.approval_wire(approval, execution))
        return {"approvals": approvals}

    def _pending_approvals(self) -> list[dict[str, Any]]:
        pending = []
        for session in self.journal.list_sessions(self.workspace_id):
            for execution in self.journal.list_session_executions(
                self.workspace_id, session.session_id
            ):
                if execution.state is not ToolExecutionState.AWAITING_APPROVAL:
                    continue
                approval = self.journal.get_approval_for_execution(
                    self.workspace_id, execution.tool_execution_id
                )
                if approval is not None and approval.resolution is ApprovalResolution.PENDING:
                    pending.append(projections.approval_wire(approval, execution))
        return pending

    # Catalog queries ------------------------------------------------------------

    def catalog_agent_definitions(self, *, limit: int, after: str | None) -> dict[str, Any]:
        views = self.context.runtime.queries.list_agent_definitions(limit=limit, after=after)
        return {"agent_definitions": [projections.agent_definition_wire(v) for v in views]}

    def catalog_agent_definition(self, definition_id: str) -> dict[str, Any]:
        view = self.context.runtime.queries.get_agent_definition(definition_id)
        if view is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "agent definition is missing")
        return {"agent_definition": projections.agent_definition_wire(view)}

    def catalog_workflow_definitions(self, *, limit: int, after: str | None) -> dict[str, Any]:
        views = self.context.runtime.queries.list_workflow_definitions(limit=limit, after=after)
        return {"workflow_definitions": [projections.workflow_definition_wire(v) for v in views]}

    def catalog_workflow_definition(self, definition_id: str) -> dict[str, Any]:
        view = self.context.runtime.queries.get_workflow_definition(definition_id)
        if view is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "workflow definition is missing")
        return {"workflow_definition": projections.workflow_definition_wire(view)}

    def catalog_workflow_revisions(
        self, *, definition_id: str | None, limit: int, after: str | None
    ) -> dict[str, Any]:
        views = self.context.runtime.queries.list_workflow_revisions(
            workflow_definition_id=definition_id, limit=limit, after=after
        )
        return {"workflow_revisions": [projections.workflow_revision_wire(v) for v in views]}

    def catalog_providers(self) -> dict[str, Any]:
        loaded = self.context.application.global_store.load()
        config = loaded.value
        providers = []
        active_model = config.active_model if config is not None else None
        if config is not None:
            for provider_id, provider in sorted(config.providers.items()):
                providers.append(
                    projections.provider_wire(
                        provider_id,
                        provider,
                        credential_configured=self.context.application.provider_service.credential_available(
                            provider_id
                        ),
                        active_model=active_model,
                    )
                )
        return {
            "providers": providers,
            "active_model": (
                {
                    "provider_id": active_model.provider_id,
                    "model_id": active_model.model_id,
                }
                if active_model is not None
                else None
            ),
        }

    def catalog_skills(self, *, limit: int = 100) -> dict[str, Any]:
        if self.context.skill_queries is None:
            return {"skills": []}
        views = self.context.skill_queries.list(scope_id=self.workspace_id, limit=limit)
        return {"skills": [projections.skill_wire(view) for view in views]}

    def catalog_tools(self) -> dict[str, Any]:
        return {"tools": [dict(item) for item in self.context.tool_catalog]}

    def catalog_artifact_contracts(self) -> dict[str, Any]:
        from morrow.core.artifacts import ArtifactKind
        from morrow.core.workflows import contracts as workflow_contracts

        contract_models = {
            kind: getattr(workflow_contracts, kind)
            for kind in (
                "TaskContract",
                "TextResult",
                "EvidenceBundle",
                "ImplementationPatch",
                "TestReport",
                "ReviewReport",
                "PlanArtifact",
                "SynthesisReport",
                "ChangeCapture",
            )
        }
        return {
            "contracts": [
                {
                    "kind": kind,
                    "version": 1,
                    "schema": model.model_json_schema(),
                }
                for kind, model in sorted(contract_models.items())
            ],
            "artifact_kinds": [kind.value for kind in ArtifactKind],
        }
