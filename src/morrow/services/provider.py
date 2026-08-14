"""Provider onboarding and local provider/model management."""

from __future__ import annotations

import asyncio
import secrets

from morrow.adapters.credentials.keyring import environment_credential
from morrow.adapters.registry import PRESETS, AdapterRegistry
from morrow.core.models import (
    CredentialRef,
    GlobalConfig,
    LastTestResult,
    Message,
    ModelErrorCode,
    ModelProviderError,
    ModelRef,
    ProviderConfig,
    ProviderModelConfig,
)


class ProviderService:
    def __init__(
        self, global_store, credentials, registry: AdapterRegistry, credential_resolver=None
    ) -> None:
        self.global_store = global_store
        self.credentials = credentials
        self.registry = registry
        self.credential_resolver = credential_resolver or self._resolve_credential

    def _resolve_credential(self, provider_id: str, credential_ref) -> str | None:
        configured = environment_credential(provider_id)
        if configured:
            return configured
        return self.credentials.get(credential_ref.ref) if credential_ref else None

    def credential_available(self, provider_id: str) -> bool:
        config = self.provider(provider_id)
        return bool(self.credential_resolver(provider_id, config.credential_ref))

    def _ref(self, provider_id: str) -> CredentialRef:
        return CredentialRef(ref=f"provider:{provider_id}:{secrets.token_hex(4)}")

    @staticmethod
    def _probe_messages() -> list[Message]:
        return [Message(role="user", content="请回复：连接测试成功。")]

    async def add_async(
        self, preset_id: str, secret: str, *, activate_if_empty: bool = True
    ) -> ModelRef:
        preset = PRESETS.get(preset_id)
        if not preset:
            raise ValueError(f"未知 Provider 预设: {preset_id}")
        provider_id = preset["provider_id"]
        model_id = preset["model_id"]
        config = ProviderConfig(
            adapter=preset["adapter"],
            base_url=preset["base_url"],
            credential_ref=self._ref(provider_id),
            models={model_id: ProviderModelConfig(api_model_id=preset["api_model_id"])},
        )
        if not self.registry.contains(config.adapter):
            raise ValueError(f"未注册 Adapter: {config.adapter}")
        self.credentials.set(config.credential_ref.ref, secret)
        try:
            provider = self.registry.create(config, secret)
            model = ModelRef(provider_id=provider_id, model_id=model_id)
            # Explicit test before publishing usable configuration.
            await provider.complete(model, self._probe_messages())
        except Exception:
            self.credentials.delete(config.credential_ref.ref)
            raise
        current = self.global_store.load()
        existing_active = current.value.active_model if current.value else None
        result = self.global_store.update(
            lambda value: value.model_copy(
                update={
                    "providers": {
                        **value.providers,
                        provider_id: config.model_copy(
                            update={"last_test": LastTestResult(ok=True)}
                        ),
                    },
                    "active_model": existing_active
                    if existing_active or not activate_if_empty
                    else model,
                }
            ),
            expected_revision=current.revision,
        )
        if result.status.value != "ok":
            self.credentials.delete(config.credential_ref.ref)
            raise RuntimeError(result.error or result.status.value)
        return model

    def add(self, preset_id: str, secret: str, *, activate_if_empty: bool = True) -> ModelRef:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.add_async(preset_id, secret, activate_if_empty=activate_if_empty)
            )
        raise RuntimeError("在异步上下文中请使用 add_async")

    async def configure_async(
        self,
        provider_id: str,
        *,
        secret: str | None = None,
        base_url: str | None = None,
        replace_credential: bool = False,
    ) -> None:
        current = self.list()
        old = current.providers.get(provider_id)
        if not old:
            raise ValueError(f"未知 Provider: {provider_id}")
        next_config = old.model_copy(update={"base_url": base_url or old.base_url})
        new_ref = None
        credential = None
        if replace_credential and environment_credential(provider_id):
            raise ValueError("环境变量凭据正在生效；请先取消该环境变量再替换凭据")
        if replace_credential and secret is None:
            raise ValueError("替换凭据需要新的隐藏凭据值")
        if secret is not None:
            new_ref = self._ref(provider_id)
            self.credentials.set(new_ref.ref, secret)
            next_config = next_config.model_copy(update={"credential_ref": new_ref})
            credential = secret
        else:
            credential = self.credential_resolver(provider_id, old.credential_ref)
        if not credential:
            raise ValueError("Provider 凭据不可用")
        model_id = next(iter(next_config.models))
        model = ModelRef(provider_id=provider_id, model_id=model_id)
        try:
            await self.registry.create(next_config, credential).complete(
                model, self._probe_messages()
            )
            next_config = next_config.model_copy(update={"last_test": LastTestResult(ok=True)})
            result = self.global_store.update(
                lambda value: value.model_copy(
                    update={"providers": {**value.providers, provider_id: next_config}}
                ),
                expected_revision=current.revision,
            )
            if result.status.value != "ok":
                raise RuntimeError(result.error or result.status.value)
        except Exception:
            if new_ref:
                self.credentials.delete(new_ref.ref)
            raise

    def configure(
        self,
        provider_id: str,
        *,
        secret: str | None = None,
        base_url: str | None = None,
        replace_credential: bool = False,
    ) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(
                self.configure_async(
                    provider_id,
                    secret=secret,
                    base_url=base_url,
                    replace_credential=replace_credential,
                )
            )
            return
        raise RuntimeError("在异步上下文中请使用 configure_async")

    async def test_async(self, provider_id: str) -> LastTestResult:
        current = self.list()
        config = current.providers.get(provider_id)
        if not config:
            raise ValueError("Provider 凭据不可用")
        credential = self.credential_resolver(provider_id, config.credential_ref)
        if not credential:
            raise ValueError("Provider 凭据不可用")
        model_id = next(iter(config.models))
        try:
            await self.registry.create(config, credential).complete(
                ModelRef(provider_id=provider_id, model_id=model_id), self._probe_messages()
            )
        except Exception as exc:
            code = exc.code if isinstance(exc, ModelProviderError) else ModelErrorCode.INTERNAL
            result = LastTestResult(
                ok=False,
                error_code=code,
                message="模型连接测试失败",
            )
        else:
            result = LastTestResult(ok=True)
        updated = self.global_store.update(
            lambda value: value.model_copy(
                update={
                    "providers": {
                        **value.providers,
                        provider_id: value.providers[provider_id].model_copy(
                            update={"last_test": result}
                        ),
                    }
                }
            ),
            expected_revision=current.revision,
        )
        if updated.status.value != "ok":
            raise RuntimeError(updated.error or updated.status.value)
        return result

    def test(self, provider_id: str) -> LastTestResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.test_async(provider_id))
        raise RuntimeError("在异步上下文中请使用 test_async")

    def list(self) -> GlobalConfig:
        result = self.global_store.load()
        if not result.value:
            return GlobalConfig()
        return result.value

    def current_model(self) -> ModelRef | None:
        return self.list().active_model

    def provider(self, provider_id: str) -> ProviderConfig:
        config = self.list().providers.get(provider_id)
        if not config:
            raise ValueError(f"未知 Provider: {provider_id}")
        return config

    def build_active(self):
        config = self.list()
        if not config.active_model:
            raise ValueError("尚未配置 active_model")
        provider_config = config.providers[config.active_model.provider_id]
        credential = self.credential_resolver(
            config.active_model.provider_id, provider_config.credential_ref
        )
        if not credential:
            raise ValueError("Provider 凭据不可用")
        return self.registry.create(provider_config, credential), config.active_model
