"""`morrow gui`: the Core server plus the prebuilt Web GUI in a browser.

Same foreground process model as `morrow serve` (loopback only, Ctrl+C shuts
down gracefully); the server additionally mounts the prebuilt GUI bundle and
the default browser opens at the loopback URL with the one-time session token
in the URL fragment, so the token never reaches the server or its logs.
"""

from __future__ import annotations

import webbrowser
from pathlib import Path

import typer

from morrow.bootstrap import build_application
from morrow.core.capabilities import PermissionPreset, PermissionProfile
from morrow.interfaces.serve_cli import _LOOPBACK_BINDS, _serve_core
from morrow.interfaces.workflow_cli import _identity
from morrow.server.static import DEFAULT_GUI_STATIC_DIR, gui_assets_available
from morrow.services.workspace import WorkspaceError


def gui_url(base_url: str, token: str) -> str:
    """The GUI entry URL; the token rides the fragment, never the request."""

    return f"{base_url}/#token={token}"


def gui(
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
    no_browser: bool = typer.Option(False, "--no-browser", help="只打印 URL，不打开浏览器。"),
    gui_dir: Path | None = typer.Option(None, "--gui-dir", hidden=True),
) -> None:
    """启动 Core 服务器并打开 Web GUI（前台、只读观察器）。"""

    if bind not in _LOOPBACK_BINDS:
        typer.echo("gui 仅允许绑定 loopback 地址（127.0.0.1/localhost/::1）。", err=True)
        raise typer.Exit(code=2)
    static_dir = gui_dir if gui_dir is not None else DEFAULT_GUI_STATIC_DIR
    if not gui_assets_available(static_dir):
        typer.echo(
            f"未找到预构建 GUI 资源（{static_dir}）。源码检出请先运行 "
            "`pnpm --dir gui build`；通过 wheel 安装请升级到包含 GUI 资源的版本。",
            err=True,
        )
        raise typer.Exit(code=2)
    application = build_application(state_root=state_root)
    try:
        identity = _identity(application, workspace_id, directory)
    except WorkspaceError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    def announce(base_url: str, token: str) -> None:
        url = gui_url(base_url, token)
        typer.echo(f"morrow gui listening: {base_url}")
        typer.echo(f"gui url: {url}")
        if not no_browser:
            webbrowser.open(url)

    _serve_core(
        application=application,
        identity=identity,
        permission_profile=PermissionProfile.from_preset(permission_mode),
        bind=bind,
        port=port,
        gui_static_dir=static_dir,
        announce=announce,
    )


def register(app: typer.Typer) -> None:
    app.command("gui", help="启动 Core 服务器并打开 Web GUI（前台）。")(gui)
