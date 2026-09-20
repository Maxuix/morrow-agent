"""ASGI transport for the local Core API.

The transport holds no business state: every handler parses a strict wire
model, delegates to the Core Host (commands through the bounded bus, queries
through the read path) and renders the projection. Security lives at this
edge — loopback host/origin allowlisting and JSON-only mutations; headless
``serve`` also uses session-token auth — so a malicious webpage cannot ride
ambient browser authority.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import weakref
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

from morrow.application.html_preview import PreviewRegistry
from morrow.application.management import COMMAND_MODELS
from morrow.application.workflows.compiler import WorkflowCompilationError
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.execution import StaleRowVersionError

from .commands import ServerCommands
from .host import CommandBackpressureError, CoreHost, CoreHostUnavailableError
from .preview_server import PreviewHttpServer
from .protocol import (
    API_PREFIX,
    AgentDefinitionPublishRequest,
    AgentDefinitionWriteRequest,
    ApprovalResolveRequest,
    GraphPlanRequest,
    OrchestrationPolicyRequest,
    PatchCommandRequest,
    ReplanDecisionRequest,
    SessionCreateRequest,
    TaskCreateRequest,
    TaskTransitionRequest,
    WorkflowAbandonRequest,
    WorkflowControlRequest,
    WorkflowDraftCreateRequest,
    WorkflowDraftRowRequest,
    WorkflowDraftUpdateRequest,
    WorkflowRerunRequest,
    WorkflowResumeRequest,
    WorkflowStartRequest,
)
from .static import make_gui_static_handler
from .workspace_file_routes import workspace_file_routes


# Applied to every HTTP response. The CSP assumes the prebuilt GUI bundle:
# self-hosted scripts/fonts only, no framing. CodeMirror requires dynamic
# inline style sheets (style-src 'unsafe-inline') for editor and gutter layouts.
# The HTML preview origin is added to frame-src only when that loopback listener
# actually runs, so the page can frame nothing else.
def _security_headers(preview_origin: str | None = None) -> tuple[tuple[bytes, bytes], ...]:
    frame_src = b"frame-src 'self' blob:"
    if preview_origin:
        frame_src = f"frame-src 'self' blob: {preview_origin}".encode()
    return (
        (
            b"content-security-policy",
            b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; "
            # blob: 仅用于面板自建的对象 URL：图片经 img-src，PDF 经 frame-src。
            b"img-src 'self' data: blob:; connect-src 'self' ws: wss:; object-src 'none'; "
            + frame_src
            + b"; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
        ),
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"no-referrer"),
    )


_SECURITY_HEADERS = _security_headers()


def _with_security_headers(send, preview_origin: str | None = None):
    headers_to_add = _security_headers(preview_origin)

    async def sender(message):
        if message["type"] == "http.response.start":
            headers = message.setdefault("headers", [])
            headers.extend(headers_to_add)
        await send(message)

    return sender


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
    """Loopback/origin checks with optional session-token authentication.

    Headless ``serve`` requires the session token on every API request. GUI
    mode intentionally does not: it is a local, same-origin application, so
    direct navigation to the bundle works without a token. GUI mutations and
    WebSockets still require a same-origin ``Origin`` header when no token is
    presented, while the loopback host/origin allowlist remains in force.
    """

    def __init__(
        self,
        app,
        *,
        token: str,
        gui_enabled: bool = False,
        listen_port=None,
        workspace_id=None,
        preview_origin=None,
    ) -> None:
        self.app = app
        self.token = token
        self.gui_enabled = gui_enabled
        self.listen_port = listen_port
        self.workspace_id = workspace_id
        self._preview_origin = preview_origin or (lambda: None)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope["headers"]}
        critical = {
            b"host",
            b"origin",
            b"referer",
            b"authorization",
            b"content-type",
            b"content-length",
            b"cookie",
        }
        duplicate = any(
            sum(key.lower() == name for key, _ in scope["headers"]) > 1 for name in critical
        )
        if duplicate or not self._host_allowed(headers, scope):
            if scope["type"] == "websocket":
                await self._close_ws(scope, receive, send, 4403)
            else:
                await self._respond(
                    send, 403, _error_body("forbidden", "invalid request authority")
                )
            return
        if not scope["path"].startswith(API_PREFIX + "/"):
            if scope["type"] == "websocket":
                await self._close_ws(scope, receive, send, 1008)
                return
            static_get = self.gui_enabled and scope["method"] in ("GET", "HEAD")
            if not static_get or not self._origin_allowed(headers, scope):
                await self._respond(send, 404, _error_body("not_found", "unknown path"))
                return
            await self.app(scope, receive, _with_security_headers(send, self._preview_origin()))
            return
        presented = self._presented_token(scope, headers)
        cookie_auth = presented is None and self._cookie_allowed(scope, headers)
        gui_without_token = self.gui_enabled and presented is None and not cookie_auth
        token_auth = presented is not None and hmac.compare_digest(presented, self.token)
        if not gui_without_token and not cookie_auth and not token_auth:
            if scope["type"] == "websocket":
                await self._close_ws(scope, receive, send, 4401)
            else:
                await self._respond(send, 401, _error_body("unauthorized", "invalid session token"))
            return
        if not self._origin_allowed(headers, scope) or (
            (cookie_auth or gui_without_token)
            and (scope["type"] == "websocket" or scope.get("method") not in {"GET", "HEAD"})
            and b"origin" not in headers
        ):
            if scope["type"] == "websocket":
                await self._close_ws(scope, receive, send, 4403)
            else:
                await self._respond(
                    send, 403, _error_body("forbidden", "cross-origin requests are rejected")
                )
            return
        upload = (
            scope["type"] == "http"
            and scope["method"] == "PUT"
            and re.fullmatch(
                r"/v1/workspaces/ws_[A-Za-z0-9_-]+/attachments/att_[A-Za-z0-9_-]+/content",
                scope["path"],
            )
        )
        if scope["type"] == "http" and scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            content_type = headers.get(b"content-type", b"").decode("latin-1")
            media_type = content_type.partition(";")[0].strip().casefold()
            from morrow.core.attachments import MEDIA_TYPES

            if (media_type not in MEDIA_TYPES) if upload else (media_type != "application/json"):
                await self._respond(
                    send,
                    415,
                    _error_body("unsupported_media_type", "mutations require application/json"),
                )
                return
        if scope["type"] == "http":
            if not upload and scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
                body = bytearray()
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > MAX_BODY_BYTES:
                        await self._respond(
                            send, 413, _error_body("too_large", "request body exceeds the limit")
                        )
                        return
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
                delivered = False

                async def bounded_receive():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {"type": "http.request", "body": bytes(body), "more_body": False}
                    return await receive()

                receive_body = bounded_receive
            else:
                receive_body = receive

            async def authenticated_send(message):
                if (
                    self.gui_enabled
                    and presented is not None
                    and message["type"] == "http.response.start"
                ):
                    name, value = self._cookie_value(scope, headers)
                    cookie = SimpleCookie()
                    cookie[name] = value
                    cookie[name]["path"] = "/v1"
                    cookie[name]["httponly"] = True
                    cookie[name]["samesite"] = "Strict"
                    if scope.get("scheme") == "https":
                        cookie[name]["secure"] = True
                    message.setdefault("headers", []).append(
                        (b"set-cookie", cookie.output(header="").strip().encode("ascii"))
                    )
                await send(message)

            await self.app(
                scope,
                receive_body,
                _with_security_headers(authenticated_send, self._preview_origin()),
            )
        else:
            await self.app(scope, receive, send)

    def _cookie_value(self, scope, headers):
        authority = headers.get(b"host", b"")
        port = self.listen_port or (scope.get("server") or ("", 80))[1]
        value = hmac.digest(self.token.encode(), b"gui-session:" + authority, "sha256").hex()
        return f"morrow_gui_{port}", value

    def _cookie_allowed(self, scope, headers):
        if not self.gui_enabled:
            return False
        name, expected = self._cookie_value(scope, headers)
        try:
            cookie = SimpleCookie(headers.get(b"cookie", b"").decode("ascii"))
            value = cookie[name].value if name in cookie else ""
            return value.isascii() and hmac.compare_digest(value, expected)
        except (ValueError, UnicodeError, CookieError):
            return False

    def _presented_token(self, scope, headers) -> str | None:
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        if authorization.startswith("Bearer "):
            value = authorization.removeprefix("Bearer ").strip()
            return value if value.isascii() else None
        if scope["type"] == "websocket":
            # Browser WebSocket clients cannot set headers; the token travels in
            # the query string on this endpoint only.
            params = parse_qs(scope["query_string"].decode("latin-1"))
            values = params.get("token")
            if values and len(values) == 1 and values[0].isascii():
                return values[0]
        return None

    @staticmethod
    def _authority(value, *, scheme="http", origin=False, referer=False):
        try:
            text = value.decode("ascii")
            if not text or any(ch.isspace() for ch in text) or "," in text or "\\" in text:
                return None
            parsed = urlsplit(text if origin or referer else scheme + "://" + text)
            if parsed.username is not None or parsed.password is not None:
                return None
            if parsed.scheme not in {"http", "https"} or parsed.hostname not in LOOPBACK_HOSTS:
                return None
            if parsed.fragment or ((origin or not referer) and (parsed.path or parsed.query)):
                return None
            if parsed.netloc.endswith(":") or parsed.port == 0:
                return None
            return (
                parsed.scheme,
                parsed.hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
            )
        except (ValueError, UnicodeError):
            return None

    def _host_allowed(self, headers, scope):
        scheme = "https" if scope.get("scheme") in {"https", "wss"} else "http"
        authority = self._authority(headers.get(b"host", b""), scheme=scheme)
        port = (
            self.listen_port
            if self.listen_port is not None
            else (scope.get("server") or ("", 80))[1]
        )
        return authority is not None and authority[2] == port

    def _origin_allowed(self, headers, scope):
        scheme = "https" if scope.get("scheme") in {"https", "wss"} else "http"
        authority = self._authority(headers.get(b"host", b""), scheme=scheme)
        for name in (b"origin", b"referer"):
            if (
                name in headers
                and self._authority(
                    headers[name], origin=name == b"origin", referer=name == b"referer"
                )
                != authority
            ):
                return False
        return True

    @staticmethod
    async def _respond(send, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json"), *_SECURITY_HEADERS],
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
    gui_static_dir: Path | None = None,
    listen_port: int | None = None,
) -> LocalApiSecurityMiddleware:
    """Build the loopback API app around a running Core Host.

    ``gui_static_dir`` mounts the prebuilt GUI bundle (read-only GET/HEAD) so
    `morrow gui` serves the observer from the same loopback origin without a
    browser session token.
    """

    from .agent_routes import agent_routes
    from .attachment_routes import attachment_routes
    from .chat import chat_routes
    from .definition_routes import definition_routes
    from .learning_routes import learning_routes
    from .mcp_routes import mcp_routes
    from .operation_routes import operation_routes
    from .permission_routes import permission_routes
    from .preview_routes import preview_routes
    from .preview_server import PreviewHttpServer
    from .provider_routes import provider_routes
    from .session_routes import session_routes
    from .settings_routes import settings_routes
    from .skill_routes import skill_routes
    from .state_routes import state_routes
    from .task_artifact_routes import task_artifact_routes
    from .workflow_outputs import workflow_output_routes
    from .workspace_routes import workspace_routes

    # HTML 运行预览是 GUI 专属能力：独立的 loopback 端口，随本服务生命周期启动
    # 与释放。CSP 必须在该页面加载前就知道预览 origin，所以监听在这里启动一次，
    # 而预览集合仍然按需创建、过期回收。
    previews: PreviewHttpServer | None = None
    if gui_static_dir is not None:
        frame_ancestors = (
            f"http://127.0.0.1:{listen_port} http://localhost:{listen_port}"
            if listen_port is not None
            else None
        )
        previews = PreviewHttpServer(PreviewRegistry(), frame_ancestors=frame_ancestors)
        try:
            previews.start()
        except OSError:
            logger.warning("HTML 预览监听端口不可用；预览功能本次关闭")
            previews = None

    class ScopedCommands:
        def __getattr__(self, name):
            def call(*args, **kwargs):
                return getattr(ServerCommands(host.context), name)(*args, **kwargs)

            return call

    commands = ScopedCommands()

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
        except ValidationError:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "request does not match the supported schema"
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

    async def management_query(request: Request) -> Response:
        try:
            page = int(request.query_params.get("page", "0"))
        except ValueError:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "management page is invalid"
            ) from None
        return await _query(
            lambda: host.context.context_management.query(
                request.path_params["kind"],
                scope=request.query_params.get("scope", "workspace"),
                session_id=request.query_params.get("session_id"),
                task_run_id=request.query_params.get("task_run_id"),
                agent_run_id=request.query_params.get("agent_run_id"),
                page=page,
            )
        )

    async def management_command(request: Request) -> Response:
        kind = request.path_params["kind"]
        if kind not in COMMAND_MODELS:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "unknown management command")
        body = await _parse_body(request, COMMAND_MODELS[kind])
        result = await host.execute_command(
            lambda: host.context.context_management.execute(
                kind, body, target=request.path_params.get("target")
            )
        )
        await host.execute_query(lambda: host.context.hub.publish(commands.meta()["latest_cursor"]))
        return JSONResponse({"result": result})

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

    async def recovery_eligibility(request: Request) -> Response:
        return await _query(
            lambda: commands.workflow_recovery_eligibility(request.path_params["run_id"])
        )

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

    # Pre-freeze Workflow Drafts -------------------------------------------------

    async def list_workflow_drafts(request: Request) -> Response:
        limit, after = _page_params(request)
        return await _query(lambda: commands.list_workflow_drafts(limit=limit, after=after))

    async def graph_plan(request: Request) -> Response:
        body = await _parse_body(request, GraphPlanRequest)
        await host.execute_query(lambda: commands.graph_plan_check(body))
        prepared = await host.execute_preparation(
            lambda: host.context.products.graph_planner.prepare(body.planning)
        )
        outcome = await host.execute_command(lambda: commands.graph_plan(body, prepared))
        return JSONResponse(outcome.wire())

    async def planning_catalog(request: Request) -> Response:
        return await _query(commands.planning_catalog)

    async def orchestration_policies(request: Request) -> Response:
        return await _query(commands.orchestration_policies)

    async def orchestration_policy_put(request: Request) -> Response:
        return await _command(
            request, OrchestrationPolicyRequest, commands.orchestration_policy_put
        )

    async def create_workflow_draft(request: Request) -> Response:
        return await _command(
            request,
            WorkflowDraftCreateRequest,
            lambda body: commands.workflow_draft_create(body),
        )

    async def get_workflow_draft(request: Request) -> Response:
        return await _query(lambda: commands.get_workflow_draft(request.path_params["draft_id"]))

    async def update_workflow_draft(request: Request) -> Response:
        draft_id = request.path_params["draft_id"]
        return await _command(
            request,
            WorkflowDraftUpdateRequest,
            lambda body: commands.workflow_draft_update(draft_id, body),
        )

    async def revalidate_workflow_draft(request: Request) -> Response:
        draft_id = request.path_params["draft_id"]
        return await _command(
            request,
            WorkflowDraftRowRequest,
            lambda body: commands.workflow_draft_revalidate(draft_id, body),
        )

    async def reject_workflow_draft(request: Request) -> Response:
        draft_id = request.path_params["draft_id"]
        return await _command(
            request,
            WorkflowDraftRowRequest,
            lambda body: commands.workflow_draft_reject(draft_id, body),
        )

    async def freeze_workflow_draft(request: Request) -> Response:
        draft_id = request.path_params["draft_id"]
        return await _command(
            request,
            WorkflowDraftRowRequest,
            lambda body: commands.workflow_draft_freeze(draft_id, body),
        )

    # Editable Agent definitions -------------------------------------------------

    async def create_agent_definition(request: Request) -> Response:
        return await _command(
            request,
            AgentDefinitionWriteRequest,
            lambda body: commands.agent_definition_create(body),
        )

    async def update_agent_definition(request: Request) -> Response:
        definition_id = request.path_params["definition_id"]
        return await _command(
            request,
            AgentDefinitionWriteRequest,
            lambda body: commands.agent_definition_update(definition_id, body),
        )

    async def publish_agent_definition(request: Request) -> Response:
        definition_id = request.path_params["definition_id"]
        return await _command(
            request,
            AgentDefinitionPublishRequest,
            lambda body: commands.agent_definition_publish(definition_id, body),
        )

    async def replan_list(request: Request) -> Response:
        return await _query(lambda: commands.replan_list(request.path_params["run_id"]))

    async def replan_process(request: Request) -> Response:
        return await _command(
            request,
            WorkflowControlRequest,
            lambda body: commands.replan_process(request.path_params["run_id"], body),
        )

    async def replan_decide(request: Request) -> Response:
        return await _command(
            request,
            ReplanDecisionRequest,
            lambda body: commands.replan_decide(request.path_params["proposal_id"], body),
        )

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

    async def catalog_agent_version(request: Request) -> Response:
        return await _query(
            lambda: commands.catalog_agent_version(request.path_params["version_id"])
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

    async def run_preview(request: Request) -> Response:
        return await _query(lambda: commands.run_preview(request.path_params["revision_id"]))

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

    async def _workflow_compilation_error(
        request: Request, exc: WorkflowCompilationError
    ) -> Response:
        return JSONResponse(
            {
                "error": {
                    "code": "workflow_compilation_failed",
                    "message": "Workflow compilation failed",
                    "diagnostics": [
                        {
                            "severity": item.severity.value,
                            "code": item.code,
                            "message": item.message,
                            "node_id": item.node_id,
                            "edge_id": item.edge_id,
                        }
                        for item in exc.diagnostics
                    ],
                }
            },
            status_code=400,
        )

    async def _value_error(request: Request, exc: ValueError) -> Response:
        text = str(exc).casefold()
        if isinstance(exc, StaleRowVersionError) or "stale" in text:
            return JSONResponse(
                _error_body("stale", "resource row version is stale"), status_code=409
            )
        if any(
            marker in text
            for marker in (
                "conflict",
                "already exists",
                "already resolved",
                "already consumed",
            )
        ):
            message = (
                "resource revision conflict"
                if "revision conflict" in text
                else "request conflicts with current state"
            )
            return JSONResponse(_error_body("conflict", message), status_code=409)
        return JSONResponse(_error_body("invalid", "request is invalid"), status_code=400)

    async def _internal_error(request: Request, exc: Exception) -> Response:
        logger.warning("unhandled API error: %s", type(exc).__name__)
        return JSONResponse(_error_body("internal", "internal server error"), status_code=500)

    app = Starlette(
        routes=[
            *chat_routes(host, _parse_body),
            *agent_routes(host, _parse_body),
            *attachment_routes(host, _parse_body),
            *definition_routes(host, _parse_body),
            *learning_routes(host, _parse_body),
            *skill_routes(host, _parse_body),
            *mcp_routes(host, _parse_body),
            *operation_routes(host, _parse_body),
            *(preview_routes(host, _parse_body, previews) if previews is not None else []),
            *state_routes(host, _parse_body),
            *task_artifact_routes(host),
            *workflow_output_routes(host),
            *workspace_routes(host, _parse_body),
            *workspace_file_routes(host, _parse_body),
            *session_routes(host, _parse_body),
            *provider_routes(host, _parse_body),
            *settings_routes(host, _parse_body),
            *permission_routes(host, _parse_body),
            Route(f"{API_PREFIX}/management/{{kind}}", management_query),
            Route(f"{API_PREFIX}/management/{{kind}}", management_command, methods=["POST"]),
            Route(
                f"{API_PREFIX}/management/{{kind}}/{{target}}", management_command, methods=["POST"]
            ),
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
            Route(
                f"{API_PREFIX}/workflow-runs/{{run_id}}/recovery-eligibility",
                recovery_eligibility,
            ),
            Route(f"{API_PREFIX}/workflow-runs/{{run_id}}/pause", pause_run, methods=["POST"]),
            Route(f"{API_PREFIX}/workflow-runs/{{run_id}}/resume", resume_run, methods=["POST"]),
            Route(f"{API_PREFIX}/workflow-runs/{{run_id}}/cancel", cancel_run, methods=["POST"]),
            Route(f"{API_PREFIX}/workflow-runs/{{run_id}}/rerun", rerun_run, methods=["POST"]),
            Route(f"{API_PREFIX}/workflow-runs/{{run_id}}/abandon", abandon_run, methods=["POST"]),
            Route(f"{API_PREFIX}/workflow-drafts", list_workflow_drafts),
            Route(f"{API_PREFIX}/workflow-planner", graph_plan, methods=["POST"]),
            Route(f"{API_PREFIX}/catalog/planning", planning_catalog),
            Route(f"{API_PREFIX}/orchestration-policies", orchestration_policies),
            Route(
                f"{API_PREFIX}/orchestration-policies", orchestration_policy_put, methods=["PUT"]
            ),
            Route(f"{API_PREFIX}/workflow-drafts", create_workflow_draft, methods=["POST"]),
            Route(f"{API_PREFIX}/workflow-drafts/{{draft_id}}", get_workflow_draft),
            Route(
                f"{API_PREFIX}/workflow-drafts/{{draft_id}}",
                update_workflow_draft,
                methods=["PUT"],
            ),
            Route(
                f"{API_PREFIX}/workflow-drafts/{{draft_id}}/validate",
                revalidate_workflow_draft,
                methods=["POST"],
            ),
            Route(
                f"{API_PREFIX}/workflow-drafts/{{draft_id}}/reject",
                reject_workflow_draft,
                methods=["POST"],
            ),
            Route(
                f"{API_PREFIX}/workflow-drafts/{{draft_id}}/freeze",
                freeze_workflow_draft,
                methods=["POST"],
            ),
            Route(f"{API_PREFIX}/agent-definitions", create_agent_definition, methods=["POST"]),
            Route(
                f"{API_PREFIX}/agent-definitions/{{definition_id}}",
                update_agent_definition,
                methods=["PUT"],
            ),
            Route(
                f"{API_PREFIX}/agent-definitions/{{definition_id}}/publish",
                publish_agent_definition,
                methods=["POST"],
            ),
            Route(f"{API_PREFIX}/workflow-runs/{{run_id}}/replans", replan_list, methods=["GET"]),
            Route(
                f"{API_PREFIX}/workflow-runs/{{run_id}}/replans/process",
                replan_process,
                methods=["POST"],
            ),
            Route(f"{API_PREFIX}/replans/{{proposal_id}}/decide", replan_decide, methods=["POST"]),
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
            Route(
                f"{API_PREFIX}/catalog/agent-versions/{{version_id}}",
                catalog_agent_version,
            ),
            Route(f"{API_PREFIX}/catalog/workflow-definitions", catalog_workflow_definitions),
            Route(
                f"{API_PREFIX}/catalog/workflow-definitions/{{definition_id}}",
                catalog_workflow_definition,
            ),
            Route(f"{API_PREFIX}/catalog/workflow-revisions", catalog_workflow_revisions),
            Route(
                f"{API_PREFIX}/catalog/workflow-revisions/{{revision_id}}/run-preview",
                run_preview,
            ),
            Route(f"{API_PREFIX}/catalog/providers", catalog_providers),
            Route(f"{API_PREFIX}/catalog/skills", catalog_skills),
            Route(f"{API_PREFIX}/catalog/tools", catalog_tools),
            Route(f"{API_PREFIX}/catalog/artifact-contracts", catalog_artifact_contracts),
        ]
        + (
            [
                Route(
                    "/{path:path}",
                    make_gui_static_handler(gui_static_dir),
                    methods=["GET", "HEAD"],
                )
            ]
            if gui_static_dir is not None
            else []
        ),
        exception_handlers={
            ApplicationError: _application_error,
            CommandBackpressureError: _backpressure,
            CoreHostUnavailableError: _unavailable,
            WorkflowCompilationError: _workflow_compilation_error,
            ValueError: _value_error,
            Exception: _internal_error,
        },
    )

    # Reuse the same strict DTOs and application services for every workspace
    # resource. ContextVars propagate through the command bus, never global state.
    def scoped_endpoint(endpoint):
        async def invoke(request):
            wid = request.path_params["workspace_id"]
            context = await host.execute_query(lambda: host.context.workspaces.get(wid))
            token = host.request_context.set(context)
            try:
                return await endpoint(request)
            finally:
                host.request_context.reset(token)

        return invoke

    aliases = []
    for route in app.routes:
        if (
            not route.path.startswith("/v1/")
            or route.path.startswith("/v1/workspaces")
            or route.path == "/v1/directories"
        ):
            continue
        path = "/v1/workspaces/{workspace_id}" + route.path[3:]
        if isinstance(route, WebSocketRoute):
            aliases.append(WebSocketRoute(path, scoped_endpoint(route.endpoint)))
        else:
            aliases.append(Route(path, scoped_endpoint(route.endpoint), methods=route.methods))
    # Before the static catch-all, after the specialized Chat routes.
    app.router.routes[0:0] = [
        route
        for route in aliases
        if "/sessions" not in route.path and not route.path.endswith("/capabilities")
    ]
    insertion = next(
        (i for i, route in enumerate(app.routes) if route.path == "/{path:path}"), len(app.routes)
    )
    app.router.routes[insertion:insertion] = [
        route for route in aliases if "/sessions" in route.path
    ]

    middleware = LocalApiSecurityMiddleware(
        app,
        token=auth_token,
        gui_enabled=gui_static_dir is not None,
        listen_port=listen_port,
        workspace_id=host.context.workspace_id,
        # 每个响应按当时的监听状态取 origin；监听未启用时为 None。
        preview_origin=(lambda: previews.origin) if previews is not None else None,
    )
    if previews is not None:
        # 服务退出或应用被回收时同步释放监听；不留下游离端口。
        weakref.finalize(middleware, previews.stop)
    return middleware


class _PatchResult:
    """Adapt a plain dict result into the command envelope."""

    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    def wire(self) -> dict[str, Any]:
        return {"result": self._result, "receipt": None}
