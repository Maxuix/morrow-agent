"""UI-independent command use cases."""

from __future__ import annotations

from dataclasses import dataclass

from morrow.application.configuration import ConfigurationCommand, render_configuration_preview
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
        config_service=None,
    ) -> None:
        self.session = session
        self.identity = identity
        self.project_store = project_store
        self.config_service = config_service

    def _ensure_workspace_writable(self, *, preferences: bool = False) -> None:
        if self.session.read_only:
            raise RuntimeError("当前工作空间状态不可安全加载，已禁止持久化操作")
        if preferences and self.session.workspace_preferences_read_only:
            raise RuntimeError("工作空间 Preferences 不可安全加载，已禁止覆盖")

    def reset_session(self, session_id: str) -> None:
        self.session.reset(session_id)

    def reset_profile(self):
        self._ensure_workspace_writable()
        if self.config_service is None:
            raise RuntimeError("配置服务尚未就绪")
        return self.config_service.apply_command(
            ConfigurationCommand(scope="workspace", target="profile", operation="reset")
        )

    def reset_preferences(self, scope: str):
        if self.config_service is None:
            raise RuntimeError("配置服务尚未就绪")
        if scope == "workspace":
            self._ensure_workspace_writable(preferences=True)
        elif scope not in {"session", "global"}:
            raise ValueError(f"未知 Preferences 作用域：{scope}")
        return self.config_service.apply_command(
            ConfigurationCommand(scope=scope, target="preferences", operation="reset")
        )

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
                    ["当前会话有未保存的进程内对话，需要明确确认丢弃。"], action="discard_new"
                )
            return CommandResult(["已准备新的独立会话。"], action="new")
        if command == "/status":
            return CommandResult(
                [
                    f"工作空间：{self.identity.display_name}",
                    f"当前会话：{'有未保存的进程内对话' if self.session.dirty else '干净'}",
                ]
            )
        if command == "/workspace" and len(parts) > 1 and parts[1] == "reset":
            if self.session.read_only:
                return CommandResult(["当前工作空间状态不可安全加载，无法重置 Profile。"])
            return CommandResult(["Profile 重置需要预览和确认。"], action="reset_profile")
        if command == "/workspace" and len(parts) > 3 and parts[1] == "edit":
            if self.session.read_only:
                return CommandResult(["当前工作空间状态不可安全加载，无法编辑 Profile。"])
            if not self.config_service:
                return CommandResult(["配置服务尚未就绪。"])
            try:
                command = ConfigurationCommand(
                    scope="workspace",
                    target="profile",
                    operation="set",
                    path=parts[2],
                    value=" ".join(parts[3:]),
                )
                return CommandResult(
                    render_configuration_preview(command), action="config_preview", value=command
                )
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
        if command == "/config":
            if not self.config_service:
                return CommandResult(["配置服务尚未就绪。"])
            if len(parts) > 2 and parts[1] == "reset":
                if parts[2] == "workspace" and (
                    self.session.read_only or self.session.workspace_preferences_read_only
                ):
                    return CommandResult(["工作空间 Preferences 不可安全加载，无法重置。"])
                return CommandResult(
                    [f"将清除 {parts[2]} 层 Preferences 覆盖。"],
                    action="reset_config",
                    value=parts[2],
                )
            if len(parts) > 4 and parts[1] == "edit":
                if parts[2] == "workspace" and (
                    self.session.read_only or self.session.workspace_preferences_read_only
                ):
                    return CommandResult(["工作空间 Preferences 不可安全加载，无法编辑。"])
                try:
                    command = ConfigurationCommand(
                        scope=parts[2],
                        target="preferences",
                        operation="set",
                        path=parts[3],
                        value=" ".join(parts[4:]),
                    )
                    return CommandResult(
                        render_configuration_preview(command),
                        action="config_preview",
                        value=command,
                    )
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
