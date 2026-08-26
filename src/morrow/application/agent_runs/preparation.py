"""Per-AgentRun preparation: frozen spec plus a composed live runtime.

This is the only cross-domain composition boundary. ``prepare_new()`` reads the
current global configuration; ``rehydrate()`` rebuilds from stored AgentRun
evidence only and never consults the current active model. Skills and MCP plug
into this seam in later subplans.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from morrow.adapters.registry import AdapterRegistry
from morrow.application.context import ContextBuilder
from morrow.application.mcp.runtime import PreparedMcpRun, register_mcp_tools
from morrow.core.agent_runs import (
    ExactModelCapabilities,
    PreparedAgentRunSpec,
    ProviderRuntimeSnapshot,
    exact_model_capabilities,
)
from morrow.core.domain import AgentRunSnapshot, canonical_json_bytes, sha256_digest
from morrow.core.models import (
    CredentialRef,
    GlobalConfig,
    ModelRef,
    ProviderConfig,
    ProviderModelConfig,
    RunPolicy,
    ToolDefinition,
)
from morrow.runtime.policy import AgentPolicy
from morrow.runtime.tools import ToolExecutor, ToolRegistry


class AgentRunPreparationError(RuntimeError):
    """A prepared run cannot be assembled from the available evidence."""


class ProviderUnavailableError(AgentRunPreparationError):
    """A frozen CredentialRef is not resolvable; there is no silent fallback."""


@dataclass(frozen=True, slots=True)
class PreparedAgentRunRuntime:
    """Live per-run objects; never stored in Pydantic models, SQLite or events."""

    spec: PreparedAgentRunSpec
    provider: Any
    model: ModelRef
    context_builder: ContextBuilder
    tool_executor: ToolExecutor | None
    run_policy: RunPolicy
    mcp_run: PreparedMcpRun | None = None
    agent_run_id: str | None = None

    def close(self) -> None:
        """Compatibility cleanup hook; async consumers call :meth:`aclose`."""
        return None

    async def aclose(self) -> None:
        """Close lazy MCP resources without changing the ordinary loop shape."""
        if self.mcp_run is not None:
            await self.mcp_run.pool.close()


def tool_schema_digest(tools: tuple[ToolDefinition, ...]) -> str:
    payload = [tool.model_dump(mode="json") for tool in tools]
    return sha256_digest(canonical_json_bytes(payload))


def run_policy_digest(run_policy: RunPolicy) -> str:
    return sha256_digest(canonical_json_bytes(run_policy.model_dump(mode="json")))


def build_prepared_spec(
    *,
    provider_config: ProviderConfig,
    model: ModelRef,
    exact_capabilities: ExactModelCapabilities,
    config_revision: int,
    run_policy: RunPolicy,
    tools: tuple[ToolDefinition, ...],
    mcp_run_snapshot_ids: tuple[str, ...] = (),
    prompt_assembler=None,
) -> PreparedAgentRunSpec:
    """Freeze the sanitized evidence one AgentRun will be rebuilt from."""
    api_model_id = provider_config.models[model.model_id].api_model_id
    provider_runtime = ProviderRuntimeSnapshot(
        provider_id=model.provider_id,
        adapter_id=provider_config.adapter,
        model=model,
        api_model_id=api_model_id,
        endpoint=provider_config.base_url or None,
        credential_ref=provider_config.credential_ref,
        capabilities=exact_capabilities,
        config_revision=config_revision,
        config_digest=sha256_digest(canonical_json_bytes(provider_config.model_dump(mode="json"))),
    )
    prompt_values = {}
    if prompt_assembler is not None:
        evidence = prompt_assembler.evidence_for()
        prompt_values = {
            "prompt_profile_id": evidence.profile_id,
            "prompt_profile_version": evidence.profile_version,
            "prompt_profile_digest": evidence.profile_digest,
            "role_prompt_digest": evidence.role_prompt_digest,
            "project_instruction_resolver_version": evidence.project_instruction_resolver_version,
            "project_instruction_sources": evidence.project_instruction_sources,
            "project_instruction_selection_digest": evidence.project_instruction_selection_digest,
        }
    return PreparedAgentRunSpec(
        provider_runtime=provider_runtime,
        run_policy=run_policy,
        run_policy_digest=run_policy_digest(run_policy),
        tool_schema_digest=tool_schema_digest(tools),
        tool_count=len(tools),
        mcp_run_snapshot_ids=mcp_run_snapshot_ids,
        **prompt_values,
    )


class AgentRunPreparationService:
    """Compose one Provider/Model/RunPolicy/ToolSet per AgentRun."""

    def __init__(
        self,
        *,
        global_store,
        registry: AdapterRegistry,
        agent_policy: AgentPolicy,
        credential_resolver: Callable[[str, CredentialRef | None], str | None],
        frozen_credential_resolver: Callable[[str, CredentialRef | None], str | None] | None = None,
        estimate_request_chars,
        tool_factory: Callable[[RunPolicy], ToolExecutor | None],
        legacy: PreparedAgentRunRuntime | None = None,
        workspace_id: str | None = None,
        mcp_factory: Callable[[str, RunPolicy], PreparedMcpRun | None] | None = None,
        mcp_rehydrate_factory: Callable[[AgentRunSnapshot, str], PreparedMcpRun | None]
        | None = None,
        prompt_assembler=None,
    ) -> None:
        self.global_store = global_store
        self.registry = registry
        self.agent_policy = agent_policy
        self.credential_resolver = credential_resolver
        self.frozen_credential_resolver = frozen_credential_resolver or credential_resolver
        self.estimate_request_chars = estimate_request_chars
        self.tool_factory = tool_factory
        self.legacy = legacy
        self.workspace_id = workspace_id
        self.mcp_factory = mcp_factory
        self.mcp_rehydrate_factory = mcp_rehydrate_factory
        self.prompt_assembler = prompt_assembler

    def prepare_new(self, *, agent_run_id: str | None = None) -> PreparedAgentRunRuntime:
        """Prepare the next new AgentRun from the current configuration.

        Reads the active model and its ProviderConfig once per run. The
        credential value is resolved here and never frozen into evidence.
        """
        loaded = self.global_store.load()
        config: GlobalConfig | None = loaded.value
        if config is None or config.active_model is None:
            # No current configuration (explicit provider/model integrations):
            # the boot-time legacy runtime is the compatible answer, exactly like
            # the pre-Stage-6 behavior.
            if self.legacy is not None:
                return self.legacy
            raise ValueError("尚未配置 active_model")
        model = config.active_model
        provider_config = config.providers.get(model.provider_id)
        if provider_config is None:
            raise ValueError(f"未知 Provider: {model.provider_id}")
        if model.model_id not in provider_config.models:
            raise ValueError(f"模型不属于 Provider: {model.model_id}")
        credential = self.credential_resolver(model.provider_id, provider_config.credential_ref)
        if not credential:
            raise ValueError("Provider 凭据不可用")
        capabilities = self.registry.capabilities(provider_config.adapter)
        model_config = provider_config.models[model.model_id]
        exact = exact_model_capabilities(
            provider_config.adapter,
            capabilities,
            model,
            model_config.capabilities,
        )
        run_policy = self.agent_policy.resolve(
            model,
            tool_protocol=exact.tool_protocol,
            multiple_tool_calls=exact.multiple_tool_calls,
        )
        provider = self.registry.create(provider_config, credential)
        context_builder = ContextBuilder(
            run_policy=run_policy,
            estimate_request_chars=self.estimate_request_chars,
            prompt_assembler=self.prompt_assembler,
        )
        tool_executor = self.tool_factory(run_policy)
        mcp_run = None
        if self.mcp_factory is not None and agent_run_id is not None:
            if self.workspace_id is None:
                raise AgentRunPreparationError("MCP preparation workspace is unavailable")
            try:
                mcp_run = self.mcp_factory(agent_run_id, run_policy)
            except AgentRunPreparationError:
                raise
            except Exception as exc:
                raise AgentRunPreparationError("MCP preparation failed") from exc
            tool_executor = self._merge_mcp_tools(tool_executor, mcp_run, agent_run_id=agent_run_id)
        tools = tool_executor.definitions if tool_executor is not None else ()
        spec = build_prepared_spec(
            provider_config=provider_config,
            model=model,
            exact_capabilities=exact,
            config_revision=loaded.revision,
            run_policy=run_policy,
            tools=tools,
            mcp_run_snapshot_ids=mcp_run.snapshot_ids if mcp_run is not None else (),
            prompt_assembler=self.prompt_assembler,
        )
        return PreparedAgentRunRuntime(
            spec=spec,
            provider=provider,
            model=model,
            context_builder=context_builder,
            tool_executor=tool_executor,
            run_policy=run_policy,
            mcp_run=mcp_run,
            agent_run_id=agent_run_id,
        )

    def rehydrate(
        self, snapshot: AgentRunSnapshot, *, agent_run_id: str | None = None
    ) -> PreparedAgentRunRuntime:
        """Rebuild a runtime from stored AgentRun evidence only.

        Runs without frozen provider evidence (pre-Stage-6 snapshots) use the
        compatible boot-time runtime. New runs rebuild exactly: an unresolvable
        frozen CredentialRef makes the run unavailable instead of falling back.
        """
        frozen = snapshot.provider_runtime
        if frozen is None and snapshot.run_policy is None:
            if self.legacy is not None:
                return self.legacy
            raise AgentRunPreparationError("AgentRun has no frozen provider evidence to rehydrate")
        if frozen is None or snapshot.run_policy is None:
            raise AgentRunPreparationError("AgentRun frozen evidence is incomplete")
        if run_policy_digest(snapshot.run_policy) != snapshot.run_policy_digest:
            raise AgentRunPreparationError("AgentRun run-policy evidence is inconsistent")
        credential = self.frozen_credential_resolver(frozen.provider_id, frozen.credential_ref)
        if not credential:
            raise ProviderUnavailableError(
                f"frozen credential for provider {frozen.provider_id} is unavailable"
            )
        provider_config = ProviderConfig(
            adapter=frozen.adapter_id,
            base_url=frozen.endpoint or "",
            credential_ref=frozen.credential_ref,
            models={frozen.model.model_id: ProviderModelConfig(api_model_id=frozen.api_model_id)},
        )
        provider = self.registry.create(provider_config, credential)
        context_builder = ContextBuilder(
            run_policy=snapshot.run_policy,
            estimate_request_chars=self.estimate_request_chars,
            prompt_assembler=self.prompt_assembler,
        )
        tool_executor = self.tool_factory(snapshot.run_policy)
        mcp_run = None
        if snapshot.mcp_run_snapshot_ids:
            if self.mcp_rehydrate_factory is None:
                raise AgentRunPreparationError("AgentRun MCP evidence cannot be rehydrated")
            if agent_run_id is None:
                raise AgentRunPreparationError("AgentRun MCP subject is unavailable")
            try:
                mcp_run = self.mcp_rehydrate_factory(snapshot, agent_run_id)
            except AgentRunPreparationError:
                raise
            except Exception as exc:
                raise AgentRunPreparationError("MCP rehydration failed") from exc
            if mcp_run is None or mcp_run.snapshot_ids != snapshot.mcp_run_snapshot_ids:
                raise AgentRunPreparationError("AgentRun MCP snapshot evidence is inconsistent")
            tool_executor = self._merge_mcp_tools(tool_executor, mcp_run, agent_run_id=agent_run_id)
        tools = tool_executor.definitions if tool_executor is not None else ()
        if snapshot.tool_schema_digest != tool_schema_digest(tools):
            raise AgentRunPreparationError("AgentRun tool schema drifted from frozen evidence")
        spec = PreparedAgentRunSpec(
            provider_runtime=frozen,
            run_policy=snapshot.run_policy,
            run_policy_digest=snapshot.run_policy_digest,
            tool_schema_digest=snapshot.tool_schema_digest,
            tool_count=len(tools),
            skill_selection_ids=snapshot.skill_selection_ids,
            skill_selection_id=snapshot.skill_selection_id,
            skill_context_ids=snapshot.skill_context_ids,
            skill_context_id=snapshot.skill_context_id,
            skill_selection_digest=snapshot.skill_selection_digest,
            skill_context_digest=snapshot.skill_context_digest,
            mcp_run_snapshot_ids=snapshot.mcp_run_snapshot_ids,
            prompt_profile_id=snapshot.prompt_profile_id,
            prompt_profile_version=snapshot.prompt_profile_version,
            prompt_profile_digest=snapshot.prompt_profile_digest,
            role_prompt_digest=snapshot.role_prompt_digest,
            project_instruction_resolver_version=snapshot.project_instruction_resolver_version,
            project_instruction_sources=snapshot.project_instruction_sources,
            project_instruction_selection_digest=snapshot.project_instruction_selection_digest,
        )
        return PreparedAgentRunRuntime(
            spec=spec,
            provider=provider,
            model=frozen.model,
            context_builder=context_builder,
            tool_executor=tool_executor,
            run_policy=snapshot.run_policy,
            mcp_run=mcp_run,
            agent_run_id=agent_run_id,
        )

    def _merge_mcp_tools(
        self,
        tool_executor: ToolExecutor | None,
        mcp_run: PreparedMcpRun | None,
        *,
        agent_run_id: str,
    ) -> ToolExecutor | None:
        if mcp_run is None:
            return tool_executor
        if tool_executor is None or self.workspace_id is None:
            raise AgentRunPreparationError("MCP tools require an ordinary function-tool runtime")
        registry = ToolRegistry()
        for registered in tool_executor.tool_set.tools.values():
            registry.register(registered)
        register_mcp_tools(
            registry,
            mcp_run,
            capability_policy=tool_executor.capability_policy,
            workspace_id=self.workspace_id,
            agent_run_id=agent_run_id,
        )
        return ToolExecutor(
            registry.snapshot(),
            tool_executor.run_policy,
            approval_port=tool_executor.approval_port,
            capability_policy=tool_executor.capability_policy,
            expected_process_isolation=tool_executor.expected_process_isolation,
        )
