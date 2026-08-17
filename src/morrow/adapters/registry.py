"""Dynamic adapter registry: provider presets are data, not core branches."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from morrow.core.models import ModelRef, ProviderConfig
from morrow.core.ports import ModelProvider
from morrow.runtime.policy import ProviderToolSupport


class AdapterRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[[ProviderConfig, str], ModelProvider]] = {}
        self._tool_support: dict[str, ProviderToolSupport] = {}

    def register(
        self,
        adapter_id: str,
        factory: Callable[[ProviderConfig, str], ModelProvider],
        *,
        tool_protocol: str | None = None,
        multiple_tool_calls: bool | None = None,
    ) -> None:
        if not adapter_id.strip():
            raise ValueError("adapter_id must not be empty")
        self._factories[adapter_id] = factory
        previous = self._tool_support.get(adapter_id)
        self._tool_support[adapter_id] = ProviderToolSupport(
            tool_protocol=tool_protocol
            if tool_protocol is not None
            else (previous.tool_protocol if previous else "none"),
            multiple_tool_calls=multiple_tool_calls
            if multiple_tool_calls is not None
            else (previous.multiple_tool_calls if previous else False),
        )

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


OPENCODE_GO_PRESET: dict[str, Any] = {
    "preset_id": "opencode-go",
    "provider_id": "opencode-go",
    "adapter": "openai-compatible",
    "base_url": "https://opencode.ai/zen/go/v1",
    "model_id": "deepseek-v4-flash",
    "api_model_id": "deepseek-v4-flash",
}

PRESETS: dict[str, dict[str, Any]] = {OPENCODE_GO_PRESET["preset_id"]: OPENCODE_GO_PRESET}


def provider_model_ref(provider_id: str, config: ProviderConfig, model_id: str) -> ModelRef:
    if model_id not in config.models:
        raise ValueError(f"模型不属于 Provider: {model_id}")
    return ModelRef(provider_id=provider_id, model_id=model_id)
