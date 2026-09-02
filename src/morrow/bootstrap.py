"""Composition root.  Concrete infrastructure is assembled here only."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from morrow.adapters.credentials.keyring import CredentialAccessError, KeyringCredentialStore
from morrow.adapters.local.sandbox import (
    NativeSandboxProcessAdapter,
    default_sandbox_backend,
)
from morrow.adapters.mcp.stdio_client import McpStdioClient
from morrow.adapters.models.learning_reviewer import ModelLearningReviewer
from morrow.adapters.models.openai_compatible import (
    discover_openai_compatible_models,
    estimate_request_chars,
    make_openai_compatible,
)
from morrow.adapters.models.preference_reviewer import ModelPreferenceReviewer
from morrow.adapters.registry import AdapterRegistry
from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.definition_yaml import (
    AgentDefinitionYamlStore,
    WorkflowDefinitionYamlStore,
)
from morrow.adapters.state.extension_yaml import ExtensionYamlStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore, OperationalStoreSession
from morrow.adapters.state.preference_yaml import PreferenceYamlStore
from morrow.adapters.state.preference_yaml_types import PreferenceYamlLoadStatus
from morrow.adapters.state.yaml import (
    GlobalConfigYamlStore,
    ProjectStateYamlStore,
    WorkspaceIndexYamlStore,
)
from morrow.application.agent_definitions.builtins import builtin_definitions
from morrow.application.agent_definitions.publication import (
    AgentDefinitionPublicationService,
    DefinitionCatalog,
)
from morrow.application.agent_runs.preparation import (
    AgentRunPreparationService,
    PreparedAgentRunRuntime,
    build_prepared_spec,
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
    make_bash_tool,
    make_edit_tool,
    make_mainstream_read_search_tools,
    make_promote_sandbox_tool,
    make_read_artifact_tool,
    make_write_tool,
)
from morrow.application.mcp.definitions import McpDefinitionError, McpDefinitionService
from morrow.application.mcp.results import McpResultNormalizer
from morrow.application.mcp.runtime import prepare_mcp_run, rehydrate_mcp_run
from morrow.application.orchestrator import SessionOrchestrator
from morrow.application.preferences.inbox import PreferenceInbox
from morrow.application.preferences.queries import PreferenceQueries
from morrow.application.preferences.reviewer import PreferenceReviewRunner
from morrow.application.preferences.tool import (
    PreferenceManagementService,
    make_preference_management_tool,
)
from morrow.application.preferences.worker import ReviewWorker
from morrow.application.preferences.writer import PreferenceWriter
from morrow.application.prompt import DirectCodingPromptAssembler
from morrow.application.recovery import RecoveryService
from morrow.application.runtime_control import RuntimeControlService
from morrow.application.skills.bindings import SkillBindingService
from morrow.application.skills.catalog import SkillCatalogService
from morrow.application.skills.drafts import SkillDraftService
from morrow.application.skills.lifecycle import SkillLifecycleService
from morrow.application.skills.queries import SkillQueries
from morrow.application.skills.resources import SkillResourceService
from morrow.application.skills.scripts import (
    SkillScriptExecutionService,
    make_skill_script_tool,
)
from morrow.application.skills.selection import SkillSelectionService
from morrow.application.skills.usage import SkillUsageService
from morrow.application.tasks import TaskService
from morrow.application.turn_lifecycle import PreferenceRunSources
from morrow.application.turns import SessionPersistence
from morrow.application.workflows.builtins import visible_builtin_workflows
from morrow.application.workflows.capture import ChangeArtifactCapture
from morrow.application.workflows.composition import build_workflow_runtime
from morrow.application.workflows.management import WorkflowManagementService
from morrow.application.workflows.publication import WorkflowCompilationService
from morrow.application.workflows.queries import WorkflowQueryService
from morrow.core.agent_runs import AgentDefinitionRef, exact_model_capabilities
from morrow.core.artifacts import ArtifactKind
from morrow.core.capabilities import (
    AccessScope,
    ApprovalMode,
    PermissionProfile,
    ProcessIsolation,
    WorkspaceCapability,
)
from morrow.core.domain import DurableSession, SessionLifecycle
from morrow.core.models import (
    ModelRef,
    ProviderConfig,
    ProviderModelConfig,
    StateLoadStatus,
    StatePresence,
)
from morrow.core.permissions import UNCONFINED_HOST_WARNING_DIGEST, CapabilityName
from morrow.core.preference_documents import PreferenceDocument
from morrow.core.preference_models import PreferenceScope
from morrow.core.runtime_policy import REVIEW_MAX_TIMEOUT_SECONDS
from morrow.core.skills.scripts import SCRIPT_OUTPUT_FILE_MAX_BYTES
from morrow.core.skills.trust import SourceKind
from morrow.core.store import (
    StorageError,
    StorageErrorCode,
    StoreOpenMode,
)
from morrow.runtime.agent import AgentRuntime
from morrow.runtime.capabilities import CapabilityPolicy
from morrow.runtime.ids import RandomIdSource
from morrow.runtime.policy import (
    RuntimePolicy,
    load_runtime_policy,
)
from morrow.runtime.session import Session
from morrow.runtime.tools import ToolExecutor, ToolRegistry
from morrow.services.changes import ChangeSetService
from morrow.services.files import (
    WorkspaceFileService,
    WorkspaceMutationService,
    WorkspacePathResolver,
)
from morrow.services.git import GitInspectionService
from morrow.services.process import ProcessExecutionService
from morrow.services.profile_configuration import ConfigPatchService
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
    runtime_policy: RuntimePolicy


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
    preference_service: PreferenceManagementService | None = None
    review_worker: ReviewWorker | None = None
    workflow_runtime: object | None = None
    workflow_management: WorkflowManagementService | None = None


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


@dataclass(frozen=True)
class SkillServices:
    """Composition-only Skill services; lifecycle semantics stay in application modules."""

    extensions: ExtensionYamlStore
    packages: object
    catalog: SkillCatalogService
    bindings: SkillBindingService
    lifecycle: SkillLifecycleService
    queries: SkillQueries
    selection: SkillSelectionService
    resources: SkillResourceService
    drafts: SkillDraftService | None = None
    usage: SkillUsageService | None = None
    journal: SqliteOperationalJournal | None = None


def build_skill_services(
    app: Application,
    *,
    workspace_id: str | None = None,
    journal: SqliteOperationalJournal | None = None,
    available_tools=None,
    available_mcp_servers=None,
    available_capabilities=None,
) -> SkillServices:
    from morrow.adapters.skills.managed_store import ManagedSkillPackageStore

    extensions = ExtensionYamlStore(app.data_root.root)
    packages = ManagedSkillPackageStore(app.data_root.root)
    roots: dict[SourceKind, tuple[Path | tuple[Path, str | None], ...]] = {}
    for source_kind in SourceKind:
        global_root = app.data_root.root / "skills" / source_kind.value
        configured: list[Path | tuple[Path, str | None]] = [(global_root, None)]
        if workspace_id is not None:
            configured.append(
                (
                    app.data_root.root / "workspaces" / workspace_id / "skills" / source_kind.value,
                    workspace_id,
                )
            )
        roots[source_kind] = tuple(configured)
    catalog = SkillCatalogService(roots)
    bindings = SkillBindingService(extensions, workspace_id)
    lifecycle = SkillLifecycleService(
        extensions,
        packages,
        catalog,
        journal=journal,
        workspace_id=workspace_id,
        id_source=app.id_source,
        clock=journal.now if journal is not None else None,
        available_tools=available_tools,
        available_mcp_servers=available_mcp_servers,
    )
    queries = SkillQueries(catalog, bindings, journal=journal)
    selection = SkillSelectionService(
        catalog,
        bindings,
        packages,
        id_source=app.id_source,
        workspace_id=workspace_id,
        available_tools=available_tools,
        available_mcp_servers=available_mcp_servers,
        available_capabilities=available_capabilities,
    )
    resources = SkillResourceService(packages)
    drafts = (
        SkillDraftService(
            journal,
            packages,
            lifecycle,
            workspace_id=workspace_id,
            id_source=app.id_source,
            clock=journal.now if journal is not None else None,
            available_tools=available_tools,
            available_mcp_servers=available_mcp_servers,
        )
        if journal is not None and workspace_id is not None
        else None
    )
    usage = (
        SkillUsageService(
            journal,
            workspace_id=workspace_id,
            id_source=app.id_source,
            clock=journal.now if journal is not None else None,
        )
        if journal is not None and workspace_id is not None
        else None
    )
    return SkillServices(
        extensions=extensions,
        packages=packages,
        catalog=catalog,
        bindings=bindings,
        lifecycle=lifecycle,
        queries=queries,
        selection=selection,
        resources=resources,
        drafts=drafts,
        usage=usage,
        journal=journal,
    )


def _default_tool_executor(
    run_policy,
    *,
    config_service=None,
    preference_service: PreferenceManagementService | None = None,
    approval_port=None,
    capability_policy=None,
    files: WorkspaceFileService,
    search: WorkspaceSearchService,
    mutation: WorkspaceMutationService,
    changes: ChangeSetService,
    process: ProcessExecutionService,
    artifacts: ArtifactService | None = None,
    git: GitInspectionService,
    skill_scripts: SkillScriptExecutionService | None = None,
    sandbox: SandboxSnapshotService | None = None,
    sandbox_enabled: bool = False,
    process_isolation: ProcessIsolation = ProcessIsolation.HOST,
) -> ToolExecutor:
    registry = ToolRegistry()
    if config_service is not None:
        registry.register(make_configuration_tool(config_service))
    if preference_service is not None:
        registry.register(make_preference_management_tool(preference_service))
    for tool in make_mainstream_read_search_tools(files, search):
        registry.register(tool)
    if artifacts is not None:
        registry.register(make_read_artifact_tool(artifacts))
    registry.register(make_edit_tool(mutation, changes))
    registry.register(make_write_tool(mutation, changes))
    registry.register(make_bash_tool(process))
    if skill_scripts is not None:
        registry.register(make_skill_script_tool(skill_scripts))
    del git  # Git inspection remains available through the confined bash surface.
    if sandbox is not None and process.requires_sandbox and sandbox_enabled:
        registry.register(make_promote_sandbox_tool(sandbox, mutation, changes))
    names = tuple(tool.function.name for tool in registry.definitions())
    missing = tuple(
        name
        for name in names
        if (registered := registry.get(name)) is None or registered.recovery_declaration is None
    )
    if missing:
        raise RuntimeError("registered tools lack durable declarations: " + ", ".join(missing))
    return ToolExecutor(
        registry.snapshot(
            require_runtime_contract=True,
            require_closed_schema=False,
            require_production_declaration=True,
            expected_process_isolation=process_isolation,
        ),
        run_policy,
        approval_port=approval_port,
        capability_policy=capability_policy,
        expected_process_isolation=process_isolation,
    )


def build_application(
    *, state_root: Path | None = None, credentials=None, id_source=None
) -> Application:
    data_root = DataRoot(state_root)
    data_root.ensure()
    global_store = GlobalConfigYamlStore(data_root.root)
    loaded_config = global_store.load()
    runtime_overrides = (
        loaded_config.value.runtime_policy
        if loaded_config.status is StateLoadStatus.OK and loaded_config.value is not None
        else None
    )
    index_store = WorkspaceIndexYamlStore(data_root.root)
    project_store = ProjectStateYamlStore(data_root.root)
    registry = AdapterRegistry()
    registry.register(
        "openai-compatible",
        make_openai_compatible,
        tool_protocol="openai_function",
        multiple_tool_calls=True,
        discovery=discover_openai_compatible_models,
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
        load_runtime_policy(overrides=runtime_overrides),
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
    config_service: ConfigPatchService | None = None,
    preference_writer: PreferenceWriter | None = None,
    learning_provider=None,
    learning_model=None,
    preference_reviewer=None,
    preference_model=None,
) -> OperationalApplicationService:
    """Compose the shared command/query boundary over operational domain services."""

    resolved_config_service = config_service or ConfigPatchService(
        app.project_store, app.global_store, workspace_id
    )
    if preference_writer is not None:
        resolved_config_service.preference_writer = preference_writer
    agent_policy = app.runtime_policy.agent_run
    model_safe_chars = (
        agent_policy.model_safe_request_chars.get(
            f"{learning_model.provider_id}/{learning_model.model_id}"
        )
        if learning_model is not None
        else None
    )
    learning_context_chars = min(
        agent_policy.requested_context_chars,
        model_safe_chars or agent_policy.unknown_model_fallback_chars,
    )
    learning_reviewer = (
        ModelLearningReviewer(
            learning_provider,
            request_char_limit=learning_context_chars,
        )
        if learning_provider is not None
        else None
    )
    resolved_preference_reviewer = preference_reviewer
    if resolved_preference_reviewer is None and learning_provider is not None:
        resolved_preference_reviewer = ModelPreferenceReviewer(learning_provider)
    preference_inbox = (
        PreferenceInbox(
            journal=services.journal,
            workspace_id=workspace_id,
            writer=preference_writer,
            id_source=app.id_source,
            clock=services.journal.now,
        )
        if preference_writer is not None
        else None
    )
    preference_queries = (
        PreferenceQueries(preference_writer.yaml_store, workspace_id)
        if preference_writer is not None
        else None
    )
    preference_review_runner = (
        PreferenceReviewRunner(
            journal=services.journal,
            workspace_id=workspace_id,
            id_source=app.id_source,
            clock=services.journal.now,
            reviewer=resolved_preference_reviewer,
            model=preference_model or learning_model,
            timeout_seconds=app.runtime_policy.reviews.preference_timeout_seconds,
        )
        if resolved_preference_reviewer is not None
        else None
    )
    service = OperationalApplicationService(
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
        learning_reviewer=learning_reviewer,
        learning_model=learning_model,
        learning_review_timeout_seconds=REVIEW_MAX_TIMEOUT_SECONDS,
        learning_review_lease_seconds=int(REVIEW_MAX_TIMEOUT_SECONDS) + 60,
        learning_review_context_chars=learning_context_chars,
        config_service=resolved_config_service,
        preference_inbox=preference_inbox,
        preference_queries=preference_queries,
        preference_review_runner=preference_review_runner,
    )
    service.review_worker = ReviewWorker(
        journal=services.journal,
        workspace_id=workspace_id,
        id_source=app.id_source,
        clock=services.journal.now,
        runner=preference_review_runner,
        reviewer=resolved_preference_reviewer,
        model=preference_model or learning_model,
        learning_runner=service.learning_review_runner,
        timeout_seconds=app.runtime_policy.reviews.preference_timeout_seconds,
        lease_seconds=app.runtime_policy.reviews.preference_lease_seconds,
        retry_backoff_seconds=app.runtime_policy.reviews.preference_retry_backoff_seconds,
    )
    return service


def _sandbox_toolchain_paths(workspace_root: Path) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """Select exact existing runtimes; never inherit an arbitrary Host PATH."""

    root = workspace_root.resolve(strict=True)
    candidates: list[Path] = []
    workspace_venv = root / ".venv"
    try:
        if workspace_venv.is_dir() and not workspace_venv.is_symlink():
            resolved_venv = workspace_venv.resolve(strict=True)
            if resolved_venv.is_relative_to(root):
                candidates.append(resolved_venv)
    except OSError:
        pass
    candidates.extend((Path(sys.prefix), Path(sys.base_prefix)))

    roots: list[Path] = []
    bins: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if not resolved.is_dir() or resolved in {Path("/"), Path.home().resolve()}:
            continue
        if resolved not in roots:
            roots.append(resolved)
        bin_path = resolved / ("Scripts" if os.name == "nt" else "bin")
        if bin_path.is_dir():
            resolved_bin = bin_path.resolve(strict=True)
            if resolved_bin.is_relative_to(resolved) and resolved_bin not in bins:
                bins.append(resolved_bin)
    return tuple(roots), tuple(bins)


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
    global_result = app.global_store.load()
    config = global_result.value
    generic_preferences = PreferenceYamlStore(app.data_root.root)
    generic_global_load = generic_preferences.load_global()
    generic_workspace_load = generic_preferences.load_workspace(identity.workspace_id)
    global_preferences = (
        PreferenceWriter._document_from_value(PreferenceScope.GLOBAL, generic_global_load.value)
        if generic_global_load.status is PreferenceYamlLoadStatus.OK
        and generic_global_load.value is not None
        else PreferenceDocument(scope="global")
    )
    workspace_preferences = (
        PreferenceWriter._document_from_value(
            PreferenceScope.WORKSPACE, generic_workspace_load.value
        )
        if generic_workspace_load.status is PreferenceYamlLoadStatus.OK
        and generic_workspace_load.value is not None
        else PreferenceDocument(scope="workspace")
    )

    def load_run_preferences() -> PreferenceRunSources:
        global_load = generic_preferences.load_global()
        workspace_load = generic_preferences.load_workspace(identity.workspace_id)
        errors: list[str] = []
        if global_load.status is PreferenceYamlLoadStatus.OK and global_load.value is not None:
            global_document = PreferenceWriter._document_from_value(
                PreferenceScope.GLOBAL, global_load.value
            )
        else:
            errors.append(f"global_{global_load.status.value}")
            global_document = PreferenceDocument(
                scope="global", revision=max(0, global_load.revision), entries=()
            )
        if (
            workspace_load.status is PreferenceYamlLoadStatus.OK
            and workspace_load.value is not None
        ):
            workspace_document = PreferenceWriter._document_from_value(
                PreferenceScope.WORKSPACE, workspace_load.value
            )
            presence = StatePresence(workspace_load.presence or StatePresence.PRESENT.value)
        else:
            errors.append(f"workspace_{workspace_load.status.value}")
            workspace_document = PreferenceDocument(
                scope="workspace", revision=max(0, workspace_load.revision), entries=()
            )
            presence = StatePresence.CLEARED
        return PreferenceRunSources(
            global_document=global_document,
            workspace_document=workspace_document,
            workspace_presence=presence,
            refresh_status="degraded" if errors else "ok",
            refresh_error="+".join(errors) if errors else None,
        )

    generic_authority_active = True
    permission_profile = permission_profile or PermissionProfile()
    workspace_capability = WorkspaceCapability(
        workspace_id=identity.workspace_id,
        root=Path(identity.path),
        read_only=inspection.read_only,
    )
    prompt_assembler = DirectCodingPromptAssembler(workspace_capability.root)
    session = Session(
        session_id=resume_session_id or app.id_source.new_id("ses"),
        profile=(
            profile_result.value.profile
            if profile_result.value and not inspection.read_only
            else None
        ),
        global_preferences=global_preferences,
        workspace_preferences=workspace_preferences,
        read_only=inspection.read_only,
        workspace_preferences_read_only=inspection.preferences_read_only,
        permission_profile=permission_profile,
        workspace_capability=workspace_capability,
        metrics_enabled=metrics_enabled,
        profile_revision=profile_result.revision or 0,
        preferences_revision=preferences_result.revision or 0,
        global_preferences_revision=global_result.revision or 0,
        profile_presence=profile_result.presence
        or (StatePresence.PRESENT if profile_result.value else StatePresence.MISSING),
        workspace_preferences_presence=preferences_result.presence
        or (StatePresence.PRESENT if preferences_result.value else StatePresence.MISSING),
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
        toolchain_roots, toolchain_bins = _sandbox_toolchain_paths(workspace_capability.root)
        process = ProcessExecutionService(
            files,
            adapter=NativeSandboxProcessAdapter(
                workspace_capability.root,
                sandbox,
                sandbox_backend,
                toolchain_roots=toolchain_roots,
                toolchain_bin_paths=toolchain_bins,
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
    configured_model = (
        provider_config.models.get(model.model_id) if provider_config is not None else None
    )
    exact_capabilities = exact_model_capabilities(
        adapter_id,
        app.registry.capabilities(adapter_id),
        model,
        configured_model.capabilities if configured_model is not None else None,
    )
    run_policy = app.runtime_policy.agent_run.resolve(
        model,
        tool_protocol=exact_capabilities.tool_protocol,
        multiple_tool_calls=exact_capabilities.multiple_tool_calls,
        context_window_tokens=exact_capabilities.context_window_tokens,
        max_output_tokens=exact_capabilities.max_output_tokens,
        settings=app.runtime_policy.long_horizon,
    )
    context_builder = ContextBuilder(
        run_policy=run_policy,
        estimate_request_chars=estimate_request_chars,
        prompt_assembler=prompt_assembler,
    )
    handle = None
    preference_service = None
    try:
        handle = _open_operational_store(app)
        preference_journal = SqliteOperationalJournal(handle)
        preference_writer = PreferenceWriter(
            generic_preferences,
            preference_journal,
            identity.workspace_id,
            id_source=app.id_source,
            clock=preference_journal.now,
        )
        config_service.preference_writer = preference_writer
        preference_service = PreferenceManagementService(
            preference_writer,
            PreferenceQueries(generic_preferences, identity.workspace_id),
            session=session,
        )
        if generic_authority_active:
            tool_preference_service = preference_service
        else:
            tool_preference_service = None

        operational = build_operational_services(
            app,
            identity.workspace_id,
            handle=handle,
            write=True,
            workspace_root=workspace_capability.root,
        )
        journal = operational.journal
        from morrow.adapters.skills.managed_store import ManagedSkillPackageStore

        script_packages = ManagedSkillPackageStore(app.data_root.root)

        def make_skill_script_adapter(root: Path):
            script_files = WorkspaceFileService(WorkspacePathResolver(root))
            script_sandbox = SandboxSnapshotService(
                script_files, max_change_content_bytes=SCRIPT_OUTPUT_FILE_MAX_BYTES
            )
            return NativeSandboxProcessAdapter(root, script_sandbox, sandbox_backend)

        skill_scripts = SkillScriptExecutionService(
            script_packages,
            workspace_id=identity.workspace_id,
            journal=journal,
            artifacts=operational.artifacts,
            adapter_factory=make_skill_script_adapter,
            sandbox_available=(
                permission_profile.process_isolation is ProcessIsolation.NATIVE_SANDBOX
                and sandbox_capability.supported
            ),
            secrets=(active_credential,) if active_credential else (),
        )

        def make_tools(policy):
            if policy.provider_tool_support.tool_protocol != "openai_function":
                return None
            return _default_tool_executor(
                policy,
                config_service=config_service,
                preference_service=tool_preference_service,
                approval_port=approval_port,
                capability_policy=capability_policy,
                files=files,
                search=search,
                mutation=mutation,
                changes=changes,
                process=process,
                artifacts=operational.artifacts,
                skill_scripts=skill_scripts,
                git=git,
                sandbox=sandbox,
                sandbox_enabled=sandbox_capability.supported,
                process_isolation=permission_profile.process_isolation,
            )

        def mcp_state():
            definition_service = McpDefinitionService(
                ExtensionYamlStore(app.data_root.root), identity.workspace_id
            )
            try:
                global_definitions = definition_service.list("global")
                workspace_definitions = definition_service.list(
                    "workspace", scope_id=identity.workspace_id
                )
            except McpDefinitionError as exc:
                raise ValueError("MCP desired state is unavailable") from exc
            definitions_by_id = {item.server_id: item for item in global_definitions}
            definitions_by_id.update({item.server_id: item for item in workspace_definitions})
            definitions = tuple(sorted(definitions_by_id.values(), key=lambda item: item.server_id))
            catalogs = {}
            for definition in definitions:
                catalog = journal.get_mcp_catalog(
                    definition.scope,
                    definition.server_id,
                    scope_id=definition.scope_id,
                )
                if catalog is not None:
                    catalogs[definition.server_id] = catalog
            return definitions, catalogs

        def mcp_client_factory(definition):
            return McpStdioClient(definition, workspace_root=workspace_capability.root)

        def mcp_normalizer_factory(_server_id):
            def publish(content, mime_type, role):
                metadata = operational.artifacts.publish_bytes(
                    content,
                    kind=ArtifactKind.DIAGNOSTIC_REPORT,
                    session_id=session.session_id,
                    task_run_id=persistence.current_task_run_id,
                    already_redacted=False,
                )
                from morrow.core.mcp import McpArtifactRef

                return McpArtifactRef(
                    artifact_id=metadata.artifact_id,
                    role=role,
                    mime_type=mime_type,
                    byte_size=metadata.byte_size,
                )

            return McpResultNormalizer(publish_artifact=publish)

        def prepare_mcp(agent_run_id, _policy):
            definitions, catalogs = mcp_state()
            if not any(item.enabled for item in definitions):
                return None
            return prepare_mcp_run(
                definitions,
                catalogs,
                workspace_id=identity.workspace_id,
                agent_run_id=agent_run_id,
                id_source=app.id_source,
                client_factory=mcp_client_factory,
                normalizer_factory=mcp_normalizer_factory,
            )

        def rehydrate_mcp(snapshot, agent_run_id):
            definitions, _current_catalogs = mcp_state()
            launch_snapshots = journal.list_mcp_launch_snapshots(
                identity.workspace_id, agent_run_id
            )
            tool_snapshots = journal.list_mcp_tool_snapshots(identity.workspace_id, agent_run_id)
            if (
                tuple(item.launch_snapshot_id for item in launch_snapshots)
                != snapshot.mcp_run_snapshot_ids
            ):
                raise ValueError("AgentRun MCP snapshot references are not durable")
            launches_by_server = {item.server_id: item for item in launch_snapshots}
            catalogs = {}
            for definition in definitions:
                launch = launches_by_server.get(definition.server_id)
                if launch is None or launch.catalog_revision is None:
                    continue
                catalog = journal.get_mcp_catalog(
                    definition.scope,
                    definition.server_id,
                    scope_id=definition.scope_id,
                    revision=launch.catalog_revision,
                )
                if catalog is not None:
                    catalogs[definition.server_id] = catalog
            permission_snapshot = journal.get_permission_snapshot_for_run(
                identity.workspace_id, agent_run_id
            )
            reviews = (
                {item.server_id: item for item in permission_snapshot.mcp_review_evidence}
                if permission_snapshot is not None
                else {}
            )
            return rehydrate_mcp_run(
                definitions,
                catalogs,
                launch_snapshots,
                tool_snapshots,
                workspace_id=identity.workspace_id,
                agent_run_id=agent_run_id,
                client_factory=mcp_client_factory,
                normalizer_factory=mcp_normalizer_factory,
                reviews=reviews,
            )

        tool_executor = make_tools(run_policy)
        runtime_control = RuntimeControlService(
            journal,
            workspace_id=identity.workspace_id,
            id_source=app.id_source,
            clock=journal.now,
        )
        runtime = AgentRuntime(
            provider,
            model,
            context_builder,
            id_source=app.id_source,
            tool_executor=tool_executor,
            runtime_control=runtime_control,
        )
        skill_services = build_skill_services(
            app,
            workspace_id=identity.workspace_id,
            journal=journal,
            available_tools=(
                tuple(tool.function.name for tool in tool_executor.definitions)
                if tool_executor is not None
                else ()
            ),
            available_mcp_servers=tuple(item.server_id for item in mcp_state()[0] if item.enabled),
        )
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
            preference_loader=load_run_preferences,
            skill_selection=skill_services.selection,
            skill_usage=skill_services.usage,
            prompt_assembler=prompt_assembler,
        )
        spec_provider_config = provider_config
        if spec_provider_config is None:
            # Explicit injected Provider integrations have no global ProviderConfig, so freeze
            # their exact model in a current prepared spec.
            spec_provider_config = ProviderConfig(
                adapter=adapter_id,
                base_url="",
                models={model.model_id: ProviderModelConfig(api_model_id=model.model_id)},
            )
        injected_spec = build_prepared_spec(
            provider_config=spec_provider_config,
            model=model,
            exact_capabilities=exact_capabilities,
            config_revision=global_result.revision,
            run_policy=run_policy,
            tools=tool_executor.definitions if tool_executor is not None else (),
            prompt_assembler=prompt_assembler,
        )
        injected_prepared = PreparedAgentRunRuntime(
            spec=injected_spec,
            provider=provider,
            model=model,
            context_builder=context_builder,
            tool_executor=tool_executor,
            run_policy=run_policy,
        )
        preparation = AgentRunPreparationService(
            global_store=app.global_store,
            registry=app.registry,
            agent_policy=app.runtime_policy.agent_run,
            credential_resolver=app.provider_service.credential_resolver,
            frozen_credential_resolver=app.provider_service.resolve_frozen_credential,
            estimate_request_chars=estimate_request_chars,
            tool_factory=make_tools,
            injected=injected_prepared,
            workspace_id=identity.workspace_id,
            mcp_factory=prepare_mcp,
            mcp_rehydrate_factory=rehydrate_mcp,
            prompt_assembler=prompt_assembler,
            long_horizon_settings=app.runtime_policy.long_horizon,
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
            config_service=config_service,
            preference_writer=preference_writer,
            learning_provider=provider,
            learning_model=model,
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
            preference_service=preference_service,
        )
        orchestrator = SessionOrchestrator(
            session=session,
            runtime=runtime,
            command_service=commands,
            context_builder=context_builder,
            id_source=app.id_source,
            preparation=preparation,
            runtime_control=runtime_control,
        )
        tool_names = (
            tuple(tool.function.name for tool in tool_executor.definitions)
            if tool_executor is not None
            else ()
        )
        read_tools = {"read", "read_artifact", "ls", "find", "grep"}
        workflow_catalog = DefinitionCatalog(
            models=tuple(
                ModelRef(provider_id=provider_id, model_id=model_id)
                for provider_id, value in (config.providers.items() if config else ())
                for model_id in value.models
            )
            or (model,),
            skill_version_ids=frozenset(
                item.version_id
                for item in journal.list_skill_versions(workspace_id=identity.workspace_id)
            ),
            tool_access={name: "read" if name in read_tools else "write" for name in tool_names},
            allowed_tools=frozenset(tool_names),
        )
        agent_publication = AgentDefinitionPublicationService(
            journal,
            workspace_id=identity.workspace_id,
            catalog=workflow_catalog,
            id_source=app.id_source,
        )
        workflow_publication = WorkflowCompilationService(
            journal,
            workspace_id=identity.workspace_id,
            catalog=workflow_catalog,
            id_source=app.id_source,
        )
        workflow_runtime = build_workflow_runtime(
            journal,
            handle,
            workspace_id=identity.workspace_id,
            artifacts=operational.artifacts,
            agent_publication=agent_publication,
            preparation=preparation,
            id_source=app.id_source,
            runtime_instance_id=f"inst-{os.getpid()}",
            clock=journal.now,
            skill_selection=skill_services.selection,
            mutation=mutation,
            change_capture=ChangeArtifactCapture(operational.artifacts, mutation),
        )
        packaged_agents = builtin_definitions(model)
        packaged_refs = {}
        for source in packaged_agents:
            head = journal.agent_definitions.get_head(identity.workspace_id, source.definition_id)
            version = (
                journal.agent_definitions.get_version(identity.workspace_id, head.version_id)
                if head is not None
                else None
            )
            if version is not None:
                packaged_refs[source.definition_id] = AgentDefinitionRef(
                    definition_id=source.definition_id,
                    version_id=version.version_id,
                    content_hash=version.content_hash,
                )
        packaged_workflows = visible_builtin_workflows(
            packaged_refs,
            native_sandbox=(
                permission_profile.process_isolation is ProcessIsolation.NATIVE_SANDBOX
                and sandbox_capability.supported
            ),
        )
        agent_source_store = AgentDefinitionYamlStore(app.data_root.root)
        workflow_source_store = WorkflowDefinitionYamlStore(app.data_root.root)
        workflow_runtime = replace(
            workflow_runtime,
            queries=WorkflowQueryService(
                journal,
                workspace_id=identity.workspace_id,
                agent_sources=agent_source_store,
                workflow_sources=workflow_source_store,
                agent_builtins=packaged_agents,
                workflow_builtins=packaged_workflows,
            ),
        )
        workflow_management = WorkflowManagementService(
            workspace_id=identity.workspace_id,
            agent_sources=agent_source_store,
            workflow_sources=workflow_source_store,
            agent_publication=agent_publication,
            workflow_publication=workflow_publication,
            runtime=workflow_runtime,
            active_model=model,
            agent_builtins=packaged_agents,
            workflow_builtins=packaged_workflows,
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
            preference_service=preference_service,
            review_worker=api.review_worker,
            workflow_runtime=workflow_runtime,
            workflow_management=workflow_management,
        )
    except BaseException:
        handle.close()
        raise
    return products
