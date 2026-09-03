"""`morrow serve`: the foreground headless Core API server.

The server binds loopback only, prints its address and one-time session token,
and shuts down gracefully on SIGINT: in-flight requests drain, driver tasks are
cancelled without recording any user cancellation, and durable state stays
owned by the Core process.
"""

from __future__ import annotations

import asyncio
import secrets
import socket
from pathlib import Path

import typer
import uvicorn

from morrow.bootstrap import build_application
from morrow.core.capabilities import PermissionPreset, PermissionProfile
from morrow.interfaces.workflow_cli import _identity
from morrow.server.app import create_asgi_app
from morrow.server.composition import make_context_builder
from morrow.server.host import CoreHost
from morrow.services.workspace import WorkspaceError, WorkspaceWriterLock

_LOOPBACK_BINDS = {"127.0.0.1", "localhost", "::1"}


def serve(
    port: int = typer.Option(0, "--port", help="监听端口；0 表示自动分配。"),
    bind: str = typer.Option("127.0.0.1", "--bind", help="绑定地址；仅允许 loopback。"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
    permission_mode: PermissionPreset = typer.Option(
        PermissionPreset.MANUAL,
        "--permission-mode",
        "--mode",
        help="权限预设：manual、auto-safe、auto-sandboxed 或 full-access-manual。",
    ),
) -> None:
    """启动本地 Core API 服务器（前台、headless）。"""

    if bind not in _LOOPBACK_BINDS:
        typer.echo("serve 仅允许绑定 loopback 地址（127.0.0.1/localhost/::1）。", err=True)
        raise typer.Exit(code=2)
    application = build_application(state_root=state_root)
    try:
        identity = _identity(application, workspace_id, directory)
    except WorkspaceError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    token = secrets.token_urlsafe(32)
    host = CoreHost(
        make_context_builder(
            application,
            identity,
            permission_profile=PermissionProfile.from_preset(permission_mode),
        )
    )
    try:
        with WorkspaceWriterLock(application.data_root, identity.workspace_id):
            try:
                host.start()
                listener = socket.socket(socket.AF_INET6 if bind == "::1" else socket.AF_INET)
                try:
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    listener.bind((bind, port))
                    listener.listen(socket.SOMAXCONN)
                    bound_port = listener.getsockname()[1]
                except OSError:
                    listener.close()
                    raise
                asgi_app = create_asgi_app(host, auth_token=token)
                display_host = "[::1]" if bind == "::1" else bind
                typer.echo(f"morrow serve listening: http://{display_host}:{bound_port}")
                typer.echo(f"session token: {token}")
                config = uvicorn.Config(
                    asgi_app,
                    log_level="warning",
                    access_log=False,
                )
                server = uvicorn.Server(config)
                try:
                    asyncio.run(server.serve(sockets=[listener]))
                except KeyboardInterrupt:
                    pass
            finally:
                host.stop()
    except WorkspaceError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc


def register(app: typer.Typer) -> None:
    app.command("serve", help="启动本地 Core API 服务器（前台、headless）。")(serve)
