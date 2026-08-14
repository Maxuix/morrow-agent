"""UI-independent command use cases."""

from __future__ import annotations

from dataclasses import dataclass

from morrow.core.models import ConfigPatch, ConfigPatchOperation, Preferences
from morrow.core.preferences import merge_preferences


@dataclass
class CommandResult:
    lines: list[str]
    action: str | None = None
    value: object | None = None


class CommandService:
    def __init__(
        self,
        *,
        session,
        identity,
        project_store,
        handoff_service,
        config_service=None,
        provider_service=None,
        workspace_service=None,
    ) -> None:
        self.session = session
        self.identity = identity
        self.project_store = project_store
        self.handoff_service = handoff_service
        self.config_service = config_service
        self.provider_service = provider_service
        self.workspace_service = workspace_service

    def reset_session(self, session_id: str) -> None:
        self.session.reset(session_id)

    def load_handoff(self):
        loaded = self.project_store.load_handoff(self.identity.workspace_id)
        if loaded.value:
            self.session.loaded_handoff = loaded.value.handoff
            self.session.handoff_source_revision = loaded.revision
        return loaded

    def handoff_revision(self) -> int:
        return self.project_store.load_handoff(self.identity.workspace_id).revision

    def clear_handoff(self):
        result = self.project_store.clear_handoff(self.identity.workspace_id)
        if result.status.value == "ok":
            self.session.loaded_handoff = None
            self.session.handoff_source_revision = None
        return result

    def reset_profile(self):
        result = self.project_store.clear_profile(self.identity.workspace_id)
        if result.status.value == "ok":
            self.session.profile = None
        return result

    def reset_preferences(self, scope: str):
        if scope == "session":
            self.session.preferences = Preferences()
            return True
        if scope == "workspace":
            result = self.project_store.clear_preferences(self.identity.workspace_id)
            if result.status.value == "ok":
                self.session.workspace_preferences = Preferences()
            return result
        if scope == "global" and self.config_service:
            current = self.config_service.global_store.load()
            result = self.config_service.global_store.update(
                lambda value: value.model_copy(update={"preferences": Preferences()}),
                expected_revision=current.revision,
            )
            if result.status.value == "ok":
                self.session.global_preferences = result.value.preferences
            return result
        raise ValueError(f"未知 Preferences 作用域：{scope}")

    def execute(self, raw: str) -> CommandResult:
        parts = raw.strip().split()
        if not parts:
            return CommandResult([])
        command = parts[0]
        if command == "/exit":
            return CommandResult(["正在退出。"], action="exit")
        if command == "/new":
            if self.session.dirty:
                return CommandResult(
                    ["当前会话有未交接内容，需要先保存或明确丢弃。"], action="switch_new"
                )
            return CommandResult(["已准备新的独立会话。"], action="new")
        if command == "/continue":
            if self.session.dirty:
                return CommandResult(
                    ["当前会话有未交接内容，需要先保存或明确丢弃。"], action="switch_continue"
                )
            return CommandResult(["已请求继续最近一次交接。"], action="continue")
        if command == "/status":
            handoff = self.project_store.load_handoff(self.identity.workspace_id)
            revision = handoff.revision if handoff.value else None
            return CommandResult(
                [
                    f"工作空间：{self.identity.display_name}",
                    f"交接：{'已加载' if self.session.loaded_handoff else '未加载'}",
                    f"可用 revision：{revision if revision is not None else '无'}",
                    f"当前会话：{'有未交接内容' if self.session.dirty else '干净'}",
                ]
            )
        if command == "/workspace" and len(parts) > 1 and parts[1] == "reset":
            return CommandResult(["Profile 重置需要预览和确认。"], action="reset_profile")
        if command == "/workspace" and len(parts) > 3 and parts[1] == "edit":
            if not self.config_service:
                return CommandResult(["配置服务尚未就绪。"])
            try:
                self.config_service.apply(
                    ConfigPatch(
                        scope="workspace",
                        target="profile",
                        operations=[
                            ConfigPatchOperation(op="set", path=parts[2], value=" ".join(parts[3:]))
                        ],
                    )
                )
                return CommandResult(["Profile 已更新。"])
            except (ValueError, RuntimeError) as exc:
                return CommandResult([f"Profile 更新失败：{exc}"])
        if command == "/workspace":
            profile = self.project_store.load_profile(self.identity.workspace_id)
            lines = [
                f"工作空间：{self.identity.display_name}",
                f"路径：{self.identity.path}",
                f"ID：{self.identity.workspace_id}",
            ]
            if profile.value:
                lines.append(f"简介：{profile.value.profile.summary or '未填写'}")
            return CommandResult(lines)
        if command == "/handoff":
            if len(parts) > 1 and parts[1] == "update":
                return CommandResult(["将生成并替换完整 Handoff。"], action="update_handoff")
            if len(parts) > 1 and parts[1] == "clear":
                return CommandResult(["清除 Handoff 需要预览和确认。"], action="clear_handoff")
            if len(parts) > 3 and parts[1] == "edit":
                if not self.config_service:
                    return CommandResult(["配置服务尚未就绪。"])
                try:
                    self.config_service.apply(
                        ConfigPatch(
                            scope="workspace",
                            target="handoff",
                            operations=[
                                ConfigPatchOperation(
                                    op="set", path=parts[2], value=" ".join(parts[3:])
                                )
                            ],
                        )
                    )
                    return CommandResult(["Handoff 字段已更新。"])
                except (ValueError, RuntimeError) as exc:
                    return CommandResult([f"Handoff 更新失败：{exc}"])
            handoff = self.project_store.load_handoff(self.identity.workspace_id)
            if not handoff.value:
                return CommandResult(["当前没有已保存的交接。"])
            value = handoff.value.handoff
            return CommandResult(
                [f"当前目标：{value.current_goal}", f"revision：{handoff.revision}"]
            )
        if command == "/config":
            if not self.config_service:
                return CommandResult(["配置服务尚未就绪。"])
            if len(parts) > 2 and parts[1] == "reset":
                return CommandResult(
                    [f"将清除 {parts[2]} 层 Preferences 覆盖。"],
                    action="reset_config",
                    value=parts[2],
                )
            if len(parts) > 4 and parts[1] == "edit":
                try:
                    self.config_service.apply(
                        ConfigPatch(
                            scope=parts[2],
                            target="preferences",
                            operations=[
                                ConfigPatchOperation(
                                    op="set", path=parts[3], value=" ".join(parts[4:])
                                )
                            ],
                        )
                    )
                    return CommandResult(["配置已保存。"])
                except (ValueError, RuntimeError) as exc:
                    return CommandResult([f"配置保存失败：{exc}"])
            effective = merge_preferences(
                self.session.global_preferences,
                self.session.workspace_preferences,
                self.session.preferences,
            )
            return CommandResult(
                [
                    f"language：{effective.language or '默认'}",
                    f"response_detail：{effective.response_detail or '默认'}",
                    f"instructions：{len(effective.instructions)} 条",
                ]
            )
        return CommandResult([f"未知命令：{command}"])
