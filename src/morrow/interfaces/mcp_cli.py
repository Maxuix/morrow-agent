"""Typer control-plane commands for MCP desired state and Catalogs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from pydantic import ValidationError

from morrow.adapters.state.extension_yaml import ExtensionYamlStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.mcp.catalog import McpCatalogError, McpCatalogService
from morrow.application.mcp.definitions import McpDefinitionError, McpDefinitionService
from morrow.application.mcp.queries import McpQueries, McpQueryError, project_server
from morrow.bootstrap import build_application
from morrow.core.mcp import (
    McpApprovalMode,
    McpCwdPolicy,
    McpLaunchRisk,
    McpServerDefinition,
    McpToolPolicy,
    McpToolRiskMapping,
    McpWorkspaceVisibility,
)
from morrow.core.models import ToolEffect
from morrow.core.store import StorageError, StorageErrorCode, StoreOpenMode

mcp_app = typer.Typer(help="MCP Server desired state、Catalog 与安全映射管理。")


def _open_journal(application):
    store = OperationalStore(application.data_root.root)
    try:
        handle = store.open(StoreOpenMode.READ_WRITE)
    except StorageError as exc:
        if exc.code is not StorageErrorCode.NOT_FOUND:
            raise
        handle = store.initialize()
    return handle, SqliteOperationalJournal(handle)


def _scope(workspace: str | None) -> tuple[str, str | None]:
    return ("workspace", workspace) if workspace else ("global", None)


def _parse_pairs(values: list[str] | None, *, label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values or []:
        name, separator, setting = value.partition("=")
        if not separator or not name or not setting or name in result:
            raise typer.BadParameter(f"{label} must use unique name=value entries")
        result[name] = setting
    return result


def _definition_service(application, workspace: str | None) -> McpDefinitionService:
    return McpDefinitionService(ExtensionYamlStore(application.data_root.root), workspace)


def _catalogs(journal, definitions, scope: str, scope_id: str | None):
    values = {}
    for item in definitions.list(scope, scope_id=scope_id):
        catalog = journal.get_mcp_catalog(scope, item.server_id, scope_id=scope_id)
        if catalog is not None:
            values[item.server_id] = catalog
    return values


def _emit(value, *, as_json: bool) -> None:
    if as_json:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        typer.echo(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return
    if hasattr(value, "model_dump"):
        for key, item in value.model_dump(mode="json").items():
            if isinstance(item, (list, dict)):
                item = json.dumps(item, ensure_ascii=False, sort_keys=True)
            typer.echo(f"{key}: {item}")
    else:
        typer.echo(str(value))


def _error(exc: Exception) -> None:
    if isinstance(exc, ValidationError):
        issue = exc.errors(include_url=False, include_context=False, include_input=False)[0]
        location = ".".join(str(item) for item in issue["loc"]) or "configuration"
        typer.echo(f"MCP 操作失败（invalid_configuration）：{location}: {issue['msg']}", err=True)
        return
    code = getattr(exc, "code", type(exc).__name__)
    message = getattr(exc, "message", "MCP operation failed")
    typer.echo(f"MCP 操作失败（{code}）：{message}", err=True)


@mcp_app.command("add")
def mcp_add(
    server_id: str,
    executable: str = typer.Option(..., "--executable", "--command"),
    name: str | None = typer.Option(None, "--name"),
    argument: list[str] | None = typer.Option(None, "--arg"),
    workspace: str | None = typer.Option(None, "--workspace", "--workspace-id"),
    cwd_policy: str = typer.Option("workspace", "--cwd-policy"),
    cwd: str | None = typer.Option(None, "--cwd"),
    timeout_ms: int = typer.Option(15_000, "--timeout-ms", min=100, max=300_000),
    credential_ref: list[str] | None = typer.Option(None, "--credential-ref"),
    allow_tool: list[str] | None = typer.Option(None, "--allow-tool"),
    tool_effect: list[str] | None = typer.Option(None, "--tool-effect"),
    tool_approval: list[str] | None = typer.Option(None, "--tool-approval"),
    launch_risk: list[str] | None = typer.Option(None, "--launch-risk"),
    visibility: str = typer.Option("none", "--visibility"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    application = build_application(state_root=state_root)
    scope, scope_id = _scope(workspace)
    try:
        effects = _parse_pairs(tool_effect, label="--tool-effect")
        approvals = _parse_pairs(tool_approval, label="--tool-approval")
        allowlist = tuple(allow_tool or ())
        if set(effects) - set(allowlist) or set(approvals) - set(allowlist):
            raise typer.BadParameter("tool mappings must target --allow-tool entries")
        mappings = tuple(
            McpToolRiskMapping(
                remote_name=remote_name,
                effect=ToolEffect(effect),
                approval=McpApprovalMode(approvals.get(remote_name, "require_approval")),
            )
            for remote_name, effect in effects.items()
        )
        risks = tuple(McpLaunchRisk(value) for value in (launch_risk or ()))
        definition = McpServerDefinition(
            server_id=server_id,
            display_name=name or server_id,
            executable=executable,
            argv=tuple(argument or ()),
            cwd_policy=McpCwdPolicy(cwd_policy),
            cwd=cwd,
            timeout_ms=timeout_ms,
            credential_refs=tuple(credential_ref or ()),
            workspace_visibility=McpWorkspaceVisibility(visibility),
            requested_launch_risks=risks,
            tool_policy=McpToolPolicy(allowlist=allowlist, mappings=mappings),
            scope=scope,
            scope_id=scope_id,
            enabled=False,
        )
        handle, _journal = _open_journal(application)
        handle.close()
        _definition_service(application, workspace).add(definition, scope=scope, scope_id=scope_id)
        typer.echo(f"已添加 MCP Server：{server_id}；enabled: false")
    except (McpDefinitionError, StorageError, ValueError, typer.BadParameter) as exc:
        _error(exc)
        raise typer.Exit(code=2) from None


def _query(
    application,
    workspace: str | None,
):
    scope, scope_id = _scope(workspace)
    definitions = _definition_service(application, workspace)
    if not definitions.list(scope, scope_id=scope_id):
        return None, McpQueries(definitions, catalogs={}), scope, scope_id
    store = OperationalStore(application.data_root.root)
    try:
        handle = store.open(StoreOpenMode.READ_ONLY)
    except StorageError as exc:
        if exc.code is not StorageErrorCode.NOT_FOUND:
            raise
        initialized, _journal = _open_journal(application)
        initialized.close()
        handle = store.open(StoreOpenMode.READ_ONLY)
    journal = SqliteOperationalJournal(handle)
    catalogs = _catalogs(journal, definitions, scope, scope_id)
    return handle, McpQueries(definitions, catalogs=catalogs), scope, scope_id


@mcp_app.command("list")
def mcp_list(
    workspace: str | None = typer.Option(None, "--workspace", "--workspace-id"),
    as_json: bool = typer.Option(False, "--json"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    application = build_application(state_root=state_root)
    handle = None
    try:
        handle, queries, scope, scope_id = _query(application, workspace)
        values = queries.list(scope, scope_id=scope_id)
        if as_json:
            _emit([item.model_dump(mode="json") for item in values], as_json=True)
        else:
            for item in values:
                typer.echo(
                    f"{item.server_id}\t{item.catalog_status.value}\t"
                    f"{'enabled' if item.enabled else 'disabled'}\ttools={item.tool_count}"
                )
    except (McpQueryError, McpDefinitionError, StorageError, ValueError) as exc:
        _error(exc)
        raise typer.Exit(code=2) from None
    finally:
        if handle is not None:
            handle.close()


def _show_command(
    command: str,
    server_id: str,
    workspace: str | None,
    as_json: bool,
    state_root: Path | None,
) -> None:
    application = build_application(state_root=state_root)
    handle = None
    try:
        handle, queries, scope, scope_id = _query(application, workspace)
        value = getattr(queries, command)(server_id, scope, scope_id=scope_id)
        _emit(value, as_json=as_json)
    except (McpQueryError, McpDefinitionError, StorageError, ValueError) as exc:
        _error(exc)
        raise typer.Exit(code=2) from None
    finally:
        if handle is not None:
            handle.close()


@mcp_app.command("show")
def mcp_show(
    server_id: str,
    workspace: str | None = typer.Option(None, "--workspace", "--workspace-id"),
    as_json: bool = typer.Option(False, "--json"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _show_command("show", server_id, workspace, as_json, state_root)


@mcp_app.command("inspect")
def mcp_inspect(
    server_id: str,
    workspace: str | None = typer.Option(None, "--workspace", "--workspace-id"),
    as_json: bool = typer.Option(False, "--json"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _show_command("inspect", server_id, workspace, as_json, state_root)


@mcp_app.command("status")
def mcp_status(
    server_id: str,
    workspace: str | None = typer.Option(None, "--workspace", "--workspace-id"),
    as_json: bool = typer.Option(False, "--json"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _show_command("status", server_id, workspace, as_json, state_root)


def _mutate(
    operation: str,
    server_id: str,
    workspace: str | None,
    state_root: Path | None,
    catalog: object | None = None,
) -> None:
    application = build_application(state_root=state_root)
    handle = None
    try:
        scope, scope_id = _scope(workspace)
        handle, journal = _open_journal(application)
        service = _definition_service(application, workspace)
        if operation == "enable":
            catalog = journal.get_mcp_catalog(scope, server_id, scope_id=scope_id)
            service.enable(server_id, scope=scope, scope_id=scope_id, catalog=catalog)
        else:
            getattr(service, operation)(server_id, scope=scope, scope_id=scope_id)
        typer.echo(f"{operation}: {server_id}")
    except (McpDefinitionError, StorageError, ValueError) as exc:
        _error(exc)
        raise typer.Exit(code=2) from None
    finally:
        if handle is not None:
            handle.close()


@mcp_app.command("enable")
def mcp_enable(
    server_id: str,
    workspace: str | None = typer.Option(None, "--workspace", "--workspace-id"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _mutate("enable", server_id, workspace, state_root)


@mcp_app.command("disable")
def mcp_disable(
    server_id: str,
    workspace: str | None = typer.Option(None, "--workspace", "--workspace-id"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _mutate("disable", server_id, workspace, state_root)


@mcp_app.command("remove")
def mcp_remove(
    server_id: str,
    workspace: str | None = typer.Option(None, "--workspace", "--workspace-id"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _mutate("remove", server_id, workspace, state_root)


@mcp_app.command("refresh")
def mcp_refresh(
    server_id: str,
    workspace: str | None = typer.Option(None, "--workspace", "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    as_json: bool = typer.Option(False, "--json"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    application = build_application(state_root=state_root)
    handle = None
    try:
        scope, scope_id = _scope(workspace)
        handle, journal = _open_journal(application)
        service = _definition_service(application, workspace)
        definition = service.show(server_id, scope, scope_id=scope_id)
        previous = journal.get_mcp_catalog(scope, server_id, scope_id=scope_id)
        catalog = asyncio.run(
            McpCatalogService(workspace_root=directory.resolve()).refresh(
                definition, previous=previous
            )
        )
        journal.put_mcp_server(definition, catalog=catalog)
        _emit(project_server(definition, catalog), as_json=as_json)
    except (McpCatalogError, McpDefinitionError, StorageError, ValueError) as exc:
        _error(exc)
        raise typer.Exit(code=2) from None
    finally:
        if handle is not None:
            handle.close()


__all__ = ["mcp_app"]
