"""Dynamic adapter registry: provider presets are data, not core branches."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from morrow.core.models import ModelRef, ProviderConfig
from morrow.core.ports import ModelProvider


class AdapterRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[[ProviderConfig, str], ModelProvider]] = {}

    def register(
        self, adapter_id: str, factory: Callable[[ProviderConfig, str], ModelProvider]
    ) -> None:
        if not adapter_id.strip():
            raise ValueError("adapter_id must not be empty")
        self._factories[adapter_id] = factory

    def create(self, config: ProviderConfig, credential: str) -> ModelProvider:
        try:
            factory = self._factories[config.adapter]
        except KeyError as exc:
            raise ValueError(f"未注册的 Adapter: {config.adapter}") from exc
        return factory(config, credential)

    def contains(self, adapter_id: str) -> bool:
        return adapter_id in self._factories


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
