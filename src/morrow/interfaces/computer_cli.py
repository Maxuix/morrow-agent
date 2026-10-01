"""Local desktop configuration; these commands never grant a model device access."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from pydantic import ValidationError

from morrow.application.computer_settings import ComputerUseSettingsService
from morrow.bootstrap import build_application, build_computer_use_lifecycle
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.runtime_policy import ComputerUseMode, ComputerUseSettings
from morrow.runtime.policy import resolve_computer_use_settings

computer_app = typer.Typer(help="桌面功能配置与宿主状态；运行授权需独立选择。")


def _service(application):
    return ComputerUseSettingsService(
        application,
        preflight=lambda settings: build_computer_use_lifecycle(application, settings).preflight(),
    )


def _view(application, service):
    config = application.global_store.load().value
    return service.view(config.active_model if config else None)


def _emit(view, *, as_json):
    if as_json:
        typer.echo(json.dumps(view, ensure_ascii=False, sort_keys=True))
        return
    settings = view["settings"]
    typer.echo(f"全局修订：{view['revision']}")
    typer.echo(f"桌面功能：{'启用' if settings['enabled'] else '关闭'} · {settings['mode']}")
    typer.echo(f"宿主：{view['host']['status']} ({view['host']['reason']})")
    typer.echo(view["host_recovery"])
    typer.echo(f"当前全局模型：{view['model_error'] or '支持所需协议'}")
    typer.echo("配置对后续运行生效；仍需 Full Access Manual 和本次运行的独立桌面授权。")


def _error(exc):
    if isinstance(exc, ValidationError):
        typer.echo("桌面设置无效；预算超出允许范围。", err=True)
    else:
        typer.echo(f"桌面配置失败（{exc.code.value}）：{exc.message}", err=True)
    raise typer.Exit(code=2)


@computer_app.command("status")
def computer_status(
    as_json: bool = typer.Option(False, "--json"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    """查看全局配置及当前全局模型；不创建 Driver、运行或授权。"""
    application = build_application(state_root=state_root)
    try:
        _emit(_view(application, _service(application)), as_json=as_json)
    except ApplicationError as exc:
        _error(exc)


@computer_app.command("configure")
def computer_configure(
    expected_revision: int = typer.Option(..., "--expected-revision", min=0),
    enabled: bool | None = typer.Option(None, "--enable/--disable"),
    mode: ComputerUseMode | None = typer.Option(None, "--mode"),
    max_operations: int | None = typer.Option(None, "--max-operations", min=1, max=100),
    max_run_seconds: int | None = typer.Option(None, "--max-run-seconds", min=1, max=600),
    max_call_seconds: int | None = typer.Option(None, "--max-call-seconds", min=1, max=60),
    max_observation_bytes: int | None = typer.Option(
        None, "--max-observation-bytes", min=1, max=64 * 1024 * 1024
    ),
    image_long_edge_px: int | None = typer.Option(None, "--image-long-edge-px", min=1, max=1920),
    as_json: bool = typer.Option(False, "--json"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    """修改全局配置；先用 status 读取修订，不授予设备权限。"""
    updates = {
        key: value
        for key, value in {
            "enabled": enabled,
            "mode": mode,
            "max_operations": max_operations,
            "max_run_seconds": max_run_seconds,
            "max_call_seconds": max_call_seconds,
            "max_observation_bytes": max_observation_bytes,
            "image_long_edge_px": image_long_edge_px,
        }.items()
        if value is not None
    }
    if not updates:
        raise typer.BadParameter("至少指定一项桌面设置")
    application = build_application(state_root=state_root)
    service = _service(application)
    try:
        config = application.global_store.load().value
        if config is None:
            raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "桌面配置需要修复")
        settings = resolve_computer_use_settings(config.runtime_policy)
        settings = ComputerUseSettings.model_validate(settings.model_dump() | updates, strict=True)
        # The user's revision is authoritative, even if the local read is newer.
        service.put(settings, expected_revision=expected_revision)
        _emit(_view(application, service), as_json=as_json)
    except (ApplicationError, ValidationError) as exc:
        _error(exc)
