"""Local desktop configuration and exact-model availability, without creating a run."""

from morrow.core.agent_runs import exact_model_capabilities
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.models import StateWriteStatus
from morrow.core.runtime_policy import ComputerUseMode, ComputerUseSettings, RuntimePolicyOverrides
from morrow.runtime.policy import resolve_computer_use_settings


class ComputerUseSettingsService:
    def __init__(self, application, *, preflight):
        self.application = application
        self.preflight = preflight

    def view(self, model):
        loaded = self.application.global_store.load()
        config = loaded.value
        if config is None:
            raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "桌面配置需要修复")
        settings = resolve_computer_use_settings(config.runtime_policy)
        # A disabled feature must not load its optional native SDK even for status.
        from morrow.core.computer_use import ComputerUsePreflight

        host = (
            self.preflight(settings)
            if settings.enabled
            else ComputerUsePreflight(status="unavailable", reason="disabled")
        )
        function_tools = images = False
        model_error = "model_unavailable"
        provider = config.providers.get(model.provider_id) if model is not None else None
        if provider is not None and model.model_id in provider.models:
            try:
                exact = exact_model_capabilities(
                    provider.adapter,
                    self.application.registry.capabilities(provider.adapter),
                    model,
                    provider.models[model.model_id].capabilities,
                )
            except (KeyError, ValueError):
                model_error = "model_unavailable"
            else:
                function_tools = exact.tool_protocol == "openai_function"
                images = "image" in exact.input_types
                model_error = (
                    "function_tools_required"
                    if not function_tools
                    else "images_not_supported"
                    if settings.mode is ComputerUseMode.HYBRID and not images
                    else None
                )
        return {
            "revision": loaded.revision,
            "settings": settings.model_dump(mode="json"),
            "host": host.model_dump(mode="json"),
            "model": model.model_dump(mode="json") if model is not None else None,
            "model_capabilities": {"function_tools": function_tools, "images": images},
            "model_error": model_error,
            "required_permission": "full-access-manual",
            "configuration_scope": "global",
        }

    def put(self, settings, *, expected_revision):
        if not isinstance(settings, ComputerUseSettings):
            raise ApplicationError(ApplicationErrorCode.INVALID, "桌面设置无效")
        settings = ComputerUseSettings.model_validate(settings.model_dump(), strict=True)
        result = self.application.global_store.update(
            lambda value: value.model_copy(
                update={
                    "runtime_policy": (value.runtime_policy or RuntimePolicyOverrides()).model_copy(
                        update={"computer_use": settings}
                    )
                }
            ),
            expected_revision=expected_revision,
        )
        if result.status is not StateWriteStatus.OK:
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "全局设置已变化，请刷新后重试")
