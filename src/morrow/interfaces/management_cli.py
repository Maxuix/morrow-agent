"""Scriptable CLI client of the same Context/Learning/Skill command facade as GUI."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from morrow.application.management import COMMAND_MODELS, ManagementService
from morrow.application.preferences.tool import PreferenceManagementService
from morrow.bootstrap import build_skill_services
from morrow.services.profile_configuration import ConfigPatchService

management_app = typer.Typer(help="Context、Learning、Skill 管理；与 GUI 使用相同命令。")


def _run(
    kind,
    *,
    request_file,
    target,
    scope,
    task_run_id,
    agent_run_id,
    page,
    state_root,
    workspace_id,
    directory,
):
    from morrow.interfaces.cli import _state_services

    handle = None
    try:
        app, handle, api, _, _ = _state_services(
            state_root=state_root,
            workspace_id=workspace_id,
            directory=directory,
            write=request_file is not None,
        )
        from morrow.adapters.state.extension_yaml import ExtensionYamlStore
        from morrow.application.workflows.feedback import WorkflowFeedbackService
        from morrow.application.workflows.orchestration_policy import OrchestrationPolicyService

        feedback = WorkflowFeedbackService(
            api.journal,
            workspace_id=api.workspace_id,
            artifacts=api.artifacts,
            policies=OrchestrationPolicyService(
                ExtensionYamlStore(app.data_root.root), workspace_id=api.workspace_id
            ),
        )
        service = ManagementService(
            api,
            PreferenceManagementService(api.preference_inbox.writer, api.preference_queries),
            ConfigPatchService(app.project_store, app.global_store, api.workspace_id),
            build_skill_services(app, workspace_id=api.workspace_id, journal=api.journal),
            workflow_feedback=feedback,
        )
        if request_file is None:
            result = service.query(
                kind, scope=scope, task_run_id=task_run_id, agent_run_id=agent_run_id, page=page
            )
        else:
            if kind not in COMMAND_MODELS or request_file.stat().st_size > 1024 * 1024:
                raise ValueError("invalid request")
            request = COMMAND_MODELS[kind].model_validate_json(request_file.read_bytes())
            result = service.execute(kind, request, target=target)
        typer.echo(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except Exception:
        typer.echo("管理操作失败；请检查目标、修订号和请求格式。", err=True)
        raise typer.Exit(code=2) from None
    finally:
        if handle is not None:
            handle.close()


@management_app.command("query")
def management_query(
    kind: str,
    scope: str = "workspace",
    task_run_id: str | None = None,
    agent_run_id: str | None = None,
    page: int = 0,
    workspace_id: str | None = None,
    directory: Path = typer.Option(Path("."), "--dir"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    _run(
        kind,
        request_file=None,
        target=None,
        scope=scope,
        task_run_id=task_run_id,
        agent_run_id=agent_run_id,
        page=page,
        workspace_id=workspace_id,
        directory=directory,
        state_root=state_root,
    )


@management_app.command("command")
def management_command(
    kind: str,
    request_file: Path,
    target: str | None = None,
    workspace_id: str | None = None,
    directory: Path = typer.Option(Path("."), "--dir"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    _run(
        kind,
        request_file=request_file,
        target=target,
        scope="workspace",
        task_run_id=None,
        agent_run_id=None,
        page=0,
        workspace_id=workspace_id,
        directory=directory,
        state_root=state_root,
    )
