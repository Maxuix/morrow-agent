"""Typer entry point and local provider/model commands."""

from __future__ import annotations

import asyncio
import getpass
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer
from prompt_toolkit import PromptSession

from morrow.adapters.credentials.keyring import CredentialAccessError, environment_credential
from morrow.adapters.local.sandbox import default_sandbox_backend
from morrow.adapters.registry import PRESETS
from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.api import OperationalApplicationService
from morrow.application.artifacts import ArtifactService
from morrow.application.backup import BackupBundleError, OperationalBackupService
from morrow.application.checkpoints import ContextCheckpointService, SessionForkService
from morrow.application.doctor import OperationalDoctor
from morrow.application.recovery import RecoveryService
from morrow.application.tasks import TaskService
from morrow.bootstrap import build_application, build_session_application
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.capabilities import PermissionPreset, PermissionProfile
from morrow.core.permissions import (
    UNCONFINED_HOST_WARNING,
    UNCONFINED_HOST_WARNING_DIGEST,
    CapabilityName,
)
from morrow.core.recovery import RecoveryResolution
from morrow.core.store import StorageError, StorageErrorCode, StoreOpenMode
from morrow.interfaces.terminal import Terminal, TerminalApprovalPort, run_repl
from morrow.runtime.durable_log import DurableConversationWriter, restore_conversation_log
from morrow.runtime.ids import RandomIdSource
from morrow.services.workspace import WorkspaceError, WorkspaceWriterLock

app = typer.Typer(help="Morrow（承序）工作空间终端 Agent。")
provider_app = typer.Typer(help="Provider 管理。")
model_app = typer.Typer(help="模型查看。")
workspace_app = typer.Typer(help="工作空间管理。")
session_app = typer.Typer(help="Session 生命周期与历史。")
task_app = typer.Typer(help="TaskRun 操作。")
artifact_app = typer.Typer(help="Artifact 查看与保留。")
recovery_app = typer.Typer(help="恢复报告与决策。")
grant_app = typer.Typer(help="Foreground AgentRun 的手动权限授予与撤销。")
state_app = typer.Typer(help="Operational Store 诊断、事件与备份。")
app.add_typer(provider_app, name="provider")
app.add_typer(model_app, name="model")
app.add_typer(workspace_app, name="workspace")
app.add_typer(session_app, name="session")
app.add_typer(task_app, name="task")
app.add_typer(artifact_app, name="artifact")
app.add_typer(recovery_app, name="recovery")
app.add_typer(grant_app, name="grant")
app.add_typer(state_app, name="state")


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
        help="权限预设：manual、auto-safe、auto-sandboxed 或 full-access-manual。",
    ),
    session_id: str | None = typer.Option(
        None, "--session-id", help="恢复指定 Session；不提供时创建新的前台 Session。"
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
                resume_session_id=session_id,
            )
    except WorkspaceError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    except CredentialAccessError as exc:
        _echo_credential_error(exc)
        raise typer.Exit(code=2) from None
    raise typer.Exit(code=code)


def _run_workspace(
    application,
    identity,
    *,
    permission_profile: PermissionProfile,
    resume_session_id: str | None = None,
) -> int:
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
    if resume_session_id is None:
        candidates = _resume_candidates(application, identity.workspace_id)
        if candidates:
            listed = ", ".join(candidates)
            typer.echo(
                f"检测到可恢复 Session：{listed}；如需继续，请使用 --session-id SESSION_ID。"
            )
    terminal = Terminal()
    prompt_session = PromptSession()
    approval_port = TerminalApprovalPort(terminal, prompt_session)
    try:
        session_app = build_session_application(
            application,
            identity,
            approval_port=approval_port,
            permission_profile=permission_profile,
            resume_session_id=resume_session_id,
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
            resume_current_turn=session_app.persistence.pending_resume,
        )
    )


