"""Explicit workspace/Session routes for the Chat protocol."""

import asyncio
import json
from typing import Literal

from pydantic import Field
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute

from morrow.core.activity import ACTIVITY_SCHEMA_VERSION
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.attachments import attachment_limits
from morrow.core.execution_pause import TurnPauseSignal
from morrow.core.interactions import InteractionRequest
from morrow.core.recovery import RecoveryResolution
from morrow.server.commands import ServerCommands
from morrow.server.protocol import SessionCreateRequest

# Internal default for the Chat task-plan entry. These flags describe the
# current server capability projection; durable planning state remains intact.
TASK_WORKFLOW_CAPABILITY = {
    "schema": 1,
    "planning": True,
    "start": True,
    "control": True,
    "pause": True,
    "change": True,
    "apply_change": True,
    "resume": True,
    "repair": True,
}

# Activity-mode negotiation (master plan agent-transparency P1.4/P2). Enabled
# with the P2 activity flow; rollback flips the flag — v1 stage/reply delivery,
# durable facts and execution control are untouched.
ACTIVITY_STREAM_ENABLED = True

_MEDIA_SNIFF_PREFIXES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF8", "image/gif"),
    (b"%PDF", "application/pdf"),
)


def _sniff_media_type(content: bytes) -> str:
    """Best-effort content type for the raw artifact read path.

    Artifacts carry no media metadata; magic bytes identify the formats the
    GUI renders inline, everything else stays an opaque download.
    """
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    for prefix, media_type in _MEDIA_SNIFF_PREFIXES:
        if content.startswith(prefix):
            return media_type
    return "application/octet-stream"


class ChatSessionCreateRequest(SessionCreateRequest):
    command_id: str


