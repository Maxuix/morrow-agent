"""Typer entry point and local provider/model commands."""

from __future__ import annotations

import asyncio
import getpass
from pathlib import Path

import typer

from morrow.adapters.credentials.keyring import environment_credential
from morrow.bootstrap import build_application, build_session_application
from morrow.interfaces.terminal import run_repl
from morrow.services.workspace import WorkspaceError, WorkspaceWriterLock

app = typer.Typer(help="Morrow（承序）工作空间连续性 Agent。")
provider_app = typer.Typer(help="Provider 管理。")
model_app = typer.Typer(help="模型查看。")
workspace_app = typer.Typer(help="工作空间管理。")
app.add_typer(provider_app, name="provider")
app.add_typer(model_app, name="model")
app.add_typer(workspace_app, name="workspace")


def _secret(provider_id: str = "opencode-go") -> str:
    configured = environment_credential(provider_id)
    if configured:
        return configured
    return getpass.getpass("OpenCode Go API Key（输入不回显）：")


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    dir: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    if ctx.invoked_subcommand:
        return
    application = build_application(state_root=state_root)
    global_state = application.global_store.load()
    if global_state.status.value != "ok":
        typer.echo(
            f"全局状态不可安全加载（{global_state.status.value}），已阻止写入；请先恢复或升级。",
            err=True,
        )
        raise typer.Exit(code=2)
    resolution = application.workspace_service.resolve(dir)
    if resolution.status == "candidate":
        candidate = resolution.candidate
        typer.echo(f"发现新工作空间：{candidate.display_name} ({candidate.path})")
        if not typer.confirm("确认登记此工作空间？"):
            raise typer.Exit(code=2)
        identity = application.workspace_service.confirm(resolution)
    else:
        identity = resolution.identity
    try:
        with WorkspaceWriterLock(application.data_root, identity.workspace_id):
            code = _run_workspace(application, identity)
    except WorkspaceError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    raise typer.Exit(code=code)


def _run_workspace(application, identity) -> int:
    if not application.provider_service.current_model():
        typer.echo("尚未配置模型，开始 Provider 引导。")
        secret = _secret()
        try:
            application.provider_service.add("opencode-go", secret)
        except Exception as exc:
            typer.echo(f"Provider 配置失败：{type(exc).__name__}", err=True)
            raise typer.Exit(code=2) from exc
    inspection = application.workspace_state_service.inspect(identity.workspace_id)
    profile_result = inspection.profile
    read_only_workspace = inspection.read_only
    if read_only_workspace:
        typer.echo("工作空间状态来自更新版本；本次仅进入独立只读对话。")
    if not read_only_workspace and not profile_result.value:
        summary = typer.prompt("项目要达成什么？（可跳过）", default="", show_default=False)
        current_goal = typer.prompt(
            "现在准备推进哪一步？（可跳过）", default="", show_default=False
        )
        try:
            application.workspace_state_service.onboard(
                identity.workspace_id,
                display_name=identity.display_name,
                summary=summary,
                current_goal=current_goal,
            )
        except WorkspaceError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc
    session, _, handoff_service, _, orchestrator = build_session_application(application, identity)
    session.read_only = read_only_workspace
    available = application.workspace_state_service.inspect(identity.workspace_id).handoff
    if available.value:
        typer.echo(
            f"发现可用交接 revision {available.revision}：{available.value.handoff.current_goal}"
        )
        typer.echo("输入 /continue 才会加载它；直接输入消息将开始独立会话。")
    return asyncio.run(
        run_repl(
            orchestrator,
            handoff_service=handoff_service,
            project_store=application.project_store,
            workspace_id=identity.workspace_id,
            session=session,
        )
    )


@provider_app.command("list")
def provider_list(
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    service = build_application(state_root=state_root).provider_service
    config = service.list()
    for provider_id, provider in config.providers.items():
        typer.echo(f"{provider_id}\t{provider.adapter}\t{provider.base_url}")


@provider_app.command("show")
def provider_show(
    provider_id: str, state_root: Path | None = typer.Option(None, "--state-root", hidden=True)
) -> None:
    provider = build_application(state_root=state_root).provider_service.provider(provider_id)
    typer.echo(f"adapter: {provider.adapter}")
    typer.echo(f"base_url: {provider.base_url}")
    typer.echo(f"credential: {'已配置' if provider.credential_ref else '未配置'}")
    typer.echo("models: " + ", ".join(provider.models))
    if provider.last_test:
        typer.echo(
            "last_test: "
            + ("ok" if provider.last_test.ok else provider.last_test.message or "failed")
        )


@provider_app.command("add")
def provider_add(
    preset: str = typer.Option("opencode-go", "--preset"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    service = build_application(state_root=state_root).provider_service
    try:
        model = service.add(preset, _secret())
    except Exception as exc:
        typer.echo(f"Provider 添加失败：{type(exc).__name__}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"已配置 {model}")


@provider_app.command("test")
def provider_test(
    provider_id: str, state_root: Path | None = typer.Option(None, "--state-root", hidden=True)
) -> None:
    try:
        result = build_application(state_root=state_root).provider_service.test(provider_id)
    except Exception as exc:
        typer.echo(f"连接失败：{type(exc).__name__}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo("连接成功" if result.ok else f"连接失败：{result.message or '未知错误'}")


@provider_app.command("configure")
def provider_configure(
    provider_id: str,
    base_url: str | None = typer.Option(None, "--base-url"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    application = build_application(state_root=state_root)
    try:
        application.provider_service.configure(
            provider_id, secret=_secret(provider_id), base_url=base_url
        )
    except Exception as exc:
        typer.echo(f"Provider 更新失败：{type(exc).__name__}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo("Provider 已更新并通过连接测试")


@model_app.command("list")
def model_list(
    provider: str | None = typer.Option(None, "--provider"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    config = build_application(state_root=state_root).provider_service.list()
    for provider_id, value in config.providers.items():
        if provider and provider != provider_id:
            continue
        for model_id in value.models:
            typer.echo(f"{provider_id}/{model_id}")


@model_app.command("current")
def model_current(
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    current = build_application(state_root=state_root).provider_service.current_model()
    typer.echo(str(current) if current else "未配置 active_model")


@workspace_app.command("relink")
def workspace_relink(
    workspace_id: str,
    dir: Path = typer.Option(..., "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    application = build_application(state_root=state_root)
    typer.echo(f"将把 {workspace_id} 指向 {dir.resolve()}")
    if not typer.confirm("确认重连？"):
        raise typer.Exit(code=2)
    identity = application.workspace_service.relink(workspace_id, dir)
    typer.echo(f"已重连：{identity.path}")


def main() -> None:
    app()
