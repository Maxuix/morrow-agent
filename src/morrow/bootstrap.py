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
from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore, OperationalStoreSession
from morrow.adapters.state.yaml import (
    GlobalConfigYamlStore,
    ProjectStateYamlStore,
    WorkspaceIndexYamlStore,
)
from morrow.application.api import OperationalApplicationService
from morrow.application.artifacts import ArtifactService
from morrow.application.backup import OperationalBackupService
from morrow.application.checkpoints import ContextCheckpointService, SessionForkService
from morrow.application.commands import CommandService
from morrow.application.configuration import make_configuration_tool
from morrow.application.context import ContextBuilder
from morrow.application.doctor import OperationalDoctor
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
from morrow.application.recovery import RecoveryService
from morrow.application.tasks import TaskService
from morrow.application.turns import SessionPersistence
from morrow.core.capabilities import (
    AccessScope,
    ApprovalMode,
    PermissionProfile,
    ProcessIsolation,
    WorkspaceCapability,
)
from morrow.core.domain import DurableSession, SessionLifecycle
from morrow.core.execution import missing_declarations
from morrow.core.models import Preferences
from morrow.core.permissions import UNCONFINED_HOST_WARNING_DIGEST, CapabilityName
from morrow.core.store import (
    StorageError,
    StorageErrorCode,
    StoreOpenMode,
)
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
    artifacts: ArtifactService | None = None
    checkpoints: ContextCheckpointService | None = None
    forks: SessionForkService | None = None
    api: OperationalApplicationService | None = None
    doctor: OperationalDoctor | None = None
    backup: OperationalBackupService | None = None


@dataclass(frozen=True)
class OperationalServices:
    """Provider-independent operational services sharing one store session and journal."""

    store: OperationalStore
    handle: OperationalStoreSession
    journal: SqliteOperationalJournal
    artifacts: ArtifactService
    checkpoints: ContextCheckpointService
    forks: SessionForkService
    recovery: RecoveryService
    doctor: OperationalDoctor
    backup: OperationalBackupService


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


def build_operational_services(
    app: Application,
    workspace_id: str,
    *,
    handle: OperationalStoreSession,
    write: bool,
    workspace_root: Path | None = None,
) -> OperationalServices:
    """Compose the operational domain services used by interactive and headless interfaces."""

    store = OperationalStore(app.data_root.root)
    journal = SqliteOperationalJournal(handle)
    artifact_files = FilesystemArtifactStore(store.layout)
    if write:
        artifact_files.ensure_layout()
    artifacts = ArtifactService(
        journal=journal,
        filesystem=artifact_files,
        workspace_id=workspace_id,
        id_source=app.id_source,
        clock=journal.now,
    )
    checkpoints = ContextCheckpointService(
        journal,
        workspace_id=workspace_id,
        id_source=app.id_source,
        clock=journal.now,
    )
    forks = SessionForkService(
        journal,
        workspace_id=workspace_id,
        id_source=app.id_source,
        clock=journal.now,
    )
    recovery = RecoveryService(
        journal,
        workspace_id=workspace_id,
        id_source=app.id_source,
        workspace_root=workspace_root,
    )
    return OperationalServices(
        store=store,
        handle=handle,
        journal=journal,
        artifacts=artifacts,
        checkpoints=checkpoints,
        forks=forks,
        recovery=recovery,
        doctor=OperationalDoctor(store),
        backup=OperationalBackupService(store, journal=journal),
    )


def build_operational_api(
    app: Application,
    workspace_id: str,
    services: OperationalServices,
    *,
    tasks: TaskService | None = None,
    persistence=None,
) -> OperationalApplicationService:
    """Compose the shared command/query boundary over operational domain services."""

    return OperationalApplicationService(
        journal=services.journal,
        workspace_id=workspace_id,
        id_source=app.id_source,
        tasks=tasks,
        artifacts=services.artifacts,
        recovery=services.recovery,
        checkpoints=services.checkpoints,
        forks=services.forks,
        persistence=persistence,
        clock=services.journal.now,
    )


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
    try:
        operational = build_operational_services(
            app,
            identity.workspace_id,
            handle=handle,
            write=True,
            workspace_root=workspace_capability.root,
        )
        journal = operational.journal
        if resume_session_id is not None:
            resumed = journal.get_session(identity.workspace_id, resume_session_id)
            if resumed is not None and resumed.lifecycle is not SessionLifecycle.ACTIVE:
                raise ValueError("only an active Session can resume interactive mode")
        persistence = SessionPersistence(
            workspace_id=identity.workspace_id,
            journal=journal,
            store_session=handle,
            id_source=app.id_source,
            model=model,
            run_policy=run_policy,
            runtime_instance_id=f"inst-{os.getpid()}",
            mutation=mutation,
            artifacts=operational.artifacts,
            recovery=operational.recovery,
        )
        if resume_session_id:
            persistence.restore_into(session)
        else:
            if journal.get_session(identity.workspace_id, session.session_id) is None:
                stamp = journal.now()
                journal.create_session(
                    DurableSession(
                        session_id=session.session_id,
                        workspace_id=identity.workspace_id,
                        created_at=stamp,
                        updated_at=stamp,
                    )
                )
            persistence.attach(session)
        api = build_operational_api(
            app,
            identity.workspace_id,
            operational,
            tasks=persistence.tasks,
            persistence=persistence,
        )

        def create_foreground_grant(current_session: Session):
            profile = current_session.permission_profile
            if (
                profile.access_scope is not AccessScope.FULL_ACCESS
                or profile.approval_mode is not ApprovalMode.MANUAL
                or profile.process_isolation is not ProcessIsolation.HOST
            ):
                raise RuntimeError("只有 full-access-manual 预设支持本地 Host 权限授予")
            task_run_id = getattr(current_session.committer, "current_task_run_id", None)
            agent_run_id = getattr(current_session.committer, "current_agent_run_id", None)
            if task_run_id is None or agent_run_id is None:
                raise RuntimeError("当前前台 AgentRun 尚未创建")
            result = api.create_grant(
                task_run_id=task_run_id,
                agent_run_id=agent_run_id,
                capabilities=(CapabilityName.UNCONFINED_HOST_PROCESS,),
                reason=(
                    "local interface approved unconfined Host access for this foreground AgentRun"
                ),
                preview_digest=UNCONFINED_HOST_WARNING_DIGEST,
                command_id=app.id_source.new_id("cmd"),
            )
            return result.value

        runtime.loop.grant_provider = create_foreground_grant
        commands = CommandService(
            session=session,
            identity=identity,
            project_store=app.project_store,
            config_service=config_service,
            task_service=persistence.tasks,
            api=api,
            id_source=app.id_source,
        )
        orchestrator = SessionOrchestrator(
            session=session,
            runtime=runtime,
            command_service=commands,
            context_builder=context_builder,
            id_source=app.id_source,
        )
        products = SessionApplication(
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
            artifacts=operational.artifacts,
            checkpoints=operational.checkpoints,
            forks=operational.forks,
            api=api,
            doctor=operational.doctor,
            backup=operational.backup,
        )
    except BaseException:
        handle.close()
        raise
    return products
