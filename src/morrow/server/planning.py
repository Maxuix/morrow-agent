"""Session-scoped, suggestion-only planning protocol."""

from pydantic import Field
from starlette.responses import JSONResponse
from starlette.routing import Route

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.workflows.definitions import WorkflowDefinitionSource
from morrow.core.workflows.planning import (
    ApplyWorkflowChangeRequest,
    CommandId,
    NodePlanningMetadata,
    PausePlanningGenerationRequest,
    PauseWorkflowPlanRequest,
    PlanningValue,
    PlanNodeEdit,
    PlanWorkflowRequest,
    PrepareWorkflowChangeRequest,
    PrepareWorkflowRepairRequest,
    ResumePlanningGenerationRequest,
    ResumeWorkflowPlanRequest,
    SessionId,
    StartWorkflowPlanRequest,
)
from morrow.server.protocol import CommandRequest


class EditPlanRequest(PlanningValue):
    command_id: CommandId
    binding_id: str
    expected_version: int = Field(ge=1, strict=True)
    source: WorkflowDefinitionSource
    confirm_impact: bool = False
    metadata: dict[str, NodePlanningMetadata] = Field(default_factory=dict, max_length=16)


class WorkflowNodeSteerRequest(CommandRequest):
    """Targeted node steering: exact binding, root-session authorization (P6.2)."""

    session_id: str = Field(min_length=1, max_length=128)
    workflow_run_id: str = Field(min_length=1, max_length=128)
    node_run_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=4096)
    expected_revision: str | None = Field(default=None, max_length=128)


class TaskPlanControlRequest(CommandRequest):
    """Raw user text for state-aware control; not a planning payload.

    ``client_message_id`` is the client-side retry identity: the durable
    receipt is keyed by ``command_id`` and the pair must stay stable across
    retries of the same input. ``expected_workflow_run_id`` is the run the
    client saw when composing the command; when the server's current target
    no longer matches, the command settles as unresolved instead of acting on
    a different (or missing) run.
    """

    session_id: SessionId
    text: str = Field(min_length=1, max_length=4096)
    client_message_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_workflow_run_id: str | None = Field(default=None, max_length=128)


