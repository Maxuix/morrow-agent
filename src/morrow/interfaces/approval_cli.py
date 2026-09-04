"""Approval CLI: the same §8.5 surface and resolution services as the GUI."""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Literal

import typer

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.bootstrap import build_application, build_session_application
from morrow.core.capabilities import PermissionProfile
from morrow.core.execution import (
    ApprovalResolution,
    ToolExecutionState,
    session_granted_scope,
    session_scope_allowed,
)
from morrow.core.store import StorageError, StorageErrorCode, StoreOpenMode
from morrow.server.projections import approval_wire
from morrow.services.workspace import WorkspaceError

approval_app = typer.Typer(help="审批查看与决议（与 GUI 使用同一应用服务）。")


def _jsonable(value):
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _echo(value) -> None:
    typer.echo(json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, allow_nan=False))


def _fail(exc) -> None:
    typer.echo(str(exc), err=True)
    raise typer.Exit(code=2) from None


def _identity(application, workspace_id, directory):
    if workspace_id is not None:
        identity = application.workspace_service.get(workspace_id)
        if identity is None:
            raise WorkspaceError("workspace is not registered")
        return identity
    resolution = application.workspace_service.resolve(directory)
    if resolution.status == "candidate":
        raise WorkspaceError("workspace is not registered; confirm it first or use --workspace-id")
    return resolution.identity


def _open_journal(application, *, write: bool):
    store = OperationalStore(application.data_root.root)
    mode = StoreOpenMode.READ_WRITE if write else StoreOpenMode.READ_ONLY
    try:
        handle = store.open(mode)
    except StorageError as exc:
        if write and exc.code is StorageErrorCode.NOT_FOUND:
            handle = store.initialize()
        else:
            raise
    return handle, SqliteOperationalJournal(handle)


def _list_approvals(journal, workspace_id: str, *, pending_only: bool) -> list[dict]:
    rows = []
    for session in journal.list_sessions(workspace_id):
        for execution in journal.list_session_executions(workspace_id, session.session_id):
            if pending_only and execution.state is not ToolExecutionState.AWAITING_APPROVAL:
                continue
            approval = journal.get_approval_for_execution(workspace_id, execution.tool_execution_id)
            if approval is None:
                continue
            if pending_only and approval.resolution is not ApprovalResolution.PENDING:
                continue
            agent_run = journal.get_agent_run(workspace_id, execution.agent_run_id)
            rows.append(approval_wire(approval, execution, agent_run))
    rows.sort(key=lambda item: item["created_at"])
    return rows


@approval_app.command("list")
def approval_list(
    include_resolved: bool = typer.Option(False, "--all", help="Include resolved approvals."),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    """List pending approvals with requester, operation, risk and redacted preview."""

    handle = None
    try:
        application = build_application(state_root=state_root)
        identity = _identity(application, workspace_id, directory)
        handle, journal = _open_journal(application, write=False)
        _echo(
            {
                "approvals": _list_approvals(
                    journal, identity.workspace_id, pending_only=not include_resolved
                )
            }
        )
    except Exception as exc:
        _fail(exc)
    finally:
        if handle is not None:
            handle.close()


@approval_app.command("resolve")
def approval_resolve(
    approval_id: str,
    decision: Literal["allow-once", "deny", "allow-session"] = typer.Option(
        ..., "--decision", help="allow-once / deny / allow-session（会话内同范围免批）。"
    ),
    command_id: str | None = typer.Option(None, "--command-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    """Resolve a pending approval durably; a live owner observes the committed decision."""

    products = None
    handle = None
    try:
        application = build_application(state_root=state_root)
        identity = _identity(application, workspace_id, directory)
        handle, journal = _open_journal(application, write=False)
        approval = journal.get_approval(identity.workspace_id, approval_id)
        if approval is None:
            raise ValueError("approval is missing")
        execution = journal.get_execution(identity.workspace_id, approval.tool_execution_id)
        if execution is None:
            raise ValueError("approval execution is missing")
        handle.close()
        handle = None

        approved = decision != "deny"
        granted_scope = None
        if decision == "allow-session":
            if not session_scope_allowed(execution.intent.effect_class, execution.isolation):
                raise ValueError("session-scoped approval is not offered for high-risk operations")
            granted_scope = session_granted_scope(approval.requested_scope)

        products = build_session_application(
            application,
            identity,
            resume_session_id=execution.session_id,
            permission_profile=PermissionProfile(),
        )
        resolved = products.api.resolve_approval(
            execution,
            approval,
            approved=approved,
            command_id=command_id,
            granted_scope=granted_scope,
        )
        _saved_execution, saved_approval, did_execute = resolved.value
        _echo(
            {
                "approval_id": saved_approval.approval_id,
                "resolution": saved_approval.resolution,
                "granted_scope": saved_approval.granted_scope,
                "executed": did_execute,
            }
        )
    except Exception as exc:
        _fail(exc)
    finally:
        if handle is not None:
            handle.close()
        if products is not None:
            products.persistence.store_session.close()
