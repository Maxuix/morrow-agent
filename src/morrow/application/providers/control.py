"""Versioned Provider/Model control operations used by the CLI and composition root."""

from __future__ import annotations

from morrow.core.models import (
    GlobalConfig,
    ModelCapabilityOverrides,
    ModelErrorCode,
    ModelProviderError,
    ModelRef,
    ProviderConfig,
    ProviderModelConfig,
    StateWriteStatus,
    provider_error_message,
)
from morrow.core.providers import (
    validate_base_url,
    validate_model_id,
    validate_provider_id,
)


class ProviderControlError(ValueError):
    """Safe, actionable control-plane failure."""


class ProviderControlConflict(ProviderControlError):
    """The YAML revision changed while a control operation was being committed."""


class ProviderControlMixin:
    """Mixin kept separate from legacy onboarding for a small composition surface."""

    def _commit(self, current: GlobalConfig, mutator) -> GlobalConfig:
        result = self.global_store.update(mutator, expected_revision=current.revision)
        if result.status is StateWriteStatus.REVISION_CONFLICT:
            raise ProviderControlConflict("配置已变化，请重新读取后重试")
        if result.status is not StateWriteStatus.OK:
            raise ProviderControlError("Provider 配置保存失败")
        return result.value

    @staticmethod
    def _require_adapter(registry, adapter_id: str) -> None:
        if not adapter_id.strip():
            raise ProviderControlError("Adapter ID 不能为空")
        if not registry.contains(adapter_id):
            raise ProviderControlError(f"未注册 Adapter: {adapter_id}")

    @staticmethod
    def _require_capability_override(registry, adapter_id: str, overrides) -> None:
        if overrides is None:
            return
        if not isinstance(overrides, ModelCapabilityOverrides):
            try:
                overrides = ModelCapabilityOverrides.model_validate(overrides)
            except ValueError as exc:
                raise ProviderControlError("模型能力覆盖项无效") from exc
        if overrides.tool_protocol not in (None, "none"):
            declared = registry.capabilities(adapter_id).tool_protocol
            if declared != overrides.tool_protocol:
                raise ProviderControlError("模型能力覆盖项不能发明 Adapter 不支持的工具协议")

    def add_provider(
        self,
        provider_id: str,
        *,
        adapter_id: str,
        base_url: str,
        secret: str | None = None,
    ) -> ProviderConfig:
        provider_id = validate_provider_id(provider_id)
        self._require_adapter(self.registry, adapter_id)
        try:
            base_url = validate_base_url(base_url)
        except ValueError as exc:
            raise ProviderControlError(str(exc)) from None
        current = self.list()
        if provider_id in current.providers:
            raise ProviderControlError(f"Provider 已存在: {provider_id}")
        config = ProviderConfig(adapter=adapter_id, base_url=base_url)
        credential_ref = None
        if secret is not None:
            if not secret.strip():
                raise ProviderControlError("凭据不能为空")
            credential_ref = self._ref(provider_id)
            config = config.model_copy(update={"credential_ref": credential_ref})
            self.credentials.set(credential_ref.ref, secret)
        try:
            self._commit(
                current,
                lambda value: value.model_copy(
                    update={"providers": {**value.providers, provider_id: config}}
                ),
            )
        except Exception:
            if credential_ref is not None:
                self.credentials.delete(credential_ref.ref)
            raise
        return config

    def remove_provider(self, provider_id: str) -> None:
        provider_id = validate_provider_id(provider_id)
        current = self.list()
        config = current.providers.get(provider_id)
        if config is None:
            raise ProviderControlError(f"未知 Provider: {provider_id}")
        if current.active_model and current.active_model.provider_id == provider_id:
            raise ProviderControlError("不能移除当前 active_model 所属的 Provider")
        self._commit(
            current,
            lambda value: value.model_copy(
                update={
                    "providers": {
                        key: item for key, item in value.providers.items() if key != provider_id
                    }
                }
            ),
        )
        ref = config.credential_ref
        if ref and not any(
            item.credential_ref and item.credential_ref.ref == ref.ref
            for item in current.providers.values()
            if item is not config
        ):
            self.credentials.delete(ref.ref)

    def add_model(
        self,
        provider_id: str,
        model_id: str,
        *,
        api_model_id: str | None = None,
        capabilities: ModelCapabilityOverrides | dict | None = None,
    ) -> ModelRef:
        provider_id = validate_provider_id(provider_id)
        model_id = validate_model_id(model_id)
        api_model_id = validate_model_id(api_model_id or model_id)
        current = self.list()
        provider = current.providers.get(provider_id)
        if provider is None:
            raise ProviderControlError(f"未知 Provider: {provider_id}")
        self._require_adapter(self.registry, provider.adapter)
        self._require_capability_override(self.registry, provider.adapter, capabilities)
        parsed_capabilities = (
            capabilities
            if isinstance(capabilities, ModelCapabilityOverrides)
            else ModelCapabilityOverrides.model_validate(capabilities)
            if capabilities is not None
            else None
        )
        if model_id in provider.models:
            raise ProviderControlError(f"模型已存在: {provider_id}/{model_id}")
        next_model = ProviderModelConfig(
            api_model_id=api_model_id,
            capabilities=parsed_capabilities,
        )
        self._commit(
            current,
            lambda value: value.model_copy(
                update={
                    "providers": {
                        **value.providers,
                        provider_id: provider.model_copy(
                            update={"models": {**provider.models, model_id: next_model}}
                        ),
                    }
                }
            ),
        )
        return ModelRef(provider_id=provider_id, model_id=model_id)

    def remove_model(self, provider_id: str, model_id: str) -> None:
        provider_id = validate_provider_id(provider_id)
        model_id = validate_model_id(model_id)
        current = self.list()
        provider = current.providers.get(provider_id)
        if provider is None or model_id not in provider.models:
            raise ProviderControlError(f"未知模型: {provider_id}/{model_id}")
        if current.active_model == ModelRef(provider_id=provider_id, model_id=model_id):
            raise ProviderControlError("不能移除当前 active_model")
        self._commit(
            current,
            lambda value: value.model_copy(
                update={
                    "providers": {
                        **value.providers,
                        provider_id: provider.model_copy(
                            update={
                                "models": {
                                    key: item
                                    for key, item in provider.models.items()
                                    if key != model_id
                                }
                            }
                        ),
                    }
                }
            ),
        )

    def use_model(self, provider_id: str, model_id: str) -> ModelRef:
        provider_id = validate_provider_id(provider_id)
        model_id = validate_model_id(model_id)
        current = self.list()
        provider = current.providers.get(provider_id)
        if provider is None or model_id not in provider.models:
            raise ProviderControlError(f"未知模型: {provider_id}/{model_id}")
        model = ModelRef(provider_id=provider_id, model_id=model_id)
        self._commit(current, lambda value: value.model_copy(update={"active_model": model}))
        return model

    async def sync_models_async(self, provider_id: str) -> tuple[ModelRef, ...]:
        provider_id = validate_provider_id(provider_id)
        current = self.list()
        provider = current.providers.get(provider_id)
        if provider is None:
            raise ProviderControlError(f"未知 Provider: {provider_id}")
        credential = self._read_credential(provider_id, provider.credential_ref)
        if not credential:
            raise ProviderControlError("Provider 凭据不可用")
        try:
            discovered = await self.registry.discover_models(provider, credential)
        except ModelProviderError as exc:
            raise ProviderControlError(provider_error_message_safe(exc.code)) from None
        except ValueError:
            raise
        except Exception:
            raise ProviderControlError("模型发现失败，请稍后重试") from None
        if not discovered:
            raise ProviderControlError("未发现可用模型，配置未修改")
        models = {}
        for item in discovered:
            previous = provider.models.get(item.model_id)
            caps = (
                item.capabilities
                if item.capabilities is not None
                else (previous.capabilities if previous else None)
            )
            self._require_capability_override(self.registry, provider.adapter, caps)
            models[item.model_id] = ProviderModelConfig(
                api_model_id=item.api_model_id,
                capabilities=caps,
            )
        active = current.active_model
        if active and active.provider_id == provider_id and active.model_id not in models:
            models[active.model_id] = provider.models[active.model_id]
        self._commit(
            current,
            lambda value: value.model_copy(
                update={
                    "providers": {
                        **value.providers,
                        provider_id: provider.model_copy(update={"models": models}),
                    }
                }
            ),
        )
        return tuple(
            ModelRef(provider_id=provider_id, model_id=item.model_id) for item in discovered
        )

    def sync_models(self, provider_id: str) -> tuple[ModelRef, ...]:
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.sync_models_async(provider_id))
        raise RuntimeError("在异步上下文中请使用 sync_models_async")

    # Verbose aliases keep the control-plane vocabulary discoverable to callers
    # without changing the legacy ``ProviderService.add`` preset API.
    def provider_add(self, provider_id: str, **kwargs) -> ProviderConfig:
        return self.add_provider(provider_id, **kwargs)

    def provider_remove(self, provider_id: str) -> None:
        self.remove_provider(provider_id)

    def model_add(self, provider_id: str, model_id: str, **kwargs) -> ModelRef:
        return self.add_model(provider_id, model_id, **kwargs)

    def model_remove(self, provider_id: str, model_id: str) -> None:
        self.remove_model(provider_id, model_id)

    def model_use(self, provider_id: str, model_id: str) -> ModelRef:
        return self.use_model(provider_id, model_id)

    def model_sync(self, provider_id: str) -> tuple[ModelRef, ...]:
        return self.sync_models(provider_id)


def provider_error_message_safe(code: ModelErrorCode) -> str:
    return provider_error_message(code)


__all__ = ["ProviderControlConflict", "ProviderControlError", "ProviderControlMixin"]
