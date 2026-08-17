"""Composition root.  Concrete infrastructure is assembled here only."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from morrow.adapters.credentials.keyring import KeyringCredentialStore
from morrow.adapters.models.openai_compatible import estimate_request_chars, make_openai_compatible
from morrow.adapters.registry import AdapterRegistry
from morrow.adapters.state.yaml import (
    GlobalConfigYamlStore,
    ProjectStateYamlStore,
    WorkspaceIndexYamlStore,
)
from morrow.application.commands import CommandService
from morrow.application.context import ContextBudgetError, ContextBuilder
from morrow.application.orchestrator import SessionOrchestrator
from morrow.application.structured import StructuredCompletionError, complete_structured
from morrow.core.models import ConfigExtractionResult, Preferences
from morrow.runtime.agent import AgentRuntime
from morrow.runtime.ids import RandomIdSource
from morrow.runtime.policy import AgentPolicy, load_agent_policy
from morrow.runtime.session import Session
from morrow.runtime.tools import (
    ToolExecutor,
    ToolRegistry,
    make_calculate_tool,
    make_lookup_record_tool,
)
from morrow.services.preferences import ConfigPatchService
from morrow.services.provider import ProviderService
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


DEMO_RECORDS = {
    ("plans", "starter"): {"monthly_price": 29.0},
    ("plans", "pro"): {"monthly_price": 79.0},
    ("regions", "de"): {"tax_rate": 0.19},
    ("regions", "us-ca"): {"tax_rate": 0.0725},
}


def _default_tool_executor(run_policy) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(make_lookup_record_tool(DEMO_RECORDS))
    registry.register(make_calculate_tool())
    return ToolExecutor(registry.snapshot(), run_policy)


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


def build_session_application(app: Application, identity, *, provider=None, model=None):
    inspection = app.workspace_state_service.inspect(identity.workspace_id)
    profile_result = inspection.profile
    preferences_result = inspection.preferences
    config = app.global_store.load().value
    session = Session(
        session_id=app.id_source.new_id("ses"),
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
    )
    if provider is None or model is None:
        provider, model = app.provider_service.build_active()
    provider_config = config.providers.get(model.provider_id) if config else None
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
        _default_tool_executor(run_policy)
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
        except (StructuredCompletionError, ContextBudgetError, TimeoutError, RuntimeError):
            return ConfigExtractionResult(
                result="clarification_required",
                question="配置结果不明确；请直接使用 /config edit。",
            )

    commands = CommandService(
        session=session,
        identity=identity,
        project_store=app.project_store,
        config_service=config_service,
    )
    orchestrator = SessionOrchestrator(
        session=session,
        runtime=runtime,
        command_service=commands,
        context_builder=context_builder,
        config_extractor=extract_config,
        config_patch_service=config_service,
        id_source=app.id_source,
    )
    return SessionApplication(
        session=session,
        context_builder=context_builder,
        commands=commands,
        orchestrator=orchestrator,
    )