def chat_routes(host, _parse_body):

    def context(request):
        workspace_id = request.path_params.get("workspace_id", host.context.workspace_id)
        registry = host.context.workspaces
        if registry is not None:
            return registry.get(workspace_id)
        if workspace_id != host.context.workspace_id:
            raise ApplicationError(ApplicationErrorCode.CROSS_WORKSPACE, "Unknown workspace scope")
        return host.context

    def scoped(request):
        return context(request).chat

    async def sessions(request):
        if request.method == "POST":
            body = await _parse_body(request, ChatSessionCreateRequest)

            def create():
                scoped(request)
                return ServerCommands(context(request)).session_create(body).wire()

            return JSONResponse(await host.execute_command(create))

        def query():
            scoped(request)
            try:
                limit = int(request.query_params.get("limit", "50"))
            except ValueError:
                raise ApplicationError(ApplicationErrorCode.INVALID, "Invalid page limit") from None
            if not 1 <= limit <= 100:
                raise ApplicationError(ApplicationErrorCode.INVALID, "Invalid page limit")
            if any(key in request.query_params for key in ("search", "archived")):
                try:
                    offset = int(request.query_params.get("cursor", "0"))
                except ValueError:
                    raise ApplicationError(
                        ApplicationErrorCode.INVALID, "Invalid Session cursor"
                    ) from None
                c = context(request)
                include_execution = request.query_params.get("include_execution", "false") == "true"
                ids, cursor = c.journal.session_metadata.search(
                    c.workspace_id,
                    text=request.query_params.get("search", ""),
                    archived=request.query_params.get("archived") == "true",
                    offset=offset,
                    limit=limit,
                    include_execution=include_execution,
                )
                page = {
                    "sessions": [ServerCommands(c).get_session(sid)["session"] for sid in ids],
                    "next_cursor": cursor,
                }
            else:
                page = ServerCommands(context(request)).list_sessions(
                    cursor=request.query_params.get("cursor"),
                    limit=limit,
                    include_execution=(
                        request.query_params.get("include_execution", "false") == "true"
                    ),
                )
            for item in page["sessions"]:
                item["metadata"] = context(request).api.get_session_metadata(item["session_id"])
            return page

        return JSONResponse(await host.execute_query(query))

    async def session(request):
        def query():
            manager = scoped(request)
            session_id = request.path_params["session_id"]
            result = ServerCommands(context(request)).get_session(session_id)
            result["driving"] = session_id in manager.drivers
            result["session"]["metadata"] = manager.api.get_session_metadata(session_id)
            return result

        return JSONResponse(await host.execute_query(query))

    async def interactions(request):
        sid = request.path_params["session_id"]
        if request.method == "POST":
            body = await _parse_body(request, InteractionRequest)
            value = await host.execute_command(
                lambda: scoped(request).interactions.submit(sid, body)
            )
            return JSONResponse({"receipt": value}, status_code=202)
        value = await host.execute_query(
            lambda: scoped(request).interactions.receipt(
                sid, request.path_params["client_message_id"]
            )
        )
        return JSONResponse({"receipt": value})

    async def queue(request):
        value = await host.execute_query(
            lambda: scoped(request).interactions.queue(request.path_params["session_id"])
        )
        return JSONResponse(value)

    async def mutation(request):
        is_withdraw = request.url.path.endswith("/withdraw")
        body = await _parse_body(request, WithdrawRequest if is_withdraw else ChatControlRequest)
        sid = request.path_params["session_id"]
        key = request.path_params.get("client_message_id")

        def apply():
            service = scoped(request).interactions
            commands = ServerCommands(context(request))

            def execute():
                result = (
                    service.withdraw(sid, key, body.expected_revision)
                    if is_withdraw
                    else service.control(sid, body)
                )
                return result, sid

            value, receipt = context(request).journal.transact(
                lambda _: commands._idempotent(
                    "chat_withdraw" if is_withdraw else "chat_control",
                    body.command_id,
                    {"session_id": sid, "client_message_id": key, **body.model_dump(mode="json")},
                    execute,
                    lambda _: (
                        service.receipt(sid, key, replay=True)
                        if is_withdraw
                        else {"disposition": "replay", **service.queue(sid)}
                    ),
                    result_kind="session",
                )
            )
            return {"result": value, "command_disposition": receipt.disposition.value}

        return JSONResponse(await host.execute_command(apply))

    async def pause_turn(request):
        """Durable chat-turn pause (lane A seams, coordinator wiring).

        Accepts the frozen GuiPauseRequest durably first (replay returns the
        stored cycle, a reused id with a different payload conflicts), then
        wakes a running chat drive through ChatTurnPauseControl.interrupt.
        """
        sid = request.path_params["session_id"]
        body = await _parse_body(request, ChatPauseRequest)
        if body.session_id != sid:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Session mismatch")

        def command():
            chat = scoped(request)
            point = chat.pause_service.accept_chat_pause(sid, command_id=body.command_id)
            # The acceptance has committed inside this command; the wake signal
            # must reach any in-flight model wait only after that is true.
            chat.turn_pause_control.interrupt(
                sid,
                TurnPauseSignal(
                    control_generation=point.fact.control_generation,
                    command_id=point.fact.command_id,
                    reason=point.fact.reason,
                ),
            )
            return {
                "lifecycle": point.fact.lifecycle,
                "control_generation": point.fact.control_generation,
                "command_id": point.fact.command_id,
                "cancelled": point.cancel_command_id is not None,
            }

        return JSONResponse(await host.execute_command(command))

    async def timeline(request):
        try:
            limit = int(request.query_params.get("limit", "50"))
        except ValueError:
            raise ApplicationError(ApplicationErrorCode.INVALID, "Invalid history limit") from None
        after_position = request.query_params.get("after_position")
        if after_position is not None:
            try:
                after_value = int(after_position)
            except ValueError:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID, "Invalid replay position"
                ) from None
        else:
            after_value = None

        def query():
            if after_value is not None:
                # Durable replay window after a snapshot high-water (P08.2).
                return scoped(request).timeline.index.snapshot_page(
                    request.path_params["session_id"],
                    limit=limit,
                    after_position=after_value,
                )
            return scoped(request).timeline.page(
                request.path_params["session_id"],
                before=request.query_params.get("before"),
                limit=limit,
            )

        return JSONResponse(await host.execute_query(query))

    async def activities(request):
        """Read-only durable tool-activity recovery (master plan P2.4)."""
        try:
            limit = int(request.query_params.get("limit", "128"))
        except ValueError:
            raise ApplicationError(ApplicationErrorCode.INVALID, "Invalid activity limit") from None
        value = await host.execute_query(
            lambda: scoped(request).timeline.tool_activities(
                request.path_params["session_id"], limit=limit
            )
        )
        return JSONResponse(value)

    async def content(request):
        def query():
            manager = scoped(request).timeline
            sid = request.path_params["session_id"]
            record_id = request.path_params["record_id"]
            # View-scoped resolver: fork lineage first, workflow-leaf scope
            # second. Reading never grants forking — the fork endpoint keeps
            # the strict ancestry precheck.
            return manager.index.read_record_content_authorized(sid, record_id)

        value = await host.execute_query(query)
        return JSONResponse(value)

    async def artifact_content(request):
        async def query():
            manager = scoped(request)
            sid = request.path_params["session_id"]
            manager.require_session(sid)
            artifact_id = request.path_params["artifact_id"]
            artifact = manager.api.artifacts.get(artifact_id)
            if artifact is None or artifact.session_id != sid:
                raise ApplicationError(
                    ApplicationErrorCode.NOT_FOUND, "Artifact is outside this Session"
                )
            from morrow.core.artifacts import ArtifactError

            try:
                content = (
                    await manager.api.artifacts.read_async(artifact_id, max_bytes=65536)
                ).content
            except ArtifactError:
                raise ApplicationError(
                    ApplicationErrorCode.UNAVAILABLE, "Artifact content is unavailable"
                ) from None
            if request.query_params.get("raw") == "1":
                # Binary consumers (image previews) read the original bytes;
                # the default JSON shape stays the bounded text preview.
                return Response(content, media_type=_sniff_media_type(content))
            # Published text artifacts already passed the Artifact authority's
            # sensitivity checks. Only bounded, inert text enters the GUI.
            return {
                "content": content.decode("utf-8", errors="replace"),
                "truncated": artifact.byte_size > len(content),
                "byte_size": artifact.byte_size,
            }

        value = await host.execute_preparation(query)
        if isinstance(value, Response):
            return value
        return JSONResponse(value)

    async def activity_content(request):
        """Bounded, authorized read of one activity's durable safe content (P07)."""

        def query():
            manager = scoped(request)
            sid = request.path_params["session_id"]
            activity_id = request.path_params["activity_id"]
            result = manager.timeline.index.read_activity_content_authorized(
                sid, activity_id, max_bytes=16384
            )
            return {
                "content": result["body"],
                "truncated": result["truncated"],
                "ref": result["ref"].model_dump(mode="json"),
            }

        return JSONResponse(await host.execute_query(query))

    async def snapshot(request):
        activity_schema = request.query_params.get("activity_schema")
        activity_mode = False
        if activity_schema is not None:
            if not ACTIVITY_STREAM_ENABLED or activity_schema != str(ACTIVITY_SCHEMA_VERSION):
                raise ApplicationError(ApplicationErrorCode.INVALID, "activity mode unavailable")
            activity_mode = True
        value = await host.execute_query(
            lambda: scoped(request).streams.snapshot(
                request.path_params["session_id"], activity_mode=activity_mode
            )
        )
        return JSONResponse(value)

    async def stream(websocket):
        sid = websocket.path_params["session_id"]
        token = None
        try:
            await host.execute_query(lambda: scoped(websocket).require_session(sid))
            await websocket.accept()
            message = await websocket.receive()
            raw = message.get("text", "")
            if len(raw.encode()) > 8192:
                await websocket.close(4400)
                return
            body = json.loads(raw)
            allowed = {"type", "stream_epoch", "after_sequence"}
            if "activity_schema" in body:
                # Explicit activity-mode selection (master plan P1.4); v1 clients
                # keep the exact old shape and the v1 delivery path.
                allowed = allowed | {"activity_schema"}
            if (
                not isinstance(body, dict)
                or set(body) != allowed
                or body["type"] != "subscribe"
                or not isinstance(body["stream_epoch"], str)
                or type(body["after_sequence"]) is not int
                or body["after_sequence"] < 0
            ):
                await websocket.close(4400)
                return
            activity_schema = body.get("activity_schema")
            if activity_schema is not None and (
                not ACTIVITY_STREAM_ENABLED
                or type(activity_schema) is not int
                or activity_schema != ACTIVITY_SCHEMA_VERSION
            ):
                # Unadvertised or unknown mode: the client falls back to v1.
                await websocket.close(4400)
                return
            epoch, after = body["stream_epoch"], body["after_sequence"]
            activity_mode = activity_schema is not None
            loop = asyncio.get_running_loop()
            token, hints = await host.execute_query(
                lambda: scoped(websocket).streams.subscribe(sid, loop)
            )
            while True:
                if activity_mode:
                    frames = await host.execute_query(
                        lambda after=after: scoped(websocket).streams.pull_activities(
                            sid, epoch, after
                        )
                    )
                else:
                    frames = await host.execute_query(
                        lambda after=after: scoped(websocket).streams.pull(sid, epoch, after)
                    )
                for frame in frames:
                    await websocket.send_json(frame)
                    if frame["type"] == "resync_required":
                        await websocket.close(4409)
                        return
                    after = frame["sequence"]
                if frames:
                    continue
                receiver = asyncio.create_task(websocket.receive())
                notification = asyncio.create_task(hints.get())
                try:
                    done, _ = await asyncio.wait(
                        {receiver, notification}, timeout=30, return_when=asyncio.FIRST_COMPLETED
                    )
                    if receiver in done:
                        return
                    if not done:
                        await websocket.send_json(
                            {"type": "heartbeat", "stream_epoch": epoch, "sequence": after}
                        )
                finally:
                    for task in (receiver, notification):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(receiver, notification, return_exceptions=True)
        except (ApplicationError, ValueError, TypeError):
            await websocket.close(4400)
        finally:
            if token is not None:
                await host.execute_query(lambda: scoped(websocket).streams.unsubscribe(sid, token))

    async def workflow_interactions(request):
        cursor = request.query_params.get("before")
        try:
            before = int(cursor) if cursor else None
            if before is not None and before < 1:
                raise ValueError
        except ValueError:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Invalid workflow cursor"
            ) from None
        return JSONResponse(
            await host.execute_query(
                lambda: scoped(request).workflows.page(
                    request.path_params["session_id"], before=before
                )
            )
        )

    async def capabilities(request):
        from morrow.application.chat_runtime import MAX_RUNTIME_HISTORY_BYTES, MAX_SESSION_RUNTIMES
        from morrow.server.replies import (
            DELIVERY_BYTES,
            DRAFT_BYTES,
            HOST_SUBSCRIBERS,
            RING_BYTES,
            RING_FRAMES,
            SESSION_SUBSCRIBERS,
            STREAM_CACHE,
        )

        def query():
            config = context(request).application.global_store.load().value
            return {
                "protocol_version": 1,
                "interaction_protocol_version": 1,
                "task_workflow": TASK_WORKFLOW_CAPABILITY,
                # Advertised only after the production activity flow exists;
                # missing key means the client stays on v1 stage/reply frames.
                **(
                    {"activity_stream": {"schema": ACTIVITY_SCHEMA_VERSION}}
                    if ACTIVITY_STREAM_ENABLED
                    else {}
                ),
                "workspace_id": context(request).workspace_id,
                "features": {
                    "chat": {"available": True},
                    "compact": {"available": True},
                    "workspace_management": {"available": True},
                    "session_management": {"available": True},
                    "attachments": {"available": True},
                    "generation_settings": {"available": True},
                    "explicit_workflow": {"available": True},
                    "recovery_status": {"available": True},
                    "agent_presets": {"available": True},
                    "agent_quick_save": {"available": True},
                },
                "execution_ready": bool(config and config.active_model),
                "effective_settings": {
                    "model": (
                        f"{config.active_model.provider_id}/{config.active_model.model_id}"
                        if config and config.active_model
                        else None
                    ),
                    "permission": " / ".join(
                        value.value
                        for value in (
                            context(request).chat.permission_profile.access_scope,
                            context(request).chat.permission_profile.approval_mode,
                            context(request).chat.permission_profile.process_isolation,
                        )
                    ),
                },
                "attachment_limits": attachment_limits(),
                "limits": {
                    "text_chars": 4096,
                    "session_pending": 32,
                    "workspace_pending": 128,
                    "history_items": 100,
                    "history_bytes": 1048576,
                    "record_bytes": 262144,
                    "ring_bytes": RING_BYTES,
                    "ring_frames": RING_FRAMES,
                    "draft_bytes": DRAFT_BYTES,
                    "stream_cache": STREAM_CACHE,
                    "host_subscribers": HOST_SUBSCRIBERS,
                    "session_subscribers": SESSION_SUBSCRIBERS,
                    "delivery_bytes": DELIVERY_BYTES,
                    "session_runtimes": MAX_SESSION_RUNTIMES,
                    "workspace_runtimes": 4,
                    "runtime_history_bytes": MAX_RUNTIME_HISTORY_BYTES,
                },
            }

        return JSONResponse(await host.execute_query(query))

    async def recovery(request):
        sid = request.path_params["session_id"]
        if request.method == "GET":
            value = await host.execute_query(
                lambda: scoped(request).interactions.recovery_reports(sid)
            )
        else:
            body = await _parse_body(request, ChatRecoveryRequest)
            if body.action == "resolve" and (body.report_id is None or body.resolution is None):
                raise ApplicationError(
                    ApplicationErrorCode.INVALID, "Recovery report and resolution are required"
                )
            value = await host.execute_command(
                lambda: scoped(request).interactions.recover(sid, body)
            )
        return JSONResponse(value)

    async def recovery_status(request):
        """Read-only recovery capability projection.

        Unlike discovery-only checks this endpoint never restores a
        runtime or creates a report. It is safe for a reconnecting client to
        call once when the durable execution projection asks for review.
        """

        sid = request.path_params["session_id"]
        value = await host.execute_query(lambda: scoped(request).recovery_status.build(sid))
        return JSONResponse(value.model_dump(mode="json"))

    async def workflow_recovery(request):
        """Resolve a Workflow-owned report through the Workflow owner.

        Item decisions only update the report. A report-level ``resume`` is
        the one action that additionally asks the Workflow scheduler to drive
        the run; the Chat recovery driver is never used for a Workflow leaf.
        """

        sid = request.path_params["session_id"]
        body = await _parse_body(request, WorkflowRecoveryRequest)

        def apply():
            c = context(request)
            manager = c.chat
            manager.require_session(sid)
            run_id = request.path_params["workflow_run_id"]
            run = c.journal.workflows.get_run(c.workspace_id, run_id)
            if run is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Workflow run is missing")
            owned_sessions: set[str] = set()
            root_task = c.journal.get_task_run(c.workspace_id, run.root_task_run_id)
            if root_task is not None:
                owned_sessions.add(root_task.session_id)
            for node in c.journal.workflows.list_nodes(c.workspace_id, run_id):
                if node.conversation_session_id:
                    owned_sessions.add(node.conversation_session_id)
            if sid not in owned_sessions:
                raise ApplicationError(
                    ApplicationErrorCode.NOT_FOUND, "Workflow run is outside this Session"
                )

            if body.report_id is not None:
                report = c.api.get_recovery(body.report_id)
            else:
                report = next(
                    (
                        candidate
                        for candidate in (
                            c.journal.get_open_report(c.workspace_id, session_id)
                            for session_id in owned_sessions
                        )
                        if candidate is not None
                    ),
                    None,
                )
            if report is None or report.session_id not in owned_sessions:
                raise ApplicationError(
                    ApplicationErrorCode.NOT_FOUND, "Recovery report is outside this Workflow"
                )
            prepared = c.api._prepare(
                "recovery_resolve",
                {
                    "report_id": report.report_id,
                    "resolution": body.resolution,
                    "item_id": body.item_id,
                },
                body.command_id,
            )
            if prepared[2] is not None:
                replay = c.api.get_recovery(prepared[2].result_id or "")
                if replay is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY, "recovery result is missing"
                    )
                return {
                    "report": replay.model_dump(mode="json"),
                    "disposition": "replay",
                    "driving": False,
                }
            if manager.recovery_lock.locked() or any(
                candidate in manager.drivers for candidate in owned_sessions
            ):
                raise ApplicationError(
                    ApplicationErrorCode.BUSY, "Wait for Workflow execution to finish"
                )

            resolution = RecoveryResolution(body.resolution)
            products = manager.runtimes.get(report.session_id)
            if products is not None:
                result = products.api.resolve_recovery(
                    report,
                    command_id=body.command_id,
                    resolution=resolution,
                    item_id=body.item_id,
                    log=products.session.log,
                    writer=products.persistence.writer,
                )
            else:
                result = c.api.resolve_recovery_by_id(
                    report.report_id,
                    command_id=body.command_id,
                    resolution=resolution,
                    item_id=body.item_id,
                )
            manager.interactions.records.pause(report.session_id)
            manager.streams.changed(report.session_id)
            driving = False
            if (
                resolution is RecoveryResolution.RESUME
                and result.receipt.disposition.value != "replay"
                and not run.status.terminal
            ):
                driving = c.supervisor.ensure_driver(
                    run_id,
                    lambda: c.runtime.scheduler.recover(run_id, cancelled_is_user=False),
                )
            return {
                "report": result.value.model_dump(mode="json"),
                "disposition": result.receipt.disposition.value,
                "driving": driving,
            }

        return JSONResponse(await host.execute_command(apply))

    async def commands(request):
        body = await _parse_body(request, ChatCommandRequest)
        sid = request.path_params["session_id"]

        async def execute():
            manager = scoped(request)
            manager.require_session(sid)
            manager.interactions.check_admission()
            api = manager.api
            operation = "chat_" + body.action
            cid, digest, receipt = api._prepare(
                operation,
                {
                    "session_id": sid,
                    **({"instructions": body.instructions} if body.instructions else {}),
                },
                body.command_id,
            )
            if receipt is not None:
                return {"message": "此命令已完成（原请求回执）。"}
            if sid in manager.drivers or manager.execution_lock.locked():
                raise ApplicationError(
                    ApplicationErrorCode.BUSY, "Wait for workspace execution to finish"
                )
            result = {}

            async def compact():
                products = manager.runtime(sid)
                dispatched = await products.orchestrator.dispatch(
                    "/compact" + (" " + body.instructions if body.instructions else "")
                )
                result["message"] = "\n".join(dispatched.lines)
                api.journal.transact(
                    lambda txn: api._receipt(
                        txn,
                        command_id=cid,
                        operation=operation,
                        digest=digest,
                        session_id=sid,
                        result_kind="session",
                        result_id=sid,
                        event_cursor=None,
                    )
                )
                manager.streams.changed(sid)

            manager.ensure_driver(sid, compact)
            # The long Provider call runs outside the mutation consumer. Cancelling
            # the HTTP waiter cannot cancel the Session-owned driver.
            driver = manager.drivers[sid]
            driver.add_done_callback(
                lambda done: done.exception() if not done.cancelled() else None
            )
            await asyncio.shield(driver)
            return result

        if body.action == "task":
            # Short synchronous mutation stays on the command bus.
            def new_task():
                manager = scoped(request)
                manager.require_session(sid)
                if sid in manager.drivers:
                    raise ApplicationError(ApplicationErrorCode.BUSY, "Session is active")
                manager.api.task_new(sid, command_id=body.command_id)
                manager.streams.changed(sid)
                return {"message": "已创建新任务。"}

            return JSONResponse(await host.execute_command(new_task))
        return JSONResponse(await host.execute_preparation(execute))

    from morrow.server.planning import planning_routes

    root = "/v1/workspaces/{workspace_id}/sessions"
    return [
        *planning_routes(host, _parse_body, context),
        Route(
            "/v1/workspaces/{workspace_id}/sessions/{session_id}/workflow-interactions",
            workflow_interactions,
        ),
        Route("/v1/capabilities", capabilities),
        Route("/v1/workspaces/{workspace_id}/capabilities", capabilities),
        Route(root, sessions, methods=["GET", "POST"]),
        Route(root + "/{session_id}", session),
        Route(root + "/{session_id}/commands", commands, methods=["POST"]),
        Route(root + "/{session_id}/recovery/status", recovery_status),
        Route(root + "/{session_id}/recovery", recovery, methods=["GET", "POST"]),
        Route(
            root + "/{session_id}/workflow-recovery/{workflow_run_id}",
            workflow_recovery,
            methods=["POST"],
        ),
        Route(root + "/{session_id}/snapshot", snapshot),
        Route(root + "/{session_id}/artifacts/{artifact_id}/content", artifact_content),
        WebSocketRoute(root + "/{session_id}/stream", stream),
        Route(root + "/{session_id}/timeline", timeline),
        Route(root + "/{session_id}/activities", activities),
        Route(root + "/{session_id}/activity-content/{activity_id}", activity_content),
        Route(root + "/{session_id}/content/{record_id}", content),
        Route(root + "/{session_id}/queue", queue),
        Route(root + "/{session_id}/control", mutation, methods=["POST"]),
        Route(root + "/{session_id}/pause", pause_turn, methods=["POST"]),
        Route(
            root + "/{session_id}/interactions/{client_message_id}/withdraw",
            mutation,
            methods=["POST"],
        ),
        Route(root + "/{session_id}/interactions", interactions, methods=["POST"]),
        Route(root + "/{session_id}/interactions/{client_message_id}", interactions),
    ]


