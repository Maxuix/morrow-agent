"""Composition root.  Concrete infrastructure is assembled here only."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from morrow.adapters.credentials.keyring import CredentialAccessError, KeyringCredentialStore
from morrow.adapters.local.sandbox import (
    NativeSandboxProcessAdapter,
    default_sandbox_backend,
)
from morrow.adapters.models.openai_compatible import estimate_request_chars, make_openai_compatible
from morrow.adapters.registry import AdapterRegistry
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.adapters.state.yaml import (
    GlobalConfigYamlStore,
    ProjectStateYamlStore,
    WorkspaceIndexYamlStore,
)
from morrow.application.commands import CommandService
from morrow.application.configuration import make_configuration_tool
from morrow.application.context import ContextBuilder
from morrow.application.local_tools import (
    make_apply_patch_tool,
    make_git_diff_tool,
    make_git_status_tool,
    make_promote_sandbox_tool,
    make_read_search_tools,
    make_run_command_tool,
    make_show_changes_tool,
    make_write_file_tool,
)
from morrow.application.orchestrator import SessionOrchestrator
from morrow.application.turns import SessionPersistence
from morrow.core.capabilities import PermissionProfile, ProcessIsolation, WorkspaceCapability
from morrow.core.domain import DurableSession
from morrow.core.execution import missing_declarations
from morrow.core.models import Preferences
from morrow.core.store import StorageError, StorageErrorCode, StoreOpenMode
from morrow.runtime.agent import AgentRuntime
from morrow.runtime.capabilities import CapabilityPolicy
from morrow.runtime.ids import RandomIdSource
from morrow.runtime.policy import AgentPolicy, load_agent_policy
from morrow.runtime.session import Session
from morrow.runtime.tools import ToolExecutor, ToolRegistry
from morrow.services.changes import ChangeSetService
from morrow.services.files import (
    WorkspaceFileService,
    WorkspaceMutationService,
    WorkspacePathResolver,
)
from morrow.services.git import GitInspectionService
from morrow.services.preferences import ConfigPatchService
from morrow.services.process import ProcessExecutionService
from morrow.services.provider import ProviderService
from morrow.services.sandbox import SandboxSnapshotService
from morrow.services.search import WorkspaceSearchService
from morrow.services.workspace import DataRoot, WorkspaceService, WorkspaceStateService


@dataclass
class Application:
    data_root: DataRoot
    global_store: GlobalConfigYamlStore
    index_store: WorkspaceIndexYamlStore
    project_store: ProjectStateYamlStore
    provider_service: ProviderService
    workspace_service: WorkspaceService
    workspace_state_service: WorkspaceStateService
    registry: AdapterRegistry
    credentials: object
    id_source: object
    agent_policy: AgentPolicy


@dataclass
class SessionApplication:
    session: Session
    context_builder: ContextBuilder
    commands: CommandService
    orchestrator: SessionOrchestrator
    files: WorkspaceFileService
    search: WorkspaceSearchService
    mutation: WorkspaceMutationService
    changes: ChangeSetService
    process: ProcessExecutionService
    git: GitInspectionService
    sandbox_capability: object
    persistence: object | None = None
    tasks: object | None = None


def _default_tool_executor(
    run_policy,
    *,
    config_service=None,
    approval_port=None,
    capability_policy=None,
    files: WorkspaceFileService,
    search: WorkspaceSearchService,
    mutation: WorkspaceMutationService,
    changes: ChangeSetService,
    process: ProcessExecutionService,
    git: GitInspectionService,
    sandbox: SandboxSnapshotService | None = None,
    sandbox_enabled: bool = False,
    process_isolation: ProcessIsolation = ProcessIsolation.HOST,
) -> ToolExecutor:
    registry = ToolRegistry()
    if config_service is not None:
        registry.register(make_configuration_tool(config_service))
    for tool in make_read_search_tools(files, search):
        registry.register(tool)
    registry.register(make_apply_patch_tool(mutation, changes))
    registry.register(make_write_file_tool(mutation, changes))
    registry.register(make_show_changes_tool(changes))
    registry.register(make_run_command_tool(process))
    for tool in (make_git_status_tool(git), make_git_diff_tool(git)):
        registry.register(tool)
    if sandbox is not None and process.requires_sandbox and sandbox_enabled:
        registry.register(make_promote_sandbox_tool(sandbox, mutation, changes))
    names = tuple(tool.function.name for tool in registry.definitions())
    missing = missing_declarations(names, process_isolation=process_isolation)
    if missing:
        raise RuntimeError("registered tools lack durable declarations: " + ", ".join(missing))
    return ToolExecutor(
        registry.snapshot(),
        run_policy,
        approval_port=approval_port,
        capability_policy=capability_policy,
    )


def build_application(
    *, state_root: Path | None = None, credentials=None, id_source=None
) -> Application:
    data_root = DataRoot(state_root)
    data_root.ensure()
    global_store = GlobalConfigYamlStore(data_root.root)
    index_store = WorkspaceIndexYamlStore(data_root.root)
    project_store = ProjectStateYamlStore(data_root.root)
    registry = AdapterRegistry()
    registry.register(
        "openai-compatible",
        make_openai_compatible,
        tool_protocol="openai_function",
        multiple_tool_calls=True,
    )
    credential_store = credentials or KeyringCredentialStore()
    application_id_source = id_source or RandomIdSource()
    provider_service = ProviderService(global_store, credential_store, registry)
    workspace_service = WorkspaceService(
        data_root,
        index_store,
        id_source=application_id_source,
    )
    return Application(
        data_root,
        global_store,
        index_store,
        project_store,
        provider_service,
        workspace_service,
        WorkspaceStateService(project_store),
        registry,
        credential_store,
        application_id_source,
        load_agent_policy(),
    )


def _open_operational_store(app: Application):
    store = OperationalStore(app.data_root.root)
    try:
        return store.open(StoreOpenMode.READ_WRITE)
    except StorageError as exc:
        if exc.code is StorageErrorCode.NOT_FOUND:
            return store.initialize()
        raise


def build_session_application(
    app: Application,
    identity,
    *,
    provider=None,
    model=None,
    approval_port=None,
    permission_profile: PermissionProfile | None = None,
    metrics_enabled: bool = True,
    resume_session_id: str | None = None,
):
    inspection = app.workspace_state_service.inspect(identity.workspace_id)
    profile_result = inspection.profile
    preferences_result = inspection.preferences
    config = app.global_store.load().value
    permission_profile = permission_profile or PermissionProfile()
    workspace_capability = WorkspaceCapability(
        workspace_id=identity.workspace_id,
        root=Path(identity.path),
        read_only=inspection.read_only,
    )
    session = Session(
        session_id=resume_session_id or app.id_source.new_id("ses"),
        profile=(
            profile_result.value.profile
            if profile_result.value and not inspection.read_only
            else None
        ),
        global_preferences=config.preferences if config else Preferences(),
        workspace_preferences=preferences_result.value.preferences
        if preferences_result.value
        else Preferences(),
        read_only=inspection.read_only,
        workspace_preferences_read_only=inspection.preferences_read_only,
        permission_profile=permission_profile,
        workspace_capability=workspace_capability,
        metrics_enabled=metrics_enabled,
        profile_revision=profile_result.revision or 0,
        preferences_revision=preferences_result.revision or 0,
    )
    files = WorkspaceFileService(WorkspacePathResolver(workspace_capability.root))
    search = WorkspaceSearchService(files)
    mutation = WorkspaceMutationService(files)
    changes = ChangeSetService()
    git = GitInspectionService(files)
    sandbox_backend = default_sandbox_backend()
    sandbox_capability = sandbox_backend.probe()
    sandbox = SandboxSnapshotService(files)
    config_service = ConfigPatchService(
        app.project_store, app.global_store, identity.workspace_id, session
    )
    if provider is None or model is None:
        provider, model = app.provider_service.build_active()
    provider_config = config.providers.get(model.provider_id) if config else None
    try:
        active_credential = (
            app.provider_service.credential_resolver(
                model.provider_id, provider_config.credential_ref
            )
            if provider_config is not None
            else None
        )
    except CredentialAccessError as exc:
        raise ValueError(exc.message) from None
    if permission_profile.process_isolation is ProcessIsolation.NATIVE_SANDBOX:
        process = ProcessExecutionService(
            files,
            adapter=NativeSandboxProcessAdapter(
                workspace_capability.root,
                sandbox,
                sandbox_backend,
            ),
            secrets=(active_credential,) if active_credential else (),
            requires_host=False,
            requires_sandbox=True,
        )
    else:
        process = ProcessExecutionService(
            files,
            secrets=(active_credential,) if active_credential else (),
        )
    capability_policy = CapabilityPolicy(
        permission_profile,
        workspace_capability,
        sandbox_available=sandbox_capability.supported,
    )
    adapter_id = provider_config.adapter if provider_config else "openai-compatible"
    adapter_support = app.registry.tool_support(adapter_id)
    run_policy = app.agent_policy.resolve(
        model,
        tool_protocol=adapter_support.tool_protocol,
        multiple_tool_calls=adapter_support.multiple_tool_calls,
    )
    context_builder = ContextBuilder(
        run_policy=run_policy,
        estimate_request_chars=estimate_request_chars,
    )
    tool_executor = (
        _default_tool_executor(
            run_policy,
            config_service=config_service,
            approval_port=approval_port,
            capability_policy=capability_policy,
            files=files,
            search=search,
            mutation=mutation,
            changes=changes,
            process=process,
            git=git,
            sandbox=sandbox,
            sandbox_enabled=sandbox_capability.supported,
            process_isolation=permission_profile.process_isolation,
        )
        if adapter_support.tool_protocol == "openai_function"
        else None
    )
    runtime = AgentRuntime(
        provider,
        model,
        context_builder,
        id_source=app.id_source,
        tool_executor=tool_executor,
    )
    handle = _open_operational_store(app)
    journal = SqliteOperationalJournal(handle)
    persistence = SessionPersistence(
        workspace_id=identity.workspace_id,
        journal=journal,
        store_session=handle,
        id_source=app.id_source,
        model=model,
        run_policy=run_policy,
        runtime_instance_id=f"inst-{os.getpid()}",
        mutation=mutation,
    )
    if resume_session_id:
        persistence.restore_into(session)
    else:
        if journal.get_session(identity.workspace_id, session.session_id) is None:
            journal.create_session(
                DurableSession(session_id=session.session_id, workspace_id=identity.workspace_id)
            )
        persistence.attach(session)
    commands = CommandService(
        session=session,
        identity=identity,
        project_store=app.project_store,
        config_service=config_service,
        task_service=persistence.tasks,
        id_source=app.id_source,
    )
    orchestrator = SessionOrchestrator(
        session=session,
        runtime=runtime,
        command_service=commands,
        context_builder=context_builder,
        id_source=app.id_source,
    )
    return SessionApplication(
        session=session,
        context_builder=context_builder,
        commands=commands,
        orchestrator=orchestrator,
        files=files,
        search=search,
        mutation=mutation,
        changes=changes,
        process=process,
        git=git,
        sandbox_capability=sandbox_capability,
        persistence=persistence,
        tasks=persistence.tasks,
    )
