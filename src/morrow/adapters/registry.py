"""Dynamic adapter registry: provider presets are data, not core branches."""

from __future__ import annotations

from collections.abc import Callable
from inspect import isawaitable
from typing import Any

from morrow.core.agent_runs import ProviderCapabilities
from morrow.core.models import ModelRef, ProviderConfig
from morrow.core.ports import ModelProvider
from morrow.core.providers import DiscoveredModel, ModelDiscovery
from morrow.runtime.policy import ProviderToolSupport


class AdapterRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[[ProviderConfig, str], ModelProvider]] = {}
        self._tool_support: dict[str, ProviderToolSupport] = {}
        self._capabilities: dict[str, ProviderCapabilities] = {}
        self._discoveries: dict[str, ModelDiscovery] = {}

    def register(
        self,
        adapter_id: str,
        factory: Callable[[ProviderConfig, str], ModelProvider],
        *,
        tool_protocol: str | None = None,
        multiple_tool_calls: bool | None = None,
        capabilities: ProviderCapabilities | None = None,
        discovery: ModelDiscovery | None = None,
    ) -> None:
        if not adapter_id.strip():
            raise ValueError("adapter_id must not be empty")
        if capabilities is not None:
            if tool_protocol is not None and capabilities.tool_protocol != tool_protocol:
                raise ValueError("tool_protocol conflicts with declared Adapter capabilities")
            if (
                multiple_tool_calls is not None
                and capabilities.multiple_tool_calls != multiple_tool_calls
            ):
                raise ValueError("multiple_tool_calls conflicts with declared Adapter capabilities")
        self._factories[adapter_id] = factory
        previous = self._tool_support.get(adapter_id)
        declared = capabilities or self._capabilities.get(adapter_id)
        self._tool_support[adapter_id] = ProviderToolSupport(
            tool_protocol=tool_protocol
            if tool_protocol is not None
            else (
                declared.tool_protocol
                if declared
                else (previous.tool_protocol if previous else "none")
            ),
            multiple_tool_calls=multiple_tool_calls
            if multiple_tool_calls is not None
            else (
                declared.multiple_tool_calls
                if declared
                else (previous.multiple_tool_calls if previous else False)
            ),
            safe_request_chars=declared.safe_request_chars
            if declared
            else (previous.safe_request_chars if previous else None),
        )
        if capabilities is not None:
            self._capabilities[adapter_id] = capabilities
        if discovery is not None:
            self._discoveries[adapter_id] = discovery

    def create(self, config: ProviderConfig, credential: str) -> ModelProvider:
        try:
            factory = self._factories[config.adapter]
        except KeyError as exc:
            raise ValueError(f"未注册的 Adapter: {config.adapter}") from exc
        return factory(config, credential)

    def contains(self, adapter_id: str) -> bool:
        return adapter_id in self._factories

    def tool_support(self, adapter_id: str) -> ProviderToolSupport:
        try:
            return self._tool_support[adapter_id]
        except KeyError as exc:
            raise ValueError(f"未注册的 Adapter: {adapter_id}") from exc

    def capabilities(self, adapter_id: str) -> ProviderCapabilities:
        """Declared capabilities; default derived from tool support when absent."""
        declared = self._capabilities.get(adapter_id)
        if declared is not None:
            return declared
        support = self.tool_support(adapter_id)
        return ProviderCapabilities(
            tool_protocol=support.tool_protocol,
            multiple_tool_calls=support.multiple_tool_calls,
            safe_request_chars=support.safe_request_chars,
        )

    async def discover_models(
        self, config: ProviderConfig, credential: str
    ) -> tuple[DiscoveredModel, ...]:
        """Run an adapter-owned, explicit discovery operation once requested."""

        try:
            discovery = self._discoveries[config.adapter]
        except KeyError as exc:
            raise ValueError(f"Adapter 不支持模型发现: {config.adapter}") from exc
        discovered = discovery(config, credential)
        if isawaitable(discovered):
            discovered = await discovered
        try:
            models = tuple(DiscoveredModel.model_validate(item) for item in discovered)
        except (TypeError, ValueError) as exc:
            raise ValueError("Adapter 返回了无效的模型发现结果") from exc
        ids = [item.model_id for item in models]
        if len(ids) != len(set(ids)):
            raise ValueError("Adapter 返回了重复的模型 ID")
        return models


OPENCODE_GO_PRESET: dict[str, Any] = {
    "preset_id": "opencode-go",
    "provider_id": "opencode-go",
    "adapter": "openai-compatible",
    "base_url": "https://opencode.ai/zen/go/v1",
    "model_id": "deepseek-v4-flash",
    "api_model_id": "deepseek-v4-flash",
}

OPENCODE_GO_MIMO_PRESET: dict[str, Any] = {
    "preset_id": "opencode-go-mimo",
    "provider_id": "opencode-go",
    "adapter": "openai-compatible",
    "base_url": "https://opencode.ai/zen/go/v1",
    "model_id": "mimo-v2.5",
    "api_model_id": "mimo-v2.5",
}

PRESETS: dict[str, dict[str, Any]] = {
    preset["preset_id"]: preset for preset in (OPENCODE_GO_PRESET, OPENCODE_GO_MIMO_PRESET)
}


def provider_model_ref(provider_id: str, config: ProviderConfig, model_id: str) -> ModelRef:
    if model_id not in config.models:
        raise ValueError(f"模型不属于 Provider: {model_id}")
    return ModelRef(provider_id=provider_id, model_id=model_id)
