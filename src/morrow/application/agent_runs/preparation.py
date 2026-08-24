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
from morrow.runtime.tools import ToolExecutor


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

    def close(self) -> None:
        """Bounded cleanup owned by the run consumer (idempotent).

        Stage 6 v1 holds no pooled resources; the MCP lazy session pool and
        Skill script processes attach their cleanup handles here later.
        """


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
    return PreparedAgentRunSpec(
        provider_runtime=provider_runtime,
        run_policy=run_policy,
        run_policy_digest=run_policy_digest(run_policy),
        tool_schema_digest=tool_schema_digest(tools),
        tool_count=len(tools),
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
        estimate_request_chars,
        tool_factory: Callable[[RunPolicy], ToolExecutor | None],
        legacy: PreparedAgentRunRuntime | None = None,
    ) -> None:
        self.global_store = global_store
        self.registry = registry
        self.agent_policy = agent_policy
        self.credential_resolver = credential_resolver
        self.estimate_request_chars = estimate_request_chars
        self.tool_factory = tool_factory
        self.legacy = legacy

    def prepare_new(self) -> PreparedAgentRunRuntime:
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
        exact = exact_model_capabilities(provider_config.adapter, capabilities, model)
        run_policy = self.agent_policy.resolve(
            model,
            tool_protocol=exact.tool_protocol,
            multiple_tool_calls=exact.multiple_tool_calls,
        )
        provider = self.registry.create(provider_config, credential)
        context_builder = ContextBuilder(
            run_policy=run_policy,
            estimate_request_chars=self.estimate_request_chars,
        )
        tool_executor = self.tool_factory(run_policy)
        tools = tool_executor.definitions if tool_executor is not None else ()
        spec = build_prepared_spec(
            provider_config=provider_config,
            model=model,
            exact_capabilities=exact,
            config_revision=loaded.revision,
            run_policy=run_policy,
            tools=tools,
        )
        return PreparedAgentRunRuntime(
            spec=spec,
            provider=provider,
            model=model,
            context_builder=context_builder,
            tool_executor=tool_executor,
            run_policy=run_policy,
        )

    def rehydrate(self, snapshot: AgentRunSnapshot) -> PreparedAgentRunRuntime:
        """Rebuild a runtime from stored AgentRun evidence only.

        Runs without frozen provider evidence (pre-Stage-6 snapshots) use the
        compatible boot-time runtime. New runs rebuild exactly: an unresolvable
        frozen CredentialRef makes the run unavailable instead of falling back.
        """
        frozen = snapshot.provider_runtime
        if frozen is None or snapshot.run_policy is None:
            if self.legacy is not None:
                return self.legacy
            raise AgentRunPreparationError("AgentRun has no frozen provider evidence to rehydrate")
        if run_policy_digest(snapshot.run_policy) != snapshot.run_policy_digest:
            raise AgentRunPreparationError("AgentRun run-policy evidence is inconsistent")
        credential = self.credential_resolver(frozen.provider_id, frozen.credential_ref)
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
        )
        tool_executor = self.tool_factory(snapshot.run_policy)
        tools = tool_executor.definitions if tool_executor is not None else ()
        if snapshot.tool_schema_digest != tool_schema_digest(tools):
            raise AgentRunPreparationError("AgentRun tool schema drifted from frozen evidence")
        spec = PreparedAgentRunSpec(
            provider_runtime=frozen,
            run_policy=snapshot.run_policy,
            run_policy_digest=snapshot.run_policy_digest,
            tool_schema_digest=snapshot.tool_schema_digest,
            tool_count=len(tools),
        )
        return PreparedAgentRunRuntime(
            spec=spec,
            provider=provider,
            model=frozen.model,
            context_builder=context_builder,
            tool_executor=tool_executor,
            run_policy=snapshot.run_policy,
        )