def wire(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: wire(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(v) for v in value]
    return value


def operation_wire(value):
    # Stale/expired candidates stay internal evidence; clients read the current draft
    # or the explicit history endpoint instead of a full second source per operation.
    return wire(value.model_dump(mode="json", exclude={"candidate_source"}))


def planning_routes(host, parse_body, context):
    def service(request):
        return context(request).chat.planning

    def admission(request):
        return context(request).chat.admission

    async def plan(request):
        sid = request.path_params["session_id"]
        if request.method == "GET":
            return JSONResponse(
                wire(await host.execute_query(lambda: admission(request).view(sid)))
            )
        body = await parse_body(request, PlanWorkflowRequest)
        if body.session_id != sid:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Planning Session mismatch")
        prepared = await host.execute_command(lambda: service(request).begin(body))
        result = await host.execute_preparation(
            lambda: service(request).dispatch(prepared, command=host.execute_command)
        )
        return JSONResponse(operation_wire(result))

    async def edit(request):
        body = await parse_body(request, EditPlanRequest)
        result = await host.execute_command(
            lambda: service(request).edit(
                request.path_params["session_id"],
                body.binding_id,
                source=body.source,
                metadata=body.metadata,
                expected_version=body.expected_version,
                command_id=body.command_id,
                confirm_impact=body.confirm_impact,
            )
        )
        return JSONResponse(operation_wire(result))

    async def edit_node(request):
        body = await parse_body(request, PlanNodeEdit)
        result = await host.execute_command(
            lambda: service(request).edit_node(request.path_params["session_id"], body)
        )
        return JSONResponse(operation_wire(result))

    async def operation(request):
        sid, identity = request.path_params["session_id"], request.path_params["operation_id"]

        def read(svc):
            if request.method == "POST":
                result = svc.cancel(sid, identity)
            else:
                result = svc.get_operation(sid, identity)
            return {**operation_wire(result), "usage": svc.request_usage(identity)}

        if request.method == "POST":
            return JSONResponse(wire(await host.execute_command(lambda: read(service(request)))))
        return JSONResponse(wire(await host.execute_query(lambda: read(service(request)))))

    async def pause_generation(request):
        """Generation-scoped pause (P05, B coordination with lane D).

        Distinct from the run-scoped /pause: pauses the planning operation
        itself while it waits on the model; the suspension lands asynchronously
        and the operation view settles to ``generation.status == "paused"``.
        """
        sid = request.path_params["session_id"]
        body = await parse_body(request, PausePlanningGenerationRequest)
        if body.session_id != sid:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Planning Session mismatch")

        def command():
            return operation_wire(service(request).pause(body))

        return JSONResponse(await host.execute_command(command))

    async def resume_generation(request):
        """Generation-scoped resume: new request sequence, saved candidate wins.

        When resume returns a prepared input the dispatch runs here and the
        terminal operation is returned directly (A09: a saved candidate is
        applied without paying for the same generation request again).
        """
        sid = request.path_params["session_id"]
        body = await parse_body(request, ResumePlanningGenerationRequest)
        if body.session_id != sid:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Planning Session mismatch")
        svc = service(request)

        def command():
            return svc.resume(body)

        result = await host.execute_command(command)
        if result.prepared is not None:
            operation = await host.execute_preparation(
                lambda: svc.dispatch(result.prepared, command=host.execute_command)
            )
        else:
            operation = result.operation
        return JSONResponse(operation_wire(operation))

    async def history(request):
        def query():
            svc = service(request)
            sid = request.path_params["session_id"]
            binding = svc.repo.binding(svc.workspace_id, sid)
            if binding is None or binding.current_draft_id != request.path_params["draft_id"]:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Planning draft is missing")
            return svc.repo.history(
                svc.workspace_id,
                binding.current_draft_id,
                after=int(request.query_params.get("after", "0")),
                limit=int(request.query_params.get("limit", "50")),
            )

        return JSONResponse(wire(await host.execute_query(query)))

    async def events(request):
        def query():
            svc = service(request)
            sid = request.path_params["session_id"]
            svc._session(sid)
            return svc.repo.events(
                svc.workspace_id, sid, after=int(request.query_params.get("after", "0"))
            )

        return JSONResponse(wire(await host.execute_query(query)))

    async def pause(request):
        sid = request.path_params["session_id"]
        body = await parse_body(request, PauseWorkflowPlanRequest)
        if body.session_id != sid:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Planning Session mismatch")

        def command():
            result = context(request).chat.changes.pause(body)
            return {
                "run": admission(request).run_projection(result.run),
                "replayed": result.replayed,
                "receipt": result.receipt,
            }

        return JSONResponse(wire(await host.execute_command(command)))

    async def change(request):
        sid = request.path_params["session_id"]
        body = await parse_body(request, PrepareWorkflowChangeRequest)
        if body.session_id != sid:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Planning Session mismatch")

        def command():
            result = context(request).chat.changes.prepare(body)
            return {
                "run": admission(request).run_projection(result.run),
                "binding": result.binding,
                "replayed": result.replayed,
                "receipt": result.receipt,
            }

        return JSONResponse(wire(await host.execute_command(command)))

    async def apply_change(request):
        sid = request.path_params["session_id"]
        body = await parse_body(request, ApplyWorkflowChangeRequest)
        if body.session_id != sid:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Planning Session mismatch")

        def command():
            result = context(request).chat.changes.apply(body)
            return {
                "run": admission(request).run_projection(result.run),
                "child": admission(request).run_projection(result.child) if result.child else None,
                "binding": result.binding,
                "decision": result.decision,
                "replayed": result.replayed,
                "receipt": result.receipt,
            }

        return JSONResponse(wire(await host.execute_command(command)))

    async def resume(request):
        sid = request.path_params["session_id"]
        body = await parse_body(request, ResumeWorkflowPlanRequest)
        if body.session_id != sid:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Planning Session mismatch")

        def command():
            result = context(request).chat.changes.resume(body)
            return {
                "run": admission(request).run_projection(result.run),
                "replayed": result.replayed,
                "receipt": result.receipt,
            }

        return JSONResponse(wire(await host.execute_command(command)))

    async def repair(request):
        sid = request.path_params["session_id"]
        body = await parse_body(request, PrepareWorkflowRepairRequest)
        if body.session_id != sid:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Planning Session mismatch")

        def command():
            result = context(request).chat.changes.prepare_repair(body)
            return {
                "run": admission(request).run_projection(result.run),
                "binding": result.binding,
                "replayed": result.replayed,
                "receipt": result.receipt,
            }

        return JSONResponse(wire(await host.execute_command(command)))

    async def start(request):
        sid = request.path_params["session_id"]
        body = await parse_body(request, StartWorkflowPlanRequest)
        if body.session_id != sid:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Planning Session mismatch")

        def command():
            started = admission(request).start(body)
            if not started.replayed:
                from morrow.server.commands import ServerCommands

                ServerCommands(context(request))._emit_run_created(started.run, relation="start")
            return {
                "run": started.run,
                "receipt": started.receipt,
                "replayed": started.replayed,
            }

        return JSONResponse(wire(await host.execute_command(command)))

    async def control(request):
        sid = request.path_params["session_id"]
        body = await parse_body(request, TaskPlanControlRequest)
        if body.session_id != sid:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Planning Session mismatch")
        receipts = context(request).chat.control_receipts

        # Acceptance is durable and happens before interpretation (D06): the
        # raw text is committed first, then classified and executed.
        accepted, replayed = await host.execute_command(
            lambda: receipts.accept(
                sid,
                command_id=body.command_id,
                text=body.text,
                client_message_id=body.client_message_id,
            )
        )
        if replayed and accepted.status != "accepted":
            # A retried input replays the stored receipt; the command is never
            # executed twice and the client can reconcile a lost response.
            return JSONResponse(
                wire({**accepted.outcome, "receipt": receipts.wire(accepted), "replayed": True})
            )

        async def run():
            controls = context(request).chat.controls
            proposal = await controls.classify(sid, text=body.text)
            if (
                body.expected_workflow_run_id is not None
                and proposal["control"]["target"]["workflow_run_id"]
                != body.expected_workflow_run_id
            ):
                # The run the client composed against is gone or replaced:
                # settle the receipt normally without executing anything, so
                # the client keeps its draft and can retry against the fact.
                return {
                    "disposition": "unresolved",
                    "intent": "none",
                    "message": "执行目标已变化或不存在；请核对当前任务后重试。",
                    "state": proposal["state"],
                    "control": proposal["control"],
                    "deterministic": proposal.get("deterministic", False),
                }
            outcome = await controls.execute(
                sid,
                proposal=proposal,
                text=body.text,
                command_id=body.command_id,
                command=host.execute_command,
            )
            # The response carries the same control projection the decision was
            # taken from, so a client can route and label without a second read.
            return {
                **outcome,
                "state": proposal["state"],
                "control": proposal["control"],
                "deterministic": proposal.get("deterministic", False),
            }

        try:
            outcome = await host.execute_preparation(run)
        except ApplicationError as error:
            code, message = error.code.value, str(error)
            await host.execute_command(
                lambda: receipts.fail(accepted, error_code=code, message=message)
            )
            raise
        except Exception:
            await host.execute_command(
                lambda: receipts.fail(accepted, error_code="internal", message=None)
            )
            raise
        settled = await host.execute_command(
            lambda: receipts.settle(
                accepted,
                state=outcome.get("state"),
                intent=outcome.get("intent"),
                outcome=outcome,
            )
        )
        return JSONResponse(wire({**outcome, "receipt": receipts.wire(settled)}))

    async def control_receipts(request):
        sid = request.path_params["session_id"]
        receipts = context(request).chat.control_receipts
        after = int(request.query_params.get("after", "0"))
        limit = int(request.query_params.get("limit", "50"))
        return JSONResponse(
            wire(
                await host.execute_query(lambda: receipts.list(sid, after_unix=after, limit=limit))
            )
        )

    async def node_steer(request):
        sid = request.path_params["session_id"]
        body = await parse_body(request, WorkflowNodeSteerRequest)
        if body.session_id != sid:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Planning Session mismatch")

        def run():
            controls = context(request).chat.controls
            return controls.node_steer(
                sid,
                workflow_run_id=body.workflow_run_id,
                node_run_id=body.node_run_id,
                text=body.text,
                command_id=body.command_id,
                expected_revision=body.expected_revision,
            )

        return JSONResponse(wire(await host.execute_command(run)))

    root = "/v1/workspaces/{workspace_id}/sessions/{session_id}/task-plan"
    return [
        Route(root + "/node-steer", node_steer, methods=["POST"]),
        Route(root, plan, methods=["GET", "POST"]),
        Route(root + "/start", start, methods=["POST"]),
        Route(root + "/pause", pause, methods=["POST"]),
        Route(root + "/change", change, methods=["POST"]),
        Route(root + "/apply-change", apply_change, methods=["POST"]),
        Route(root + "/resume", resume, methods=["POST"]),
        Route(root + "/repair", repair, methods=["POST"]),
        Route(root + "/control", control, methods=["POST"]),
        Route(root + "/control-receipts", control_receipts, methods=["GET"]),
        Route(root + "/edit", edit, methods=["POST"]),
        Route(root + "/nodes", edit_node, methods=["POST"]),
        Route(root + "/operations/{operation_id}", operation, methods=["GET", "POST"]),
        Route(root + "/operations/{operation_id}/pause", pause_generation, methods=["POST"]),
        Route(root + "/operations/{operation_id}/resume", resume_generation, methods=["POST"]),
        Route(root + "/drafts/{draft_id}/history", history),
        Route(root + "/events", events),
    ]
