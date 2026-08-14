"""Composition root.  Concrete infrastructure is assembled here only."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from morrow.adapters.credentials.keyring import KeyringCredentialStore
from morrow.adapters.models.openai_compatible import make_openai_compatible
from morrow.adapters.registry import AdapterRegistry
from morrow.adapters.state.yaml import (
    GlobalConfigYamlStore,
    ProjectStateYamlStore,
    WorkspaceIndexYamlStore,
)
from morrow.application.commands import CommandService
from morrow.application.context import ContextBuilder
from morrow.application.orchestrator import SessionOrchestrator
from morrow.application.structured import StructuredCompletionError, complete_structured
from morrow.core.models import ConfigExtractionResult, Preferences
from morrow.runtime.agent import AgentRuntime
from morrow.runtime.session import Session
from morrow.services.handoff import HandoffService
from morrow.services.preferences import ConfigPatchService
from morrow.services.provider import ProviderService
from morrow.services.workspace import DataRoot, WorkspaceService, WorkspaceStateService
from morrow.testing import FixedIdSource


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


def build_application(
    *, state_root: Path | None = None, credentials=None, id_source=None
) -> Application:
    data_root = DataRoot(state_root)
    data_root.ensure()
    global_store = GlobalConfigYamlStore(data_root.root)
    index_store = WorkspaceIndexYamlStore(data_root.root)
    project_store = ProjectStateYamlStore(data_root.root)
    registry = AdapterRegistry()
    registry.register("openai-compatible", make_openai_compatible)
    credential_store = credentials or KeyringCredentialStore()
    provider_service = ProviderService(global_store, credential_store, registry)
    workspace_service = WorkspaceService(
        data_root,
        index_store,
        id_factory=(id_source or FixedIdSource()).new_id if id_source else None,
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
    )


def build_session_application(app: Application, identity, *, provider=None, model=None):
    profile_result = app.project_store.load_profile(identity.workspace_id)
    preferences_result = app.project_store.load_preferences(identity.workspace_id)
    config = app.global_store.load().value
    session = Session(
        session_id=(FixedIdSource().new_id("ses")),
        profile=profile_result.value.profile if profile_result.value else None,
        global_preferences=config.preferences if config else Preferences(),
        workspace_preferences=preferences_result.value.preferences
        if preferences_result.value
        else Preferences(),
    )
    context_builder = ContextBuilder()
    if provider is None or model is None:
        provider, model = app.provider_service.build_active()
    runtime = AgentRuntime(provider, model, context_builder)
    handoff_service = HandoffService(
        app.project_store, provider, model, context_builder, identity.workspace_id
    )
    config_service = ConfigPatchService(
        app.project_store, app.global_store, identity.workspace_id, session
    )

    async def extract_config(text, current_session):
        try:
            value, _ = await complete_structured(
                provider,
                model,
                context_builder,
                current_session,
                ConfigExtractionResult,
                "判断下面是否是一个独立的持久化配置请求；只返回 no_change、clarification_required 或 config_patch JSON。\n"
                + text,
            )
            return value
        except (StructuredCompletionError, TimeoutError, RuntimeError):
            return ConfigExtractionResult(
                result="clarification_required",
                question="配置结果不明确；请直接使用 /config edit。",
            )

    commands = CommandService(
        session=session,
        identity=identity,
        project_store=app.project_store,
        handoff_service=handoff_service,
        config_service=config_service,
        provider_service=app.provider_service,
        workspace_service=app.workspace_service,
    )
    orchestrator = SessionOrchestrator(
        session=session,
        runtime=runtime,
        command_service=commands,
        context_builder=context_builder,
        config_extractor=extract_config,
        config_patch_service=config_service,
    )
    return session, context_builder, handoff_service, commands, orchestrator