class WithdrawRequest(ChatSessionCreateRequest):
    expected_revision: int = Field(ge=1)
    session_id: None = None


class ChatControlRequest(WithdrawRequest):
    action: Literal["stop", "continue_queue"]
    target_agent_run_id: str | None = Field(default=None, max_length=128)
    withdraw_pending: bool = False


class ChatPauseRequest(ChatSessionCreateRequest):
    """Frozen GuiPauseRequest body (contract 1.0.0 §7): one chat-turn pause.

    ``expected_run_row_version`` is part of the frozen GUI shape, but a plain
    chat turn owns no workflow run row; the durable replay/conflict contract
    lives on the pause cycle itself, so the value stays unused here.
    """

    session_id: str = Field(min_length=1, max_length=128)
    expected_run_row_version: int | None = Field(default=None, ge=1)


class ChatRecoveryRequest(ChatSessionCreateRequest):
    session_id: None = None
    action: Literal["discover", "resolve", "resume"]
    target_agent_run_id: str | None = Field(default=None, max_length=128)
    report_id: str | None = Field(default=None, max_length=128)
    resolution: Literal["acknowledge", "retry", "abort", "quarantine", "resume"] | None = None
    item_id: str | None = Field(default=None, max_length=128)


class WorkflowRecoveryRequest(ChatSessionCreateRequest):
    session_id: None = None
    report_id: str | None = Field(default=None, max_length=128)
    resolution: Literal["acknowledge", "abort", "quarantine", "resume"]
    item_id: str | None = Field(default=None, max_length=128)


class ChatCommandRequest(ChatSessionCreateRequest):
    session_id: None = None
    action: Literal["task", "compact"]
    instructions: str = Field(default="", max_length=512)
