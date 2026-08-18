"""Typer entry point and local provider/model commands."""

from __future__ import annotations

import asyncio
import getpass
from pathlib import Path

import typer
from prompt_toolkit import PromptSession

from morrow.adapters.credentials.keyring import CredentialAccessError, environment_credential
from morrow.adapters.local.sandbox import default_sandbox_backend
from morrow.adapters.registry import PRESETS
from morrow.bootstrap import build_application, build_session_application
from morrow.core.capabilities import PermissionPreset, PermissionProfile
from morrow.interfaces.terminal import Terminal, TerminalApprovalPort, run_repl
from morrow.services.workspace import WorkspaceError, WorkspaceWriterLock

app = typer.Typer(help="Morrow（承序）工作空间终端 Agent。")
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


def _preset_option_help() -> str:
    listed = "、".join(PRESETS)
    return f"可用预设：{listed}。完整列表见 provider presets。"


def _echo_credential_error(exc: CredentialAccessError) -> None:
    typer.echo(exc.message, err=True)


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    dir: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
    permission_mode: PermissionPreset = typer.Option(
        PermissionPreset.MANUAL,
        "--permission-mode",
        "--mode",
        help="工作空间权限预设：manual、auto-safe 或 auto-sandboxed。",
    ),
) -> None:
    if ctx.invoked_subcommand:
        return
    if permission_mode is PermissionPreset.AUTO_SANDBOXED:
        capability = default_sandbox_backend().probe()
        if not capability.supported:
            typer.echo(
                f"Auto Sandboxed 不可用（{capability.reason}）；不会启动交互 Agent，也不会回退到 Host 执行。",
                err=True,
            )
            raise typer.Exit(code=2)
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
            code = _run_workspace(
                application,
                identity,
                permission_profile=PermissionProfile.from_preset(permission_mode),
            )
    except WorkspaceError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    except CredentialAccessError as exc:
        _echo_credential_error(exc)
        raise typer.Exit(code=2) from None
    raise typer.Exit(code=code)


def _run_workspace(application, identity, *, permission_profile: PermissionProfile) -> int:
    if not application.provider_service.current_model():
        typer.echo("尚未配置模型，开始 Provider 引导。")
        secret = _secret()
        try:
            application.provider_service.add("opencode-go", secret)
        except CredentialAccessError as exc:
            _echo_credential_error(exc)
            raise typer.Exit(code=2) from None
        except Exception as exc:
            typer.echo(f"Provider 配置失败：{type(exc).__name__}", err=True)
            raise typer.Exit(code=2) from exc
    inspection = application.workspace_state_service.inspect(identity.workspace_id)
    profile_result = inspection.profile
    read_only_workspace = inspection.read_only
    if read_only_workspace:
        typer.echo("Profile 无法安全加载；本次仅进入只读对话。")
    elif inspection.preferences_read_only:
        typer.echo("工作空间 Preferences 无法安全加载；本次将该层隔离为空且禁止覆盖。")
    if not read_only_workspace and not profile_result.value:
        summary = typer.prompt("项目要达成什么？（可跳过）", default="", show_default=False)
        try:
            application.workspace_state_service.onboard(
                identity.workspace_id,
                display_name=identity.display_name,
                summary=summary,
            )
        except WorkspaceError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc
    terminal = Terminal()
    prompt_session = PromptSession()
    approval_port = TerminalApprovalPort(terminal, prompt_session)
    try:
        session_app = build_session_application(
            application,
            identity,
            approval_port=approval_port,
            permission_profile=permission_profile,
        )
    except CredentialAccessError as exc:
        _echo_credential_error(exc)
        raise typer.Exit(code=2) from None
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from None
    session_app.session.read_only = read_only_workspace
    return asyncio.run(
        run_repl(
            session_app.orchestrator,
            session=session_app.session,
            terminal=terminal,
            prompt_session=prompt_session,
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
    service = build_application(state_root=state_root).provider_service
    try:
        provider = service.provider(provider_id)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"adapter: {provider.adapter}")
    typer.echo(f"base_url: {provider.base_url}")
    inspection = service.inspect_credential(provider_id)
    if inspection.available:
        typer.echo("credential: 可用")
    elif inspection.code == "missing":
        typer.echo("credential: 不可用")
    else:
        typer.echo(f"credential: 不可用（{inspection.code}）")
    typer.echo("models: " + ", ".join(provider.models))
    if provider.last_test:
        typer.echo(
            "last_test: "
            + ("ok" if provider.last_test.ok else provider.last_test.message or "failed")
        )
    if not inspection.available and inspection.code != "missing":
        typer.echo(inspection.message, err=True)
        raise typer.Exit(code=2)


@provider_app.command("presets")
def provider_presets() -> None:
    for preset_id, preset in PRESETS.items():
        typer.echo(f"{preset_id}\t{preset['provider_id']}/{preset['model_id']}")


@provider_app.command("add")
def provider_add(
    preset: str = typer.Option("opencode-go", "--preset", help=_preset_option_help()),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    service = build_application(state_root=state_root).provider_service
    try:
        model = service.add(preset, _secret())
    except CredentialAccessError as exc:
        _echo_credential_error(exc)
        raise typer.Exit(code=2) from None
    except Exception as exc:
        typer.echo(f"Provider 添加失败：{type(exc).__name__}", err=True)
        raise typer.Exit(code=2) from exc
    current = service.current_model()
    typer.echo(f"已配置 {model}")
    if current == model:
        typer.echo(f"当前模型：{current}")
    else:
        typer.echo(f"当前模型未切换：{current}")


@provider_app.command("test")
def provider_test(
    provider_id: str, state_root: Path | None = typer.Option(None, "--state-root", hidden=True)
) -> None:
    typer.echo("正在测试模型连接…")
    try:
        result = build_application(state_root=state_root).provider_service.test(provider_id)
    except CredentialAccessError as exc:
        _echo_credential_error(exc)
        raise typer.Exit(code=2) from None
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        typer.echo(f"连接失败：{type(exc).__name__}", err=True)
        raise typer.Exit(code=2) from exc
    if not result.ok:
        code = result.error_code.value if result.error_code else "internal"
        typer.echo(f"连接失败（{code}）：{result.message or '未知错误'}", err=True)
        raise typer.Exit(code=2)
    typer.echo("连接成功")


@provider_app.command("configure")
def provider_configure(
    provider_id: str,
    base_url: str | None = typer.Option(None, "--base-url"),
    replace_credential: bool = typer.Option(False, "--replace-credential"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    application = build_application(state_root=state_root)
    try:
        secret = None
        if replace_credential or not application.provider_service.credential_available(provider_id):
            secret = _secret(provider_id)
        application.provider_service.configure(
            provider_id,
            secret=secret,
            base_url=base_url,
            replace_credential=replace_credential,
        )
    except CredentialAccessError as exc:
        _echo_credential_error(exc)
        raise typer.Exit(code=2) from None
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
    try:
        identity = application.workspace_service.relink(workspace_id, dir)
    except WorkspaceError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"已重连：{identity.path}")


def main() -> None:
    app()
