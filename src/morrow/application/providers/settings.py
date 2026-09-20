"""GUI control projection and split-phase, explicit Provider probes.

Only execute_probe runs away from the Core owner. Its immutable input carries
no store handle; credentials never enter responses, receipts or event payloads.
"""

from dataclasses import dataclass, field
from typing import Literal

from morrow.adapters.registry import PRESETS
from morrow.core.agent_runs import exact_model_capabilities
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.models import LastTestResult, ModelErrorCode, ModelRef, provider_error_message
from morrow.core.preference_documents import GlobalConfig


@dataclass(frozen=True, repr=False)
class ProviderProbe:
    provider_id: str
    operation: Literal["test", "discover"]
    current: GlobalConfig
    registry: object = field(repr=False)
    credential: str = field(repr=False)


def require_revision(service, revision):
    current = service.list()
    if current.revision != revision:
        raise ApplicationError(ApplicationErrorCode.CONFLICT, "配置已变化，请刷新后重试")
    return current


def settings_view(service):
    snapshot = service.catalog_snapshot()
    current = snapshot.config
    available = dict(snapshot.credential_availability)
    return {
        "revision": current.revision,
        "active_model": current.active_model.model_dump() if current.active_model else None,
        "adapters": list(service.registry.catalog()),
        "presets": list(PRESETS.values()),
        "providers": [
            {
                "provider_id": pid,
                "adapter": config.adapter,
                "base_url": config.base_url,
                "credential_configured": available[pid],
                "models": [
                    {
                        "model_id": mid,
                        "api_model_id": model.api_model_id,
                        "effective_capabilities": exact_model_capabilities(
                            config.adapter,
                            service.registry.capabilities(config.adapter),
                            ModelRef(provider_id=pid, model_id=mid),
                            model.capabilities,
                        ).model_dump(mode="json"),
                        "capabilities": model.capabilities.model_dump(mode="json")
                        if model.capabilities
                        else None,
                    }
                    for mid, model in sorted(config.models.items())
                ],
                "last_test": config.last_test.model_dump(mode="json") if config.last_test else None,
            }
            for pid, config in sorted(current.providers.items())
        ],
    }


def prepare_probe(service, provider_id, operation, expected_revision):
    current = require_revision(service, expected_revision)
    config = current.providers.get(provider_id)
    if config is None:
        raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Provider 不存在")
    credential = service._read_credential(provider_id, config.credential_ref)
    if not credential:
        raise ApplicationError(ApplicationErrorCode.INVALID, "请先配置 Provider 凭据")
    if operation == "test" and not config.models:
        raise ApplicationError(ApplicationErrorCode.INVALID, "请先添加模型")
    return ProviderProbe(provider_id, operation, current, service.registry, credential)


async def execute_probe(probe):
    import asyncio

    config = probe.current.providers[probe.provider_id]
    try:
        async with asyncio.timeout(30):
            if probe.operation == "discover":
                return await probe.registry.discover_models(config, probe.credential)
            from morrow.services.provider import ProviderService

            model = ModelRef(provider_id=probe.provider_id, model_id=next(iter(config.models)))
            await probe.registry.create(config, probe.credential).complete(
                model, ProviderService._probe_messages()
            )
            return LastTestResult(ok=True)
    except Exception:
        # External adapter exceptions can carry a key, URL or hidden reasoning.
        if probe.operation == "test":
            return LastTestResult(
                ok=False,
                error_code=ModelErrorCode.INTERNAL,
                message=provider_error_message(ModelErrorCode.INTERNAL),
            )
        raise ApplicationError(
            ApplicationErrorCode.INVALID, "模型发现失败，请检查连接后重试"
        ) from None


def finish_probe(service, probe, result):
    from morrow.core.models import ProviderModelConfig

    current = require_revision(service, probe.current.revision)
    config = current.providers[probe.provider_id]
    if probe.operation == "test":
        updated = config.model_copy(update={"last_test": result})
    else:
        if not result:
            raise ApplicationError(ApplicationErrorCode.INVALID, "未发现可用模型，配置未修改")
        # Reuse manual-add capability validation and preserve exact configured mappings.
        models = dict(config.models)
        for item in result:
            service._require_capability_override(
                service.registry, config.adapter, item.capabilities
            )
            if item.model_id not in models:
                models[item.model_id] = ProviderModelConfig(
                    api_model_id=item.api_model_id, capabilities=item.capabilities
                )
        updated = config.model_copy(update={"models": models})
    service._commit(
        current,
        lambda value: value.model_copy(
            update={"providers": {**value.providers, probe.provider_id: updated}}
        ),
    )
    return settings_view(service)