def _resume_candidates(application, workspace_id: str) -> tuple[str, ...]:
    """Find resumable Sessions without making the default REPL choose for the user."""

    store = OperationalStore(application.data_root.root)
    try:
        handle = store.open(StoreOpenMode.READ_ONLY)
    except StorageError as exc:
        if exc.code is StorageErrorCode.NOT_FOUND:
            return ()
        return ()
    try:
        journal = SqliteOperationalJournal(handle)
        candidates: list[str] = []
        for session in journal.list_sessions(workspace_id):
            if session.health.value == "needs_recovery":
                candidates.append(session.session_id)
                continue
            active = restore_conversation_log(journal, workspace_id, session.session_id)
            executions = journal.list_session_executions(workspace_id, session.session_id)
            if active.has_active_turn or any(item.state.value != "closed" for item in executions):
                candidates.append(session.session_id)
        return tuple(candidates)
    except (RuntimeError, StorageError, ValueError):
        return ()
    finally:
        handle.close()


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


def _state_services(
    *,
    state_root: Path | None,
    workspace_id: str | None,
    directory: Path,
    write: bool,
):
    """Build the same application boundary used by the REPL without providers."""

    application = build_application(state_root=state_root)
    if workspace_id is None:
        resolution = application.workspace_service.resolve(directory)
        if resolution.status == "candidate":
            raise WorkspaceError("工作空间尚未登记；请先用主命令确认，或提供 --workspace-id。")
        workspace_id = resolution.identity.workspace_id
    store = OperationalStore(application.data_root.root)
    mode = StoreOpenMode.READ_WRITE if write else StoreOpenMode.READ_ONLY
    try:
        handle = store.open(mode)
    except StorageError as exc:
        if write and exc.code is StorageErrorCode.NOT_FOUND:
            handle = store.initialize()
        else:
            raise
    journal = SqliteOperationalJournal(handle)
    files = FilesystemArtifactStore(store.layout)
    if write:
        files.ensure_layout()
    artifacts = ArtifactService(
        journal=journal,
        filesystem=files,
        workspace_id=workspace_id,
        id_source=application.id_source,
    )
    checkpoints = ContextCheckpointService(
        journal, workspace_id=workspace_id, id_source=application.id_source
    )
    forks = SessionForkService(journal, workspace_id=workspace_id, id_source=application.id_source)
    tasks = TaskService(journal=journal, workspace_id=workspace_id, id_source=application.id_source)
    recovery = RecoveryService(
        journal,
        workspace_id=workspace_id,
        id_source=application.id_source,
    )
    api = OperationalApplicationService(
        journal=journal,
        workspace_id=workspace_id,
        id_source=application.id_source,
        tasks=tasks,
        artifacts=artifacts,
        recovery=recovery,
        checkpoints=checkpoints,
        forks=forks,
    )
    return (
        application,
        handle,
        api,
        OperationalDoctor(store),
        OperationalBackupService(store, journal=journal),
    )


def _close_state(handle) -> None:
    if handle is not None:
        handle.close()


def _emit_model(value, *, as_json: bool = False) -> None:
    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    if isinstance(payload, dict):
        for key, item in payload.items():
            if isinstance(item, (dict, list, tuple)):
                item = json.dumps(item, ensure_ascii=False, sort_keys=True)
            typer.echo(f"{key}: {item}")
    else:
        typer.echo(str(payload))


def _cli_error(exc: Exception) -> None:
    if isinstance(exc, ApplicationError):
        typer.echo(f"{exc.code.value}: {exc.message}", err=True)
    elif isinstance(exc, (WorkspaceError, StorageError, BackupBundleError, ValueError)):
        typer.echo(str(exc), err=True)
    else:
        typer.echo("application command failed", err=True)


