"""Authenticated Provider controls; keys have a separate write-only endpoint."""

import asyncio
from typing import Literal

from pydantic import Field, SecretStr
from starlette.responses import JSONResponse
from starlette.routing import Route

from morrow.application.providers.control import ProviderControlConflict, ProviderControlError
from morrow.application.providers.settings import (
    execute_probe,
    finish_probe,
    prepare_probe,
    require_revision,
    settings_view,
)
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.models import ModelCapabilityOverrides, ProtocolModel


class RevisionRequest(ProtocolModel):
    expected_revision: int = Field(ge=0, strict=True)


class ProviderAddRequest(RevisionRequest):
    provider_id: str = Field(min_length=1, max_length=64)
    adapter_id: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=2048)


class PresetRequest(RevisionRequest):
    preset_id: str = Field(min_length=1, max_length=128)


class ProviderConfigureRequest(RevisionRequest):
    base_url: str = Field(min_length=1, max_length=2048)


class CredentialRequest(RevisionRequest):
    secret: SecretStr = Field(min_length=1, max_length=16384, repr=False)


class ModelRequest(RevisionRequest):
    model_id: str = Field(min_length=1, max_length=128)
    api_model_id: str | None = Field(default=None, min_length=1, max_length=256)
    capabilities: ModelCapabilityOverrides | None = None


class ModelActionRequest(RevisionRequest):
    model_id: str = Field(min_length=1, max_length=128)
    action: Literal["use", "remove"]


def provider_routes(host, parse):
    probes = asyncio.Semaphore(2)

    def service():
        return host.context.application.provider_service

    def changed():
        registry = host.context.workspaces
        for c in registry.contexts.values():
            for sid in tuple(c.chat.streams.states):
                c.chat.streams.changed(sid)

    def apply(body, action):
        svc = service()
        require_revision(svc, body.expected_revision)
        try:
            action(svc)
        except ProviderControlConflict:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT, "配置已变化，请刷新后重试"
            ) from None
        except ProviderControlError as exc:
            raise ApplicationError(ApplicationErrorCode.INVALID, str(exc)) from None
        changed()
        return settings_view(svc)

    async def catalog(request):
        return JSONResponse(await host.execute_query(lambda: settings_view(service())))

    async def add(request):
        body = await parse(request, ProviderAddRequest)
        return JSONResponse(
            await host.execute_command(
                lambda: apply(
                    body,
                    lambda s: s.add_provider(
                        body.provider_id, adapter_id=body.adapter_id, base_url=body.base_url
                    ),
                )
            )
        )

    async def preset(request):
        body = await parse(request, PresetRequest)
        return JSONResponse(
            await host.execute_command(
                lambda: apply(body, lambda s: s.add_preset_saved(body.preset_id))
            )
        )

    async def configure(request):
        body = await parse(request, ProviderConfigureRequest)
        return JSONResponse(
            await host.execute_command(
                lambda: apply(
                    body,
                    lambda s: s.configure_saved(
                        request.path_params["provider_id"], base_url=body.base_url
                    ),
                )
            )
        )

    async def credential(request):
        body = await parse(request, CredentialRequest)
        # No command receipt or request digest includes a secret, even on failure.
        await host.execute_command(
            lambda: apply(
                body,
                lambda s: s.configure_saved(
                    request.path_params["provider_id"], secret=body.secret.get_secret_value()
                ),
            )
        )
        return JSONResponse({"saved": True}, headers={"Cache-Control": "no-store"})

    async def remove(request):
        body = await parse(request, RevisionRequest)
        return JSONResponse(
            await host.execute_command(
                lambda: apply(body, lambda s: s.remove_provider(request.path_params["provider_id"]))
            )
        )

    async def model_add(request):
        body = await parse(request, ModelRequest)
        return JSONResponse(
            await host.execute_command(
                lambda: apply(
                    body,
                    lambda s: (
                        s.configure_model
                        if request.url.path.endswith("/configure-model")
                        else s.add_model
                    )(
                        request.path_params["provider_id"],
                        body.model_id,
                        api_model_id=body.api_model_id,
                        capabilities=body.capabilities,
                    ),
                )
            )
        )

    async def model_action(request):
        body = await parse(request, ModelActionRequest)
        return JSONResponse(
            await host.execute_command(
                lambda: apply(
                    body,
                    lambda s: (s.use_model if body.action == "use" else s.remove_model)(
                        request.path_params["provider_id"], body.model_id
                    ),
                )
            )
        )

    def finish_and_notify(prepared, result):
        value = finish_probe(service(), prepared, result)
        changed()
        return value

    async def probe(request):
        if request.path_params["operation"] not in {"test", "discover"}:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "操作不存在")
        body = await parse(request, RevisionRequest)
        if probes.locked():
            raise ApplicationError(ApplicationErrorCode.BUSY, "已有连接检查，请稍后重试")
        async with probes:
            prepared = await host.execute_query(
                lambda: prepare_probe(
                    service(),
                    request.path_params["provider_id"],
                    request.path_params["operation"],
                    body.expected_revision,
                )
            )
            result = await execute_probe(prepared)
            return JSONResponse(
                await host.execute_command(lambda: finish_and_notify(prepared, result))
            )

    root = "/v1/providers"
    return [
        Route(root, catalog),
        Route(root, add, methods=["POST"]),
        Route(root + "/presets", preset, methods=["POST"]),
        Route(root + "/{provider_id}/configure", configure, methods=["POST"]),
        Route(root + "/{provider_id}/credentials", credential, methods=["POST"]),
        Route(root + "/{provider_id}/remove", remove, methods=["POST"]),
        Route(root + "/{provider_id}/models", model_add, methods=["POST"]),
        Route(root + "/{provider_id}/configure-model", model_add, methods=["POST"]),
        Route(root + "/{provider_id}/model", model_action, methods=["POST"]),
        Route(root + "/{provider_id}/{operation}", probe, methods=["POST"]),
    ]
