"""Versioned defaults and field-wise resolution, owned by the Core loop."""

from morrow.adapters.state.extension_yaml import ExtensionYamlConflict, ExtensionYamlStore
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.models import ChatSettings, GenerationOptions, StateWriteStatus


class ChatSettingsService:
    def __init__(self, application, journal, workspace_id, *, default_permission="manual"):
        self.application = application
        self.journal = journal
        self.workspace_id = workspace_id
        self.extensions = ExtensionYamlStore(application.data_root.root)
        self.default_permission = default_permission
        from morrow.adapters.local.sandbox import default_sandbox_backend

        self.sandbox_probe = lambda: default_sandbox_backend().probe().supported

    def documents(self, session_id):
        config = self.application.global_store.load().value
        workspace = self.extensions.load_workspace(self.workspace_id).value
        if config is None or workspace is None:
            raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "设置配置需要修复")
        session, revision = self.journal.chat_settings.get(self.workspace_id, session_id)
        return {
            "session": (session, revision),
            "workspace": (workspace.chat_settings, workspace.revision),
            "global": (
                config.chat_settings.model_copy(update={"model": config.active_model}),
                config.revision,
            ),
        }

    def resolve(self, session_id, explicit=None):
        docs = self.documents(session_id)
        layers = [("explicit", (explicit or ChatSettings(), 0)), *docs.items()]
        effective = {
            "model": None,
            "generation": GenerationOptions(),
            "permission": self.default_permission,
        }
        sources = {key: {"scope": "adapter", "revision": 0} for key in effective}
        for key in effective:
            for scope, (settings, revision) in layers:
                value = getattr(settings, key)
                if value is not None:
                    effective[key] = value
                    sources[key] = {"scope": scope, "revision": revision}
                    break
        return ChatSettings(**effective), sources

    def view(self, session_id):
        effective, sources = self.resolve(session_id)
        return {
            "documents": {
                scope: {"settings": value.model_dump(mode="json"), "revision": revision}
                for scope, (value, revision) in self.documents(session_id).items()
            },
            "effective": effective.model_dump(mode="json"),
            "sources": sources,
            "permission_presets": self.permission_presets(),
        }

    def permission_presets(self):
        from morrow.core.capabilities import PermissionPreset

        native = self.sandbox_probe()
        labels = {
            "manual": "手动审批",
            "auto-safe": "自动批准安全操作",
            "auto-sandboxed": "原生沙箱自动执行",
            "full-access-manual": "完整访问，手动审批",
        }
        return [
            {
                "preset": p.value,
                "label": labels[p.value],
                "available": p.value != "auto-sandboxed" or native,
                "reason": "原生沙箱不可用" if p.value == "auto-sandboxed" and not native else None,
            }
            for p in PermissionPreset
        ]

    def validate(self, settings):
        from morrow.core.agent_runs import exact_model_capabilities

        if settings.permission == "auto-sandboxed" and not self.sandbox_probe():
            raise ApplicationError(
                ApplicationErrorCode.UNAVAILABLE, "原生沙箱不可用，请选择其他权限模式"
            )
        if settings.model is not None:
            config = self.application.global_store.load().value
            provider = config.providers.get(settings.model.provider_id) if config else None
            if provider is None or settings.model.model_id not in provider.models:
                raise ApplicationError(ApplicationErrorCode.INVALID, "模型已不可用，请重新选择")
        if settings.generation and settings.generation.reasoning_effort is not None:
            if settings.model is None:
                raise ApplicationError(ApplicationErrorCode.INVALID, "请先选择精确模型")
            exact = exact_model_capabilities(
                provider.adapter,
                self.application.registry.capabilities(provider.adapter),
                settings.model,
                provider.models[settings.model.model_id].capabilities,
            )
            if settings.generation.reasoning_effort not in exact.reasoning_efforts:
                raise ApplicationError(ApplicationErrorCode.INVALID, "当前模型不支持所选思考参数")

    def put(self, session_id, scope, settings, expected_revision):
        self.documents(session_id)  # Explicit workspace/Session ownership check.
        if scope == "session":
            # Empty fields in a replacement Session document inherit lower scopes.
            docs = self.documents(session_id)
            fallback = ChatSettings(
                **{
                    key: next(
                        (
                            getattr(v, key)
                            for v, _ in (docs["workspace"], docs["global"])
                            if getattr(v, key) is not None
                        ),
                        None,
                    )
                    for key in ("model", "generation", "permission")
                }
            )
            effective = ChatSettings(
                **{
                    key: getattr(settings, key)
                    if getattr(settings, key) is not None
                    else getattr(fallback, key)
                    for key in ("model", "generation", "permission")
                }
            )
        else:
            effective = settings
        self.validate(effective)
        if scope == "session":
            self.journal.transact(
                lambda _: self.journal.chat_settings.put(
                    self.workspace_id, session_id, settings, expected_revision
                )
            )
        elif scope == "workspace":
            current = self.extensions.load_workspace(self.workspace_id).value
            try:
                self.extensions.write_workspace(
                    self.workspace_id,
                    current.model_copy(update={"chat_settings": settings}),
                    expected_revision=expected_revision,
                )
            except ExtensionYamlConflict:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "工作区设置已变化，请刷新"
                ) from None
        elif scope == "global":
            result = self.application.global_store.update(
                lambda value: value.model_copy(
                    update={
                        "active_model": settings.model,
                        "chat_settings": settings.model_copy(update={"model": None}),
                    }
                ),
                expected_revision=expected_revision,
            )
            if result.status is not StateWriteStatus.OK:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "全局设置未保存，请刷新后重试"
                )
        else:
            raise ApplicationError(ApplicationErrorCode.INVALID, "设置范围无效")
        return self.view(session_id)