@session_app.command("list")
def session_list(
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    cursor: str | None = typer.Option(None, "--cursor"),
    limit: int = typer.Option(50, "--limit", min=1, max=100),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    handle = None
    try:
        _application, handle, api, _doctor, _backup = _state_services(
            state_root=state_root, workspace_id=workspace_id, directory=directory, write=False
        )
        for session in api.list_sessions(cursor=cursor, limit=limit).items:
            typer.echo(
                f"{session.session_id}\t{session.lifecycle.value}\t{session.health.value}\t"
                f"position={session.conversation_position}"
            )
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


@session_app.command("create")
def session_create(
    session_id: str | None = typer.Option(None, "--session-id"),
    command_id: str | None = typer.Option(None, "--command-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    handle = None
    try:
        _application, handle, api, _doctor, _backup = _state_services(
            state_root=state_root, workspace_id=workspace_id, directory=directory, write=True
        )
        result = api.create_session(session_id=session_id, command_id=command_id)
        _emit_model(result.value)
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


@session_app.command("status")
def session_status(
    session_id: str,
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    handle = None
    try:
        _application, handle, api, _doctor, _backup = _state_services(
            state_root=state_root, workspace_id=workspace_id, directory=directory, write=False
        )
        value = api.get_session(session_id)
        if value is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Session is missing")
        _emit_model(value)
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


@session_app.command("resume")
def session_resume(
    session_id: str,
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    permission_mode: PermissionPreset = typer.Option(
        PermissionPreset.MANUAL,
        "--permission-mode",
        "--mode",
        help="权限预设：manual、auto-safe、auto-sandboxed 或 full-access-manual。",
    ),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    try:
        application = build_application(state_root=state_root)
        global_state = application.global_store.load()
        if global_state.status.value != "ok":
            raise WorkspaceError(
                f"全局状态不可安全加载（{global_state.status.value}），已阻止恢复。"
            )
        resolution = application.workspace_service.resolve(directory)
        if resolution.status != "existing" or resolution.identity is None:
            raise WorkspaceError("工作空间尚未登记；请先用主命令确认工作空间。")
        identity = resolution.identity
        if workspace_id is not None and workspace_id != identity.workspace_id:
            raise WorkspaceError("--workspace-id 与 --dir 不属于同一工作空间。")
        with WorkspaceWriterLock(application.data_root, identity.workspace_id):
            code = _run_workspace(
                application,
                identity,
                permission_profile=PermissionProfile.from_preset(permission_mode),
                resume_session_id=session_id,
            )
    except (WorkspaceError, CredentialAccessError) as exc:
        if isinstance(exc, CredentialAccessError):
            _echo_credential_error(exc)
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from None
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from None
    raise typer.Exit(code=code)


@session_app.command("archive")
def session_archive(
    session_id: str,
    command_id: str | None = typer.Option(None, "--command-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    handle = None
    try:
        _application, handle, api, _doctor, _backup = _state_services(
            state_root=state_root, workspace_id=workspace_id, directory=directory, write=True
        )
        _emit_model(api.archive_session(session_id, command_id=command_id).value)
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


@session_app.command("fork")
def session_fork(
    parent_session_id: str,
    checkpoint_id: str | None = typer.Option(None, "--checkpoint-id"),
    cut_position: int | None = typer.Option(None, "--cut-position", min=1),
    reason: str = typer.Option("context fork", "--reason"),
    command_id: str | None = typer.Option(None, "--command-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    handle = None
    try:
        _application, handle, api, _doctor, _backup = _state_services(
            state_root=state_root, workspace_id=workspace_id, directory=directory, write=True
        )
        _emit_model(
            api.fork_session(
                parent_session_id,
                checkpoint_id=checkpoint_id,
                cut_position=cut_position,
                reason=reason,
                command_id=command_id,
            ).value
        )
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


@task_app.command("show")
def task_show(
    task_run_id: str,
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    handle = None
    try:
        _application, handle, api, _doctor, _backup = _state_services(
            state_root=state_root, workspace_id=workspace_id, directory=directory, write=False
        )
        task = api.get_task(task_run_id)
        if task is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "TaskRun is missing")
        _emit_model(task)
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


@task_app.command("list")
def task_list(
    session_id: str,
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    cursor: str | None = typer.Option(None, "--cursor"),
    limit: int = typer.Option(50, "--limit", min=1, max=100),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    handle = None
    try:
        _application, handle, api, _doctor, _backup = _state_services(
            state_root=state_root, workspace_id=workspace_id, directory=directory, write=False
        )
        for task in api.list_tasks(session_id, cursor=cursor, limit=limit).items:
            typer.echo(
                f"{task.task_run_id}\t{task.status.value}\t{task.row_version}\t"
                f"attempt={task.attempt}"
            )
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


def _task_command(
    command, task_run_id, command_id, workspace_id, directory, state_root, expected_row_version
):
    handle = None
    try:
        _application, handle, api, _doctor, _backup = _state_services(
            state_root=state_root, workspace_id=workspace_id, directory=directory, write=True
        )
        _emit_model(command(api, task_run_id, command_id, expected_row_version).value)
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


@task_app.command("new")
def task_new(
    session_id: str,
    command_id: str | None = typer.Option(None, "--command-id"),
    expected_row_version: int | None = typer.Option(None, "--expected-row-version", min=1),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    handle = None
    try:
        _application, handle, api, _doctor, _backup = _state_services(
            state_root=state_root, workspace_id=workspace_id, directory=directory, write=True
        )
        _emit_model(
            api.task_new(
                session_id, command_id=command_id, expected_row_version=expected_row_version
            ).value
        )
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


@task_app.command("accept")
def task_accept(
    task_run_id: str,
    command_id: str | None = typer.Option(None, "--command-id"),
    expected_row_version: int | None = typer.Option(None, "--expected-row-version", min=1),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _task_command(
        lambda api, task, cmd, ver: api.task_accept(task, command_id=cmd, expected_row_version=ver),
        task_run_id,
        command_id,
        workspace_id,
        directory,
        state_root,
        expected_row_version,
    )


@task_app.command("cancel")
def task_cancel(
    task_run_id: str,
    command_id: str | None = typer.Option(None, "--command-id"),
    expected_row_version: int | None = typer.Option(None, "--expected-row-version", min=1),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _task_command(
        lambda api, task, cmd, ver: api.task_cancel(task, command_id=cmd, expected_row_version=ver),
        task_run_id,
        command_id,
        workspace_id,
        directory,
        state_root,
        expected_row_version,
    )


@task_app.command("resume")
def task_resume(
    task_run_id: str,
    command_id: str | None = typer.Option(None, "--command-id"),
    expected_row_version: int | None = typer.Option(None, "--expected-row-version", min=1),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _task_command(
        lambda api, task, cmd, ver: api.task_resume(task, command_id=cmd, expected_row_version=ver),
        task_run_id,
        command_id,
        workspace_id,
        directory,
        state_root,
        expected_row_version,
    )


@grant_app.command("list")
def grant_list(
    agent_run_id: str | None = typer.Option(None, "--agent-run-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    handle = None
    try:
        _application, handle, api, _doctor, _backup = _state_services(
            state_root=state_root, workspace_id=workspace_id, directory=directory, write=False
        )
        for grant in api.list_grants(agent_run_id=agent_run_id).items:
            typer.echo(
                f"{grant.grant_id}\t{grant.agent_run_id}\t"
                f"{','.join(value.value for value in grant.capabilities)}\t"
                f"expires={grant.expires_at.isoformat()}\t"
                f"revoked={grant.revoked_at.isoformat() if grant.revoked_at else '-'}"
            )
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


@grant_app.command("show")
def grant_show(
    grant_id: str,
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    handle = None
    try:
        _application, handle, api, _doctor, _backup = _state_services(
            state_root=state_root, workspace_id=workspace_id, directory=directory, write=False
        )
        value = api.get_grant(grant_id)
        if value is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "CapabilityGrant is missing")
        _emit_model(value)
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


@grant_app.command("create")
def grant_create(
    task_run_id: str,
    agent_run_id: str,
    reason: str = typer.Option(..., "--reason"),
    expires_minutes: int = typer.Option(15, "--expires-minutes", min=1, max=1_440),
    preview_digest: str | None = typer.Option(None, "--preview-digest"),
    command_id: str | None = typer.Option(None, "--command-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    """Explicitly grant only unconfined_host_process to one foreground AgentRun."""

    typer.echo(UNCONFINED_HOST_WARNING, err=True)
    if not typer.confirm("确认授予该 AgentRun 一次手动 Host 执行权限？"):
        raise typer.Exit(code=2)
    handle = None
    try:
        if preview_digest is not None and preview_digest != UNCONFINED_HOST_WARNING_DIGEST:
            raise typer.BadParameter(
                "--preview-digest must match the canonical digest of the displayed Host warning"
            )
        _application, handle, api, _doctor, _backup = _state_services(
            state_root=state_root, workspace_id=workspace_id, directory=directory, write=True
        )
        result = api.create_grant(
            task_run_id=task_run_id,
            agent_run_id=agent_run_id,
            capabilities=(CapabilityName.UNCONFINED_HOST_PROCESS,),
            reason=reason,
            preview_digest=UNCONFINED_HOST_WARNING_DIGEST,
            expires_at=datetime.now(UTC) + timedelta(minutes=expires_minutes),
            command_id=command_id,
        )
        _emit_model(result.value)
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


@grant_app.command("revoke")
def grant_revoke(
    grant_id: str,
    reason: str = typer.Option("revoked by local interface", "--reason"),
    expected_row_version: int | None = typer.Option(None, "--expected-row-version", min=1),
    command_id: str | None = typer.Option(None, "--command-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    handle = None
    try:
        _application, handle, api, _doctor, _backup = _state_services(
            state_root=state_root, workspace_id=workspace_id, directory=directory, write=True
        )
        _emit_model(
            api.revoke_grant(
                grant_id,
                reason=reason,
                expected_row_version=expected_row_version,
                command_id=command_id,
            ).value
        )
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


@artifact_app.command("list")
def artifact_list(
    session_id: str | None = typer.Option(None, "--session-id"),
    task_run_id: str | None = typer.Option(None, "--task-run-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    handle = None
    try:
        _application, handle, api, _doctor, _backup = _state_services(
            state_root=state_root, workspace_id=workspace_id, directory=directory, write=False
        )
        for item in api.list_artifacts(session_id=session_id, task_run_id=task_run_id).items:
            typer.echo(
                f"{item.artifact_id}\t{item.state.value}\t{item.byte_size} bytes\t{item.retention.value}"
            )
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


@artifact_app.command("show")
def artifact_show(
    artifact_id: str,
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    handle = None
    try:
        _application, handle, api, _doctor, _backup = _state_services(
            state_root=state_root, workspace_id=workspace_id, directory=directory, write=False
        )
        item = api.get_artifact(artifact_id)
        if item is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Artifact is missing")
        _emit_model(item)
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


@artifact_app.command("pin")
def artifact_pin(
    artifact_id: str,
    command_id: str | None = typer.Option(None, "--command-id"),
    expected_row_version: int | None = typer.Option(None, "--expected-row-version", min=1),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    handle = None
    try:
        _application, handle, api, _doctor, _backup = _state_services(
            state_root=state_root, workspace_id=workspace_id, directory=directory, write=True
        )
        _emit_model(
            api.pin_artifact(
                artifact_id, command_id=command_id, expected_row_version=expected_row_version
            ).value
        )
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


@artifact_app.command("release")
def artifact_release(
    artifact_id: str,
    command_id: str | None = typer.Option(None, "--command-id"),
    expected_row_version: int | None = typer.Option(None, "--expected-row-version", min=1),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    handle = None
    try:
        _application, handle, api, _doctor, _backup = _state_services(
            state_root=state_root, workspace_id=workspace_id, directory=directory, write=True
        )
        _emit_model(
            api.release_artifact(
                artifact_id, command_id=command_id, expected_row_version=expected_row_version
            ).value
        )
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


@recovery_app.command("show")
def recovery_show(
    session_id: str,
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    handle = None
    try:
        _application, handle, api, _doctor, _backup = _state_services(
            state_root=state_root, workspace_id=workspace_id, directory=directory, write=False
        )
        for report in api.list_recovery(session_id):
            _emit_model(report)
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


@recovery_app.command("resolve")
def recovery_resolve(
    report_id: str,
    resolution: RecoveryResolution,
    command_id: str | None = typer.Option(
        None,
        "--command-id",
        help="可选幂等键；省略时自动生成，重试同一请求时可显式复用。",
    ),
    item_id: str | None = typer.Option(None, "--item-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    """持久化 Recovery 决策；resume 只准备 AgentRun，不调用 Provider。"""
    handle = None
    try:
        _application, handle, api, _doctor, _backup = _state_services(
            state_root=state_root, workspace_id=workspace_id, directory=directory, write=True
        )
        report = api.get_recovery(report_id)
        if report is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Recovery report is missing")
        log = restore_conversation_log(api.journal, api.workspace_id, report.session_id)
        writer = DurableConversationWriter(
            log,
            api.journal,
            workspace_id=api.workspace_id,
            session_id=report.session_id,
            id_source=RandomIdSource(),
        )
        result = api.resolve_recovery(
            report,
            command_id=command_id,
            resolution=resolution,
            item_id=item_id,
            log=log,
            writer=writer,
            close_all=resolution is RecoveryResolution.ABORT and item_id is None,
        )
        _emit_model(result.value)
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


@state_app.command("doctor")
def state_doctor(
    workspace_id: str = typer.Option(..., "--workspace-id"),
    as_json: bool = typer.Option(False, "--json"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    try:
        application = build_application(state_root=state_root)
        doctor = OperationalDoctor(OperationalStore(application.data_root.root))
        report = doctor.inspect(workspace_id)
        if as_json:
            typer.echo(report.json_bytes().decode("utf-8"))
        else:
            typer.echo(f"health: {report.health.value}")
            typer.echo(f"schema_version: {report.schema_version}")
            for issue in report.issues:
                typer.echo(f"{issue.severity.value}: {issue.code} ({issue.count}) {issue.summary}")
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None


@state_app.command("events")
def state_events(
    workspace_id: str = typer.Option(..., "--workspace-id"),
    after_cursor: int = typer.Option(0, "--after", min=0),
    limit: int = typer.Option(100, "--limit", min=1, max=100),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    handle = None
    try:
        _application, handle, api, _doctor, _backup = _state_services(
            state_root=state_root, workspace_id=workspace_id, directory=Path("."), write=False
        )
        for event in api.list_events(after_cursor=after_cursor, limit=limit).items:
            typer.echo(
                f"{event.cursor}\t{event.event_type}\t{event.aggregate_kind}\t{event.aggregate_id}"
            )
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


@state_app.command("backup")
def state_backup(
    name: str | None = typer.Option(None, "--name"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    handle = None
    try:
        _application, handle, _api, _doctor, backup = _state_services(
            state_root=state_root, workspace_id="ws_cli", directory=Path("."), write=True
        )
        _emit_model(backup.create(name))
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


@state_app.command("verify-backup")
def state_verify_backup(
    bundle: Path,
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    handle = None
    try:
        _application, handle, _api, _doctor, backup = _state_services(
            state_root=state_root, workspace_id="ws_cli", directory=Path("."), write=False
        )
        report = backup.verify(bundle)
        _emit_model(report)
        if not report.ok:
            raise typer.Exit(code=2)
    except typer.Exit:
        raise
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


@state_app.command("cleanup")
def state_cleanup(
    workspace_id: str = typer.Option(..., "--workspace-id"),
    apply: bool = typer.Option(False, "--apply", help="实际删除已验证的非托管孤儿文件。"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    handle = None
    try:
        _application, handle, api, _doctor, _backup = _state_services(
            state_root=state_root, workspace_id=workspace_id, directory=Path("."), write=apply
        )
        _emit_model(api.cleanup_orphans(dry_run=not apply))
    except Exception as exc:
        _cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        _close_state(handle)


def main() -> None:
    app()
