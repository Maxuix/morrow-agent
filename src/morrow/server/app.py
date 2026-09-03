"""ASGI transport for the local Core API.

The transport holds no business state: every handler parses a strict wire
model, delegates to the Core Host (commands through the bounded bus, queries
through the read path) and renders the projection. Security lives at this
edge — loopback session-token auth, Origin/Referer allowlisting, JSON-only
mutations — so a malicious webpage cannot ride ambient browser authority.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

from morrow.core.application import ApplicationError, ApplicationErrorCode

from .commands import ServerCommands
from .host import CommandBackpressureError, CoreHost, CoreHostUnavailableError
from .protocol import (
    API_PREFIX,
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

MAX_BODY_BYTES = 1024 * 1024
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

logger = logging.getLogger("morrow.server")

_ERROR_STATUS = {
    ApplicationErrorCode.INVALID: 400,
    ApplicationErrorCode.NOT_FOUND: 404,
    ApplicationErrorCode.CONFLICT: 409,
    ApplicationErrorCode.STALE: 409,
    ApplicationErrorCode.CROSS_WORKSPACE: 403,
    ApplicationErrorCode.UNAVAILABLE: 503,
    ApplicationErrorCode.BUSY: 503,
    ApplicationErrorCode.NEEDS_RECOVERY: 409,
    ApplicationErrorCode.QUARANTINED: 409,
    ApplicationErrorCode.READ_ONLY: 403,
}


def _error_body(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


class LocalApiSecurityMiddleware:
    """Session-token auth plus browser-originated request rejection."""

    def __init__(self, app, *, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if not scope["path"].startswith(API_PREFIX + "/"):
            if scope["type"] == "websocket":
                await self._close_ws(scope, receive, send, 1008)
            else:
                await self._respond(send, 404, _error_body("not_found", "unknown path"))
            return
        headers = {key.lower(): value for key, value in scope["headers"]}
        presented = self._presented_token(scope, headers)
        if presented is None or not hmac.compare_digest(presented, self.token):
            if scope["type"] == "websocket":
                await self._close_ws(scope, receive, send, 4401)
            else:
                await self._respond(send, 401, _error_body("unauthorized", "invalid session token"))
            return
        if not self._origin_allowed(headers):
            if scope["type"] == "websocket":
                await self._close_ws(scope, receive, send, 4403)
            else:
                await self._respond(
                    send, 403, _error_body("forbidden", "cross-origin requests are rejected")
                )
            return
        if scope["type"] == "http" and scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            content_type = headers.get(b"content-type", b"").decode("latin-1")
            if not content_type.startswith("application/json"):
                await self._respond(
                    send,
                    415,
                    _error_body("unsupported_media_type", "mutations require application/json"),
                )
                return
        await self.app(scope, receive, send)

    def _presented_token(self, scope, headers) -> str | None:
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        if authorization.startswith("Bearer "):
            return authorization.removeprefix("Bearer ").strip()
        if scope["type"] == "websocket":
            # Browser WebSocket clients cannot set headers; the token travels in
            # the query string on this endpoint only.
            params = parse_qs(scope["query_string"].decode("latin-1"))
            values = params.get("token")
            if values:
                return values[0]
        return None

    @staticmethod
    def _origin_allowed(headers) -> bool:
        for name in (b"origin", b"referer"):
            value = headers.get(name)
            if not value:
                continue
            hostname = urlsplit(value.decode("latin-1")).hostname
            if hostname is None or hostname.lower() not in LOOPBACK_HOSTS:
                return False
        return True

    @staticmethod
    async def _respond(send, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": payload})

    @staticmethod
    async def _close_ws(scope, receive, send, code: int) -> None:
        message = await receive()
        if message["type"] == "websocket.connect":
            await send({"type": "websocket.close", "code": code})


def create_asgi_app(
    host: CoreHost,
    *,
    auth_token: str,
    ws_ping_seconds: float = 30.0,
) -> LocalApiSecurityMiddleware:
    """Build the loopback API app around a running Core Host."""

    commands = ServerCommands(host.context)

    async def _command(request: Request, model, handler) -> Response:
        body = await _parse_body(request, model)
        outcome = await host.execute_command(lambda: handler(body))
        # Every committed mutation hints subscribers with the latest cursor;
        # the hint carries no facts, clients pull from the durable stream.
        await host.execute_query(lambda: host.context.hub.publish(commands.meta()["latest_cursor"]))
        return JSONResponse(outcome.wire())

    async def _query(handler) -> Response:
        return JSONResponse(await host.execute_query(handler))

    async def _parse_body(request: Request, model):
        content_length = request.headers.get("content-length")
        if content_length is not None and int(content_length) > MAX_BODY_BYTES:
            raise ApplicationError(ApplicationErrorCode.INVALID, "request body is too large")
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            raise ApplicationError(ApplicationErrorCode.INVALID, "request body is too large")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "request body must be JSON"
            ) from None
        if not isinstance(payload, dict):
            raise ApplicationError(ApplicationErrorCode.INVALID, "request body must be an object")
        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            location = ".".join(str(part) for part in first.get("loc", ()))
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                f"request is invalid: {location or 'body'} {first.get('msg', '')}".strip(),
            ) from None

    def _page_params(request: Request) -> tuple[int, str | None]:
        try:
            limit = int(request.query_params.get("limit", "50"))
        except ValueError:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "limit must be an integer"
            ) from None
        after = request.query_params.get("after")
        return limit, after

    def _event_params(request: Request) -> tuple[int, int]:
        try:
            after = int(request.query_params.get("after", "0"))
            limit = int(request.query_params.get("limit", "100"))
        except ValueError:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "event cursor and limit must be integers"
            ) from None
        return after, limit

    # Meta / event stream ------------------------------------------------------

    async def meta(request: Request) -> Response:
        return await _query(lambda: commands.meta())

    async def snapshot(request: Request) -> Response:
        return await _query(lambda: commands.snapshot())

    async def events(request: Request) -> Response:
        after, limit = _event_params(request)
        return await _query(lambda: commands.events_page(after=after, limit=limit))

    async def events_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        loop = asyncio.get_running_loop()
        token, queue = host.context.hub.subscribe(loop)
        disconnected = asyncio.Event()

        async def watch_disconnect() -> None:
            try:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
            except Exception:
                return
            finally:
                disconnected.set()

        watcher = asyncio.ensure_future(watch_disconnect())
        try:
            latest = await host.execute_query(lambda: commands.meta()["latest_cursor"])
            await websocket.send_json({"type": "hello", "latest_cursor": latest})
            while not disconnected.is_set():
                hint = asyncio.ensure_future(queue.get())
                gone = asyncio.ensure_future(disconnected.wait())
                done, pending = await asyncio.wait(
                    {hint, gone},
                    timeout=ws_ping_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                if gone in done or disconnected.is_set():
                    break
                if hint in done:
                    cursor = hint.result()
                else:
                    latest = await host.execute_query(lambda: commands.meta()["latest_cursor"])
                    cursor = latest
                    await websocket.send_json({"type": "ping", "latest_cursor": cursor})
                    continue
                await websocket.send_json({"type": "cursor", "latest_cursor": cursor})
        except Exception:
            # A closed client raises on send; either way the projection is safe
            # to drop because the durable stream remains the pull source.
            return
        finally:
            watcher.cancel()
            host.context.hub.unsubscribe(token)

    # Sessions / tasks ---------------------------------------------------------

    async def create_session(request: Request) -> Response:
        return await _command(
            request, SessionCreateRequest, lambda body: commands.session_create(body)
        )

    async def list_sessions(request: Request) -> Response:
        limit, cursor = _page_params(request)
        return await _query(lambda: commands.list_sessions(cursor=cursor, limit=limit))

    async def get_session(request: Request) -> Response:
        return await _query(lambda: commands.get_session(request.path_params["session_id"]))

    async def list_session_tasks(request: Request) -> Response:
        limit, cursor = _page_params(request)
        session_id = request.path_params["session_id"]
        return await _query(lambda: commands.list_tasks(session_id, cursor=cursor, limit=limit))

    async def create_task(request: Request) -> Response:
        return await _command(request, TaskCreateRequest, lambda body: commands.task_create(body))

    async def get_task(request: Request) -> Response:
        return await _query(lambda: commands.get_task(request.path_params["task_run_id"]))

    async def accept_task(request: Request) -> Response:
        task_run_id = request.path_params["task_run_id"]
        return await _command(
            request,
            TaskTransitionRequest,
            lambda body: commands.task_accept(task_run_id, body),
        )

    async def cancel_task(request: Request) -> Response:
        task_run_id = request.path_params["task_run_id"]
        return await _command(
            request,
            TaskTransitionRequest,
            lambda body: commands.task_cancel(task_run_id, body),
        )

    async def resume_task(request: Request) -> Response:
        task_run_id = request.path_params["task_run_id"]
        return await _command(
            request,
            TaskTransitionRequest,
            lambda body: commands.task_resume(task_run_id, body),
        )

    # Workflow runs --------------------------------------------------------------

    async def list_runs(request: Request) -> Response:
        limit, after = _page_params(request)
        return await _query(lambda: commands.list_runs(limit=limit, after=after))

    async def start_run(request: Request) -> Response:
        return await _command(
            request, WorkflowStartRequest, lambda body: commands.workflow_start(body)
        )

    async def get_run(request: Request) -> Response:
        return await _query(lambda: commands.get_run_view(request.path_params["run_id"]))

    async def get_node(request: Request) -> Response:
        return await _query(
            lambda: commands.get_node_view(
                request.path_params["run_id"], request.path_params["node_run_id"]
            )
        )

    async def pause_run(request: Request) -> Response:
        run_id = request.path_params["run_id"]
        return await _command(
            request, WorkflowControlRequest, lambda body: commands.workflow_pause(run_id, body)
        )

    async def resume_run(request: Request) -> Response:
        run_id = request.path_params["run_id"]
        return await _command(
            request, WorkflowResumeRequest, lambda body: commands.workflow_resume(run_id, body)
        )

    async def cancel_run(request: Request) -> Response:
        run_id = request.path_params["run_id"]

        async def invoke(body):
            return await commands.workflow_cancel(run_id, body)

        return await _command(request, WorkflowControlRequest, invoke)

    async def rerun_run(request: Request) -> Response:
        run_id = request.path_params["run_id"]
        return await _command(
            request, WorkflowRerunRequest, lambda body: commands.workflow_rerun(run_id, body)
        )

    async def abandon_run(request: Request) -> Response:
        run_id = request.path_params["run_id"]
        return await _command(
            request, WorkflowAbandonRequest, lambda body: commands.workflow_abandon(run_id, body)
        )

    # Patches --------------------------------------------------------------------

    async def patch_validate(request: Request) -> Response:
        return await _command(
            request, PatchCommandRequest, lambda body: _PatchResult(commands.patch_validate(body))
        )

    async def patch_save(request: Request) -> Response:
        return await _command(request, PatchCommandRequest, lambda body: commands.patch_save(body))

    async def patch_apply(request: Request) -> Response:
        return await _command(request, PatchCommandRequest, lambda body: commands.patch_apply(body))

    # Approvals ------------------------------------------------------------------

    async def list_approvals(request: Request) -> Response:
        pending_only = request.query_params.get("pending", "true").lower() != "false"
        return await _query(lambda: commands.list_approvals(pending_only=pending_only))

    async def resolve_approval(request: Request) -> Response:
        approval_id = request.path_params["approval_id"]
        return await _command(
            request,
            ApprovalResolveRequest,
            lambda body: commands.approval_resolve(approval_id, body),
        )

    # Artifacts / agent runs -------------------------------------------------------

    async def list_artifacts(request: Request) -> Response:
        limit, cursor = _page_params(request)
        return await _query(
            lambda: commands.list_artifacts(
                session_id=request.query_params.get("session_id"),
                task_run_id=request.query_params.get("task_run_id"),
                cursor=cursor,
                limit=limit,
            )
        )

    async def get_artifact(request: Request) -> Response:
        return await _query(lambda: commands.get_artifact(request.path_params["artifact_id"]))

    async def get_agent_run(request: Request) -> Response:
        return await _query(lambda: commands.get_agent_run(request.path_params["agent_run_id"]))

    # Catalog ----------------------------------------------------------------------

    async def catalog_agent_definitions(request: Request) -> Response:
        limit, after = _page_params(request)
        return await _query(lambda: commands.catalog_agent_definitions(limit=limit, after=after))

    async def catalog_agent_definition(request: Request) -> Response:
        return await _query(
            lambda: commands.catalog_agent_definition(request.path_params["definition_id"])
        )

    async def catalog_workflow_definitions(request: Request) -> Response:
        limit, after = _page_params(request)
        return await _query(lambda: commands.catalog_workflow_definitions(limit=limit, after=after))

    async def catalog_workflow_definition(request: Request) -> Response:
        return await _query(
            lambda: commands.catalog_workflow_definition(request.path_params["definition_id"])
        )

    async def catalog_workflow_revisions(request: Request) -> Response:
        limit, after = _page_params(request)
        return await _query(
            lambda: commands.catalog_workflow_revisions(
                definition_id=request.query_params.get("definition_id"), limit=limit, after=after
            )
        )

    async def catalog_providers(request: Request) -> Response:
        return await _query(lambda: commands.catalog_providers())

    async def catalog_skills(request: Request) -> Response:
        return await _query(lambda: commands.catalog_skills())

    async def catalog_tools(request: Request) -> Response:
        return await _query(lambda: commands.catalog_tools())

    async def catalog_artifact_contracts(request: Request) -> Response:
        return await _query(lambda: commands.catalog_artifact_contracts())

    async def _application_error(request: Request, exc: ApplicationError) -> Response:
        return JSONResponse(
            _error_body(exc.code.value, exc.message), status_code=_ERROR_STATUS[exc.code]
        )

    async def _backpressure(request: Request, exc: CommandBackpressureError) -> Response:
        return JSONResponse(
            _error_body("busy", str(exc)), status_code=503, headers={"Retry-After": "1"}
        )

    async def _unavailable(request: Request, exc: CoreHostUnavailableError) -> Response:
        return JSONResponse(_error_body("unavailable", str(exc)), status_code=503)

    async def _value_error(request: Request, exc: ValueError) -> Response:
        return JSONResponse(_error_body("invalid", "request is invalid"), status_code=400)

    async def _internal_error(request: Request, exc: Exception) -> Response:
        logger.warning("unhandled API error: %s", type(exc).__name__)
        return JSONResponse(_error_body("internal", "internal server error"), status_code=500)

    app = Starlette(
        routes=[
            Route(f"{API_PREFIX}/meta", meta),
            Route(f"{API_PREFIX}/snapshot", snapshot),
            Route(f"{API_PREFIX}/events", events),
            WebSocketRoute(f"{API_PREFIX}/events/stream", events_stream),
            Route(f"{API_PREFIX}/sessions", create_session, methods=["POST"]),
            Route(f"{API_PREFIX}/sessions", list_sessions),
            Route(f"{API_PREFIX}/sessions/{{session_id}}", get_session),
            Route(f"{API_PREFIX}/sessions/{{session_id}}/tasks", list_session_tasks),
            Route(f"{API_PREFIX}/tasks", create_task, methods=["POST"]),
            Route(f"{API_PREFIX}/tasks/{{task_run_id}}", get_task),
            Route(f"{API_PREFIX}/tasks/{{task_run_id}}/accept", accept_task, methods=["POST"]),
            Route(f"{API_PREFIX}/tasks/{{task_run_id}}/cancel", cancel_task, methods=["POST"]),
            Route(f"{API_PREFIX}/tasks/{{task_run_id}}/resume", resume_task, methods=["POST"]),
            Route(f"{API_PREFIX}/workflow-runs", list_runs),
            Route(f"{API_PREFIX}/workflow-runs", start_run, methods=["POST"]),
            Route(f"{API_PREFIX}/workflow-runs/{{run_id}}", get_run),
            Route(f"{API_PREFIX}/workflow-runs/{{run_id}}/nodes/{{node_run_id}}", get_node),
            Route(f"{API_PREFIX}/workflow-runs/{{run_id}}/pause", pause_run, methods=["POST"]),
            Route(f"{API_PREFIX}/workflow-runs/{{run_id}}/resume", resume_run, methods=["POST"]),
            Route(f"{API_PREFIX}/workflow-runs/{{run_id}}/cancel", cancel_run, methods=["POST"]),
            Route(f"{API_PREFIX}/workflow-runs/{{run_id}}/rerun", rerun_run, methods=["POST"]),
            Route(f"{API_PREFIX}/workflow-runs/{{run_id}}/abandon", abandon_run, methods=["POST"]),
            Route(f"{API_PREFIX}/patches/validate", patch_validate, methods=["POST"]),
            Route(f"{API_PREFIX}/patches/save", patch_save, methods=["POST"]),
            Route(f"{API_PREFIX}/patches/apply", patch_apply, methods=["POST"]),
            Route(f"{API_PREFIX}/approvals", list_approvals),
            Route(
                f"{API_PREFIX}/approvals/{{approval_id}}/resolve",
                resolve_approval,
                methods=["POST"],
            ),
            Route(f"{API_PREFIX}/artifacts", list_artifacts),
            Route(f"{API_PREFIX}/artifacts/{{artifact_id}}", get_artifact),
            Route(f"{API_PREFIX}/agent-runs/{{agent_run_id}}", get_agent_run),
            Route(f"{API_PREFIX}/catalog/agent-definitions", catalog_agent_definitions),
            Route(
                f"{API_PREFIX}/catalog/agent-definitions/{{definition_id}}",
                catalog_agent_definition,
            ),
            Route(f"{API_PREFIX}/catalog/workflow-definitions", catalog_workflow_definitions),
            Route(
                f"{API_PREFIX}/catalog/workflow-definitions/{{definition_id}}",
                catalog_workflow_definition,
            ),
            Route(f"{API_PREFIX}/catalog/workflow-revisions", catalog_workflow_revisions),
            Route(f"{API_PREFIX}/catalog/providers", catalog_providers),
            Route(f"{API_PREFIX}/catalog/skills", catalog_skills),
            Route(f"{API_PREFIX}/catalog/tools", catalog_tools),
            Route(f"{API_PREFIX}/catalog/artifact-contracts", catalog_artifact_contracts),
        ],
        exception_handlers={
            ApplicationError: _application_error,
            CommandBackpressureError: _backpressure,
            CoreHostUnavailableError: _unavailable,
            ValueError: _value_error,
            Exception: _internal_error,
        },
    )
    return LocalApiSecurityMiddleware(app, token=auth_token)


class _PatchResult:
    """Adapt a plain dict result into the command envelope."""

    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    def wire(self) -> dict[str, Any]:
        return {"result": self._result, "receipt": None}
