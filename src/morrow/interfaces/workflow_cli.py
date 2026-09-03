"""Focused Stage 7 Agent-definition and Workflow CLI surfaces."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import fields, is_dataclass, replace
from datetime import date, datetime
from enum import Enum
from pathlib import Path

import typer
import yaml

from morrow.adapters.local.sandbox import default_sandbox_backend
from morrow.adapters.state.definition_yaml import (
    AgentDefinitionYamlStore,
    WorkflowDefinitionYamlStore,
)
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.agent_definitions.builtins import builtin_definitions
from morrow.application.agent_definitions.publication import (
    AgentDefinitionPublicationService,
    DefinitionCatalog,
)
from morrow.application.workflows.builtins import visible_builtin_workflows
from morrow.application.workflows.finalizer import WorkflowOutcomeFinalizer
from morrow.application.workflows.management import WorkflowManagementService
from morrow.application.workflows.publication import WorkflowCompilationService
from morrow.application.workflows.queries import WorkflowQueryService
from morrow.application.workflows.recovery import WorkflowAbandonService
from morrow.application.workflows.start import StartWorkflowCommand
from morrow.application.workflows.transitions import WorkflowTransitionService
from morrow.bootstrap import build_application, build_session_application
from morrow.core.agent_definitions import AgentDefinitionSource
from morrow.core.agent_runs import AgentDefinitionRef
from morrow.core.capabilities import PermissionPreset, PermissionProfile, ProcessIsolation
from morrow.core.models import ModelRef
from morrow.core.store import StorageError, StorageErrorCode, StoreOpenMode
from morrow.core.workflows.contracts import TaskContract
from morrow.core.workflows.definitions import WorkflowDefinitionSource
from morrow.core.workflows.patches import FutureGraphPatch
from morrow.services.workspace import WorkspaceError

agent_app = typer.Typer(help="Agent definition desired state and immutable publication.")
workflow_app = typer.Typer(help="Static Workflow definition, execution and recovery management.")
node_app = typer.Typer(help="Workflow NodeRun inspection.")
patch_app = typer.Typer(help="Validate, save, and apply exact future-only graph patches.")
workflow_app.add_typer(node_app, name="node")
workflow_app.add_typer(patch_app, name="patch")
_CLI_PERMISSION_PROFILE: ContextVar[PermissionProfile | None] = ContextVar(
    "workflow_cli_permission_profile", default=None
)


@agent_app.callback()
@workflow_app.callback()
def definition_options(
    ctx: typer.Context,
    permission_mode: PermissionPreset = typer.Option(
        PermissionPreset.MANUAL,
        "--permission-mode",
        "--mode",
        help="权限预设：manual、auto-safe、auto-sandboxed 或 full-access-manual。",
    ),
) -> None:
    """Freeze one permission profile for definition publication and Workflow execution."""

    del ctx
    _CLI_PERMISSION_PROFILE.set(PermissionProfile.from_preset(permission_mode))


def _dump(value) -> None:
    typer.echo(json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, allow_nan=False))


def _jsonable(value):
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported CLI JSON value: {type(value).__name__}")


def _workflow_run_exit_code(run) -> int:
    status = getattr(run.status, "value", run.status)
    return 0 if status == "completed" else 1


def _source(path: Path, model):
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return model.model_validate(raw)


def _identity(application, workspace_id, directory):
    if workspace_id is not None:
        for item in application.workspace_service.list():
            if item.workspace_id == workspace_id:
                return item
        raise WorkspaceError("workspace is not registered")
    resolution = application.workspace_service.resolve(directory)
    if resolution.status == "candidate":
        raise WorkspaceError("workspace is not registered; confirm it first or use --workspace-id")
    return resolution.identity


def _permission_profile() -> PermissionProfile:
    return _CLI_PERMISSION_PROFILE.get() or PermissionProfile()


def _native_sandbox(profile: PermissionProfile) -> bool:
    if profile.process_isolation is not ProcessIsolation.NATIVE_SANDBOX:
        return False
    capability = default_sandbox_backend().probe()
    if not capability.supported:
        raise ValueError(
            f"Auto Sandboxed is unavailable ({capability.reason}); refusing Host fallback"
        )
    return True


@contextmanager
def _definition_services(*, state_root, workspace_id, directory, write):
    application = build_application(state_root=state_root)
    identity = _identity(application, workspace_id, directory)
    store = OperationalStore(application.data_root.root)
    try:
        handle = store.open(StoreOpenMode.READ_WRITE if write else StoreOpenMode.READ_ONLY)
    except StorageError as exc:
        if write and exc.code is StorageErrorCode.NOT_FOUND:
            handle = store.initialize()
        else:
            raise
    try:
        journal = SqliteOperationalJournal(handle)
        config = application.global_store.load().value
        active_model = config.active_model if config is not None else None
        models = tuple(
            ModelRef(provider_id=provider_id, model_id=model_id)
            for provider_id, provider in (config.providers.items() if config else ())
            for model_id in provider.models
        )
        profile = _permission_profile()
        native_sandbox = _native_sandbox(profile)
        tool_access = {
            "read": "read",
            "read_artifact": "read",
            "grep": "read",
            "ls": "read",
            "find": "read",
            "edit": "write",
            "write": "write",
            "bash": "write",
        }
        if native_sandbox:
            tool_access["promote_sandbox_changes"] = "write"
        catalog = DefinitionCatalog(
            models=models,
            skill_version_ids=frozenset(
                item.version_id
                for item in journal.list_skill_versions(workspace_id=identity.workspace_id)
            ),
            tool_access=tool_access,
            allowed_tools=frozenset(tool_access),
        )
        agents = AgentDefinitionPublicationService(
            journal,
            workspace_id=identity.workspace_id,
            catalog=catalog,
            id_source=application.id_source,
        )
        compiler = WorkflowCompilationService(
            journal,
            workspace_id=identity.workspace_id,
            catalog=catalog,
            id_source=application.id_source,
        )
        packaged_agents = builtin_definitions(active_model) if active_model is not None else ()
        refs = {}
        for source in packaged_agents:
            head = journal.agent_definitions.get_head(identity.workspace_id, source.definition_id)
            version = (
                journal.agent_definitions.get_version(identity.workspace_id, head.version_id)
                if head is not None
                else None
            )
            if version is not None:
                refs[source.definition_id] = AgentDefinitionRef(
                    definition_id=source.definition_id,
                    version_id=version.version_id,
                    content_hash=version.content_hash,
                )
        packaged_workflows = visible_builtin_workflows(refs, native_sandbox=native_sandbox)
        agent_sources = AgentDefinitionYamlStore(application.data_root.root)
        workflow_sources = WorkflowDefinitionYamlStore(application.data_root.root)
        management = WorkflowManagementService(
            workspace_id=identity.workspace_id,
            agent_sources=agent_sources,
            workflow_sources=workflow_sources,
            agent_publication=agents,
            workflow_publication=compiler,
            runtime=None,
            active_model=active_model,
            agent_builtins=packaged_agents,
            workflow_builtins=packaged_workflows,
        )
        queries = WorkflowQueryService(
            journal,
            workspace_id=identity.workspace_id,
            agent_sources=agent_sources,
            workflow_sources=workflow_sources,
            agent_builtins=packaged_agents,
            workflow_builtins=packaged_workflows,
        )
        yield application, identity, journal, management, queries
    finally:
        handle.close()


def _options(workspace_id, directory, state_root):
    return {"workspace_id": workspace_id, "directory": directory, "state_root": state_root}


def _fail(exc):
    typer.echo(str(exc), err=True)
    raise typer.Exit(code=2) from None


@agent_app.command("list")
def agent_list(
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    try:
        with _definition_services(
            write=False, **_options(workspace_id, directory, state_root)
        ) as ctx:
            _dump(ctx[4].list_agent_definitions())
    except Exception as exc:
        _fail(exc)


@agent_app.command("show")
def agent_show(
    definition_id: str,
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    try:
        with _definition_services(
            write=False, **_options(workspace_id, directory, state_root)
        ) as ctx:
            value = ctx[4].get_agent_definition(definition_id)
            if value is None:
                raise ValueError("Agent definition is missing")
            _dump(value)
    except Exception as exc:
        _fail(exc)


def _agent_write(operation, path, expected, workspace_id, directory, state_root):
    with _definition_services(write=True, **_options(workspace_id, directory, state_root)) as ctx:
        source = _source(path, AgentDefinitionSource)
        method = getattr(ctx[3], f"{operation}_agent_source")
        _dump(method(source, expected_source_revision=expected))


@agent_app.command("create")
def agent_create(
    file: Path = typer.Option(..., "--file", exists=True, dir_okay=False),
    expected_revision: int = typer.Option(..., "--expected-revision", min=0),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    try:
        _agent_write("create", file, expected_revision, workspace_id, directory, state_root)
    except Exception as exc:
        _fail(exc)


@agent_app.command("edit")
def agent_edit(
    file: Path = typer.Option(..., "--file", exists=True, dir_okay=False),
    expected_revision: int = typer.Option(..., "--expected-revision", min=0),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    try:
        _agent_write("update", file, expected_revision, workspace_id, directory, state_root)
    except Exception as exc:
        _fail(exc)


def _agent_action(action, definition_id, expected, command_id, reason, opts):
    with _definition_services(write=action != "validate", **opts) as ctx:
        service = ctx[3]
        if action == "validate":
            _dump(service.validate_agent(definition_id))
        elif action == "publish":
            _dump(
                service.publish_agent(
                    definition_id, expected_head_revision=expected, command_id=command_id
                )
            )
        elif action in {"enable", "disable"}:
            _dump(
                service.set_agent_enabled(
                    definition_id, enabled=action == "enable", expected_head_revision=expected
                )
            )
        else:
            _dump(service.revoke_agent_version(definition_id, reason=reason, command_id=command_id))


def _common_agent_action(
    action, definition_id, expected, command_id, reason, workspace_id, directory, state_root
):
    try:
        _agent_action(
            action,
            definition_id,
            expected,
            command_id,
            reason,
            _options(workspace_id, directory, state_root),
        )
    except Exception as exc:
        _fail(exc)


@agent_app.command("validate")
def agent_validate(
    definition_id: str,
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    _common_agent_action(
        "validate", definition_id, None, None, None, workspace_id, directory, state_root
    )


@agent_app.command("publish")
def agent_publish(
    definition_id: str,
    expected_head_revision: int = typer.Option(..., "--expected-head-revision", min=0),
    command_id: str = typer.Option(..., "--command-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    _common_agent_action(
        "publish",
        definition_id,
        expected_head_revision,
        command_id,
        None,
        workspace_id,
        directory,
        state_root,
    )


def _agent_toggle(enabled, definition_id, expected, workspace_id, directory, state_root):
    _common_agent_action(
        "enable" if enabled else "disable",
        definition_id,
        expected,
        None,
        None,
        workspace_id,
        directory,
        state_root,
    )


@agent_app.command("enable")
def agent_enable(
    definition_id: str,
    expected_head_revision: int = typer.Option(..., "--expected-head-revision", min=1),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    _agent_toggle(True, definition_id, expected_head_revision, workspace_id, directory, state_root)


@agent_app.command("disable")
def agent_disable(
    definition_id: str,
    expected_head_revision: int = typer.Option(..., "--expected-head-revision", min=1),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    _agent_toggle(False, definition_id, expected_head_revision, workspace_id, directory, state_root)


@agent_app.command("revoke")
def agent_revoke(
    version_id: str,
    reason: str = typer.Option(..., "--reason"),
    command_id: str = typer.Option(..., "--command-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    _common_agent_action(
        "revoke", version_id, None, command_id, reason, workspace_id, directory, state_root
    )


@workflow_app.command("list")
def workflow_list(
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    try:
        with _definition_services(
            write=False, **_options(workspace_id, directory, state_root)
        ) as ctx:
            _dump(ctx[4].list_workflow_definitions())
    except Exception as exc:
        _fail(exc)


@workflow_app.command("runs")
def workflow_runs(
    limit: int = typer.Option(100, "--limit", min=1, max=100),
    after: str | None = typer.Option(None, "--after"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    """List durable Workflow runs so interrupted foreground work remains discoverable."""

    try:
        with _definition_services(
            write=False, **_options(workspace_id, directory, state_root)
        ) as ctx:
            _dump(ctx[4].list_runs(limit=limit, after=after))
    except Exception as exc:
        _fail(exc)


@workflow_app.command("show")
def workflow_show(
    definition_id: str,
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    try:
        with _definition_services(
            write=False, **_options(workspace_id, directory, state_root)
        ) as ctx:
            value = ctx[4].get_workflow_definition(definition_id)
            if value is None:
                raise ValueError("Workflow definition is missing")
            _dump(value)
    except Exception as exc:
        _fail(exc)


def _workflow_write(operation, path, expected, workspace_id, directory, state_root):
    with _definition_services(write=True, **_options(workspace_id, directory, state_root)) as ctx:
        source = _source(path, WorkflowDefinitionSource)
        method = getattr(ctx[3], f"{operation}_workflow_source")
        _dump(method(source, expected_source_revision=expected))


@workflow_app.command("create")
def workflow_create(
    file: Path = typer.Option(..., "--file", exists=True, dir_okay=False),
    expected_revision: int = typer.Option(..., "--expected-revision", min=0),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    try:
        _workflow_write("create", file, expected_revision, workspace_id, directory, state_root)
    except Exception as exc:
        _fail(exc)


@workflow_app.command("edit")
def workflow_edit(
    file: Path = typer.Option(..., "--file", exists=True, dir_okay=False),
    expected_revision: int = typer.Option(..., "--expected-revision", min=0),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    try:
        _workflow_write("update", file, expected_revision, workspace_id, directory, state_root)
    except Exception as exc:
        _fail(exc)


def _workflow_action(
    action, identity, expected, command_id, reason, workspace_id, directory, state_root
):
    try:
        with _definition_services(
            write=action != "validate", **_options(workspace_id, directory, state_root)
        ) as ctx:
            service = ctx[3]
            if action == "validate":
                _dump(service.validate_workflow(identity))
            elif action == "publish":
                _dump(
                    service.publish_workflow(
                        identity, expected_head_revision=expected, command_id=command_id
                    )
                )
            elif action in {"enable", "disable"}:
                _dump(
                    service.set_workflow_enabled(
                        identity, enabled=action == "enable", expected_head_revision=expected
                    )
                )
            else:
                _dump(
                    service.revoke_workflow_revision(identity, reason=reason, command_id=command_id)
                )
    except Exception as exc:
        _fail(exc)


@workflow_app.command("validate")
def workflow_validate(
    definition_id: str,
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    _workflow_action(
        "validate", definition_id, None, None, None, workspace_id, directory, state_root
    )


@workflow_app.command("publish")
def workflow_publish(
    definition_id: str,
    expected_head_revision: int = typer.Option(..., "--expected-head-revision", min=0),
    command_id: str = typer.Option(..., "--command-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    _workflow_action(
        "publish",
        definition_id,
        expected_head_revision,
        command_id,
        None,
        workspace_id,
        directory,
        state_root,
    )


@workflow_app.command("enable")
def workflow_enable(
    definition_id: str,
    expected_head_revision: int = typer.Option(..., "--expected-head-revision", min=1),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    _workflow_action(
        "enable",
        definition_id,
        expected_head_revision,
        None,
        None,
        workspace_id,
        directory,
        state_root,
    )


@workflow_app.command("disable")
def workflow_disable(
    definition_id: str,
    expected_head_revision: int = typer.Option(..., "--expected-head-revision", min=1),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    _workflow_action(
        "disable",
        definition_id,
        expected_head_revision,
        None,
        None,
        workspace_id,
        directory,
        state_root,
    )


@workflow_app.command("revoke")
def workflow_revoke(
    revision_id: str,
    reason: str = typer.Option(..., "--reason"),
    command_id: str = typer.Option(..., "--command-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    _workflow_action(
        "revoke", revision_id, None, command_id, reason, workspace_id, directory, state_root
    )


def _session_management(state_root, workspace_id, directory, session_id):
    application = build_application(state_root=state_root)
    identity = _identity(application, workspace_id, directory)
    products = build_session_application(
        application,
        identity,
        resume_session_id=session_id,
        permission_profile=_permission_profile(),
    )
    return products


@workflow_app.command("run")
def workflow_run(
    definition_id: str,
    revision: str | None = typer.Option(None, "--revision"),
    session: str = typer.Option(..., "--session"),
    root_task: str = typer.Option(..., "--root-task"),
    expected_task_version: int = typer.Option(..., "--expected-task-version", min=1),
    task: str | None = typer.Option(None, "--task"),
    stdin: bool = typer.Option(False, "--stdin"),
    command_id: str | None = typer.Option(None, "--command-id"),
    client_message_id: str | None = typer.Option(None, "--client-message-id"),
    ensure_published: bool = typer.Option(False, "--ensure-published"),
    expected_head_revision: int | None = typer.Option(None, "--expected-head-revision", min=0),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    products = None
    run_exit_code = 0
    try:
        if (task is None) == (not stdin):
            raise ValueError("choose exactly one bounded input source: --task TEXT or --stdin")
        if ensure_published and revision is not None:
            raise ValueError("omit --revision when using --ensure-published")
        if not ensure_published and revision is None:
            raise ValueError("plain workflow run requires an exact --revision")
        if ensure_published and expected_head_revision is None:
            raise ValueError("--ensure-published requires --expected-head-revision")
        text = sys.stdin.read() if stdin else task
        products = _session_management(state_root, workspace_id, directory, session)
        command_id = command_id or products.persistence.id_source.new_id("cmd")
        typer.echo(f"command_id: {command_id}")
        publication = None
        if ensure_published:
            publish_command_id = products.persistence.id_source.new_id("cmd")
            typer.echo(f"publish_command_id: {publish_command_id}")
            publication = products.workflow_management.publish_workflow(
                definition_id,
                expected_head_revision=expected_head_revision,
                command_id=publish_command_id,
            )
            revision = publication.revision.workflow_revision_id
            label = "published_revision" if publication.created else "reused_revision"
            typer.echo(f"{label}: {revision}")
        if (
            products.workflow_management.requires_client_message(
                definition_id, revision_id=revision
            )
            and client_message_id is None
        ):
            client_message_id = products.persistence.id_source.new_id("msg")
            typer.echo(f"client_message_id: {client_message_id}")
        command = StartWorkflowCommand(
            workflow_definition_id=definition_id,
            workflow_revision_id=revision,
            session_id=session,
            root_task_run_id=root_task,
            expected_root_row_version=expected_task_version,
            contract=TaskContract(objective=text),
            command_id=command_id,
            client_message_id=client_message_id,
        )
        started = products.workflow_management.start_foreground(command)
        typer.echo(f"workflow_run_id: {started.run.workflow_run_id}")
        result = asyncio.run(products.workflow_management.drive_foreground(started))
        if publication is not None:
            result = replace(result, published=publication.created)
        _dump(result)
        run_exit_code = _workflow_run_exit_code(result.run)
    except Exception as exc:
        _fail(exc)
    finally:
        if products is not None:
            products.persistence.store_session.close()
    if run_exit_code:
        raise typer.Exit(code=run_exit_code)


def _runtime_for_run(state_root, workspace_id, directory, workflow_run_id):
    with _definition_services(write=False, **_options(workspace_id, directory, state_root)) as ctx:
        recovery = ctx[4].get_run_recovery_view(workflow_run_id)
        if recovery is None:
            raise ValueError("Workflow run is missing")
    return _session_management(state_root, workspace_id, directory, recovery.session_id)


def _patch_action(
    action,
    workflow_run_id,
    base_revision,
    expected_parent_version,
    file,
    patch_id,
    requested_by,
    workspace_id,
    directory,
    state_root,
):
    products = None
    try:
        products = _runtime_for_run(state_root, workspace_id, directory, workflow_run_id)
        patch = FutureGraphPatch(
            workflow_patch_id=patch_id,
            workspace_id=products.persistence.workspace_id,
            parent_run_id=workflow_run_id,
            base_workflow_revision_id=base_revision,
            expected_parent_row_version=expected_parent_version,
            source=_source(file, WorkflowDefinitionSource),
            requested_by=requested_by,
        )
        _dump(getattr(products.workflow_management, f"{action}_patch")(patch))
    except Exception as exc:
        _fail(exc)
    finally:
        if products is not None:
            products.persistence.store_session.close()


def _patch_options(
    action,
    workflow_run_id,
    base_revision,
    expected_parent_version,
    file,
    patch_id,
    requested_by,
    workspace_id,
    directory,
    state_root,
):
    _patch_action(
        action,
        workflow_run_id,
        base_revision,
        expected_parent_version,
        file,
        patch_id,
        requested_by,
        workspace_id,
        directory,
        state_root,
    )


@patch_app.command("validate")
def workflow_patch_validate(
    workflow_run_id: str,
    base_revision: str = typer.Option(..., "--base-revision"),
    expected_parent_version: int = typer.Option(..., "--expected-parent-version", min=1),
    file: Path = typer.Option(..., "--file", exists=True, dir_okay=False),
    patch_id: str = typer.Option(..., "--patch-id"),
    requested_by: str = typer.Option(..., "--requested-by"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    _patch_options(
        "validate",
        workflow_run_id,
        base_revision,
        expected_parent_version,
        file,
        patch_id,
        requested_by,
        workspace_id,
        directory,
        state_root,
    )


@patch_app.command("save")
def workflow_patch_save(
    workflow_run_id: str,
    base_revision: str = typer.Option(..., "--base-revision"),
    expected_parent_version: int = typer.Option(..., "--expected-parent-version", min=1),
    file: Path = typer.Option(..., "--file", exists=True, dir_okay=False),
    patch_id: str = typer.Option(..., "--patch-id"),
    requested_by: str = typer.Option(..., "--requested-by"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    _patch_options(
        "save",
        workflow_run_id,
        base_revision,
        expected_parent_version,
        file,
        patch_id,
        requested_by,
        workspace_id,
        directory,
        state_root,
    )


@patch_app.command("apply")
def workflow_patch_apply(
    workflow_run_id: str,
    base_revision: str = typer.Option(..., "--base-revision"),
    expected_parent_version: int = typer.Option(..., "--expected-parent-version", min=1),
    file: Path = typer.Option(..., "--file", exists=True, dir_okay=False),
    patch_id: str = typer.Option(..., "--patch-id"),
    requested_by: str = typer.Option(..., "--requested-by"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    _patch_options(
        "apply",
        workflow_run_id,
        base_revision,
        expected_parent_version,
        file,
        patch_id,
        requested_by,
        workspace_id,
        directory,
        state_root,
    )


@contextmanager
def _control_service(*, state_root, workspace_id, directory):
    """Minimal Workflow control surface: the transition owner, nothing else."""

    application = build_application(state_root=state_root)
    identity = _identity(application, workspace_id, directory)
    store = OperationalStore(application.data_root.root)
    try:
        handle = store.open(StoreOpenMode.READ_WRITE)
    except StorageError as exc:
        if exc.code is StorageErrorCode.NOT_FOUND:
            handle = store.initialize()
        else:
            raise
    try:
        journal = SqliteOperationalJournal(handle)
        yield WorkflowTransitionService(
            journal, workspace_id=identity.workspace_id, clock=journal.now
        )
    finally:
        handle.close()


@contextmanager
def _abandon_service(*, state_root, workspace_id, directory):
    application = build_application(state_root=state_root)
    identity = _identity(application, workspace_id, directory)
    store = OperationalStore(application.data_root.root)
    try:
        handle = store.open(StoreOpenMode.READ_WRITE)
    except StorageError as exc:
        if exc.code is StorageErrorCode.NOT_FOUND:
            handle = store.initialize()
        else:
            raise
    try:
        journal = SqliteOperationalJournal(handle)
        transitions = WorkflowTransitionService(
            journal, workspace_id=identity.workspace_id, clock=journal.now
        )
        finalizer = WorkflowOutcomeFinalizer(
            journal,
            workspace_id=identity.workspace_id,
            transitions=transitions,
            id_source=application.id_source,
            clock=journal.now,
        )
        yield WorkflowAbandonService(transitions=transitions, finalizer=finalizer)
    finally:
        handle.close()


@workflow_app.command("status")
def workflow_status(
    workflow_run_id: str,
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    try:
        with _definition_services(
            write=False, **_options(workspace_id, directory, state_root)
        ) as ctx:
            value = ctx[4].get_run_view(workflow_run_id)
            if value is None:
                raise ValueError("Workflow run is missing")
            _dump(value)
    except Exception as exc:
        _fail(exc)


@workflow_app.command("rerun")
def workflow_rerun(
    workflow_run_id: str,
    full: bool = typer.Option(
        False,
        "--full",
        help="Execute the full graph without inherited outputs; otherwise retry failed nodes.",
    ),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    """Create a new-budget rerun after explicitly resuming the root TaskRun."""

    products = None
    try:
        products = _runtime_for_run(state_root, workspace_id, directory, workflow_run_id)
        result = products.workflow_management.rerun(workflow_run_id, full=full)
        typer.echo(f"new_budget_root: {result.child.workflow_run_id}")
        _dump(result)
    except Exception as exc:
        _fail(exc)
    finally:
        if products is not None:
            products.persistence.store_session.close()


@workflow_app.command("pause")
def workflow_pause(
    workflow_run_id: str,
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    """Record the durable pause fact; a running Workflow drains, then pauses."""

    try:
        with _control_service(**_options(workspace_id, directory, state_root)) as transitions:
            _dump(transitions.request_pause(workflow_run_id))
    except Exception as exc:
        _fail(exc)


@workflow_app.command("resume")
def workflow_resume(
    workflow_run_id: str,
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    products = None
    try:
        products = _runtime_for_run(state_root, workspace_id, directory, workflow_run_id)
        _dump(asyncio.run(products.workflow_management.resume(workflow_run_id)))
    except Exception as exc:
        _fail(exc)
    finally:
        if products is not None:
            products.persistence.store_session.close()


@workflow_app.command("abandon")
def workflow_abandon(
    workflow_run_id: str,
    expected_row_version: int = typer.Option(..., "--expected-row-version", min=1),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    try:
        with _abandon_service(**_options(workspace_id, directory, state_root)) as service:
            _dump(
                service.abandon(
                    workflow_run_id,
                    expected_row_version=expected_row_version,
                )
            )
    except Exception as exc:
        _fail(exc)


@node_app.command("show")
def node_show(
    node_run_id: str,
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
):
    try:
        with _definition_services(
            write=False, **_options(workspace_id, directory, state_root)
        ) as ctx:
            value = ctx[4].get_node_view(node_run_id)
            if value is None:
                raise ValueError("Workflow node is missing")
            _dump(value)
    except Exception as exc:
        _fail(exc)
