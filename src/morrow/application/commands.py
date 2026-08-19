"""UI-independent command use cases."""

from __future__ import annotations

from dataclasses import dataclass

from morrow.application.configuration import ConfigurationCommand, render_configuration_preview
from morrow.application.tasks import TaskCommandError, TaskCommandResult
from morrow.core.capabilities import AccessScope, ApprovalMode, ProcessIsolation
from morrow.core.domain import SessionHealth, TaskRunStatus
from morrow.core.permissions import UNCONFINED_HOST_WARNING
from morrow.core.preferences import merge_preferences
from morrow.core.recovery import RecoveryReportStatus, RecoveryResolution


@dataclass
class CommandResult:
    lines: list[str]
    action: str | None = None
    value: object | None = None


@dataclass(frozen=True)
class RecoveryCommandRequest:
    report_id: str
    resolution: RecoveryResolution
    item_id: str | None = None


class CommandService:
    def __init__(
        self,
        *,
        session,
        identity,
        project_store,
        config_service=None,
        task_service=None,
        api=None,
        id_source=None,
    ) -> None:
        self.session = session
        self.identity = identity
        self.project_store = project_store
        self.config_service = config_service
        self.task_service = task_service
        self.api = api
        self.id_source = id_source

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

    def arm_full_access_grant(self) -> None:
        profile = self.session.permission_profile
        if (
            profile.access_scope is not AccessScope.FULL_ACCESS
            or profile.approval_mode is not ApprovalMode.MANUAL
            or profile.process_isolation is not ProcessIsolation.HOST
        ):
            raise RuntimeError("只有 full-access-manual 预设支持本地 Host 权限授予")
        if self.session.read_only:
            raise RuntimeError("当前工作空间不可安全写入，无法授予权限")
        self.session.pending_full_access_grant = True

    def _grant_command(self) -> CommandResult:
        profile = self.session.permission_profile
        if (
            profile.access_scope is not AccessScope.FULL_ACCESS
            or profile.approval_mode is not ApprovalMode.MANUAL
            or profile.process_isolation is not ProcessIsolation.HOST
        ):
            return CommandResult(["当前回合不是 full-access-manual，未授予任何额外权限。"])
        if self.session.read_only:
            return CommandResult(["当前工作空间不可安全写入，无法授予权限。"])
        if self.session.pending_full_access_grant:
            return CommandResult(["下一次前台 AgentRun 已有待确认的 Host 权限授予。"])
        return CommandResult(
            [
                UNCONFINED_HOST_WARNING,
                "确认后仅为下一次前台 AgentRun 授予 unconfined_host_process；每条 Host 命令仍需单独手动审批。",
            ],
            action="arm_full_access_grant",
        )

    def _recovery_command(self, parts: list[str]) -> CommandResult:
        if self.api is None:
            return CommandResult(["Recovery 服务尚未就绪。"])
        reports = self.api.list_recovery(self.session.session_id)
        report = next(
            (item for item in reversed(reports) if item.status is RecoveryReportStatus.OPEN), None
        )
        if report is None:
            return CommandResult(["当前没有待处理的 Recovery 报告。"])
        if len(parts) == 1:
            lines = [f"Recovery 报告：{report.report_id}", f"状态：{report.status.value}"]
            if not report.items:
                lines.append("没有未闭合的工具项；可执行 /recovery resume。")
            for item in report.items:
                allowed = ", ".join(value.value for value in item.allowed_resolutions)
                resolution = item.resolution.value if item.resolution is not None else "open"
                lines.append(
                    f"{item.item_id} {item.tool_name}：{item.classification.value}；"
                    f"resolution={resolution}；可选={allowed}"
                )
            return CommandResult(lines)

        operation = parts[1].casefold()
        if operation == "show":
            return self._recovery_command([parts[0]])
        aliases = {
            "ack": RecoveryResolution.ACKNOWLEDGE,
            "acknowledge": RecoveryResolution.ACKNOWLEDGE,
            "abort": RecoveryResolution.ABORT,
            "quarantine": RecoveryResolution.QUARANTINE,
            "resume": RecoveryResolution.RESUME,
        }
        if operation == "retry":
            return CommandResult(
                ["Recovery 暂不支持无损 linked retry；请使用 abort 或 quarantine。"]
            )
        resolution = aliases.get(operation)
        if resolution is None:
            return CommandResult(["用法：/recovery [show|ack|abort|quarantine|resume] [item_id]"])
        item_id = parts[2] if len(parts) > 2 else None
        if resolution is RecoveryResolution.RESUME and item_id is not None:
            return CommandResult(["resume 是报告级操作，不接受 item_id。"])
        if resolution is RecoveryResolution.ACKNOWLEDGE and item_id is None:
            return CommandResult(["ack 需要 item_id；请先执行 /recovery 查看待处理项。"])
        if resolution is RecoveryResolution.QUARANTINE and item_id is not None:
            return CommandResult(["quarantine 是报告级操作，不接受 item_id。"])
        return CommandResult(
            [f"将对 Recovery 报告执行 {resolution.value}。"],
            action="resolve_recovery",
            value=RecoveryCommandRequest(report.report_id, resolution, item_id),
        )

    def resolve_recovery(self, request: RecoveryCommandRequest):
        if self.api is None:
            raise RuntimeError("Recovery 服务尚未就绪")
        report = self.api.get_recovery(request.report_id)
        if report is None:
            raise RuntimeError("Recovery 报告不存在")
        committer = self.session.committer
        writer = getattr(committer, "writer", None)
        return self.api.resolve_recovery(
            report,
            command_id=self.id_source.new_id("cmd") if self.id_source is not None else None,
            resolution=request.resolution,
            item_id=request.item_id,
            log=self.session.log,
            writer=writer,
            close_all=request.resolution is RecoveryResolution.ABORT and request.item_id is None,
        ).value

    def execute(self, raw: str) -> CommandResult:
        parts = raw.strip().split()
        if not parts:
            return CommandResult([])
        command = parts[0]
        if command == "/exit":
            if self.session.persisted:
                return CommandResult(["正在退出。已保存的对话会保留。"], action="exit")
            return CommandResult(["正在退出。"], action="exit")
        if command == "/new":
            if self.session.persisted:
                if self.session.health is SessionHealth.NEEDS_RECOVERY:
                    return CommandResult(
                        ["当前会话有未结束的工作，需要先恢复或如实关闭后才能新建会话。"]
                    )
                return CommandResult(["已准备新的独立会话。"], action="new")
            if self.session.dirty:
                return CommandResult(
                    ["当前会话有未保存的进程内对话，需要明确确认丢弃。"], action="discard_new"
                )
            return CommandResult(["已准备新的独立会话。"], action="new")
        if command == "/status":
            if self.session.persisted:
                if self.session.health is SessionHealth.NEEDS_RECOVERY:
                    current = "需要恢复"
                elif self.session.health is SessionHealth.QUARANTINED:
                    current = "已隔离"
                else:
                    current = "已保存"
            else:
                current = "有未保存的进程内对话" if self.session.dirty else "干净"
            return CommandResult(
                [
                    f"工作空间：{self.identity.display_name}",
                    f"当前会话：{current}",
                ]
            )
        if command == "/grant":
            return self._grant_command()
        if command == "/recovery":
            return self._recovery_command(parts)
        if command == "/task":
            return self._task_command(parts)
        if command == "/accept":
            return self._task_command(["/task", "accept"])
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

    def _task_command(self, parts: list[str]) -> CommandResult:
        if self.task_service is None:
            return CommandResult(["Task 服务尚未就绪。"])
        operation = parts[1] if len(parts) > 1 else "show"
        current = self._current_task()
        try:
            command_id = self.id_source.new_id("cmd") if self.id_source is not None else None
            if operation == "show":
                if current is None:
                    return CommandResult(["当前没有前台 TaskRun。"])
                return CommandResult(
                    [
                        f"TaskRun：{current.task_run_id}",
                        f"状态：{current.status.value}",
                        f"版本：{current.row_version}",
                        f"尝试：{current.attempt}",
                    ],
                    value=current,
                )
            if operation == "new":
                result = (
                    self.api.task_new(self.session.session_id, command_id=command_id)
                    if self.api is not None
                    else self.task_service.new_task(self.session.session_id, command_id=command_id)
                )
                task = result.value if self.api is not None else result.task
                if self.session.committer is not None:
                    self.session.committer.current_task_run_id = task.task_run_id
                ui_result = TaskCommandResult("accepted", task) if self.api is not None else result
                return CommandResult([f"已创建 TaskRun：{task.task_run_id}"], value=ui_result)
            if current is None:
                return CommandResult(["当前没有可操作的前台 TaskRun。"])
            if operation in {"accept", "cancel", "abandon", "resume", "retry"}:
                action_name = "resume" if operation == "retry" else operation
                if self.api is not None:
                    action = getattr(self.api, f"task_{action_name}")
                    result = action(
                        current.task_run_id,
                        command_id=command_id,
                        expected_row_version=current.row_version,
                    )
                    task = result.value
                else:
                    action = getattr(self.task_service, action_name)
                    result = action(
                        current.task_run_id,
                        command_id=command_id,
                        expected_row_version=current.row_version,
                    )
                    task = result.task
                if self.session.committer is not None:
                    self.session.committer.current_task_run_id = (
                        task.task_run_id if task.status is not TaskRunStatus.CANCELLED else None
                    )
                    if task.status in {
                        TaskRunStatus.ACCEPTED,
                        TaskRunStatus.ABANDONED,
                    }:
                        self.session.committer.current_task_run_id = None
                return CommandResult(
                    [f"TaskRun {task.task_run_id}：{task.status.value}"],
                    value=(TaskCommandResult("accepted", task) if self.api is not None else result),
                )
            return CommandResult([f"未知 Task 操作：{operation}"])
        except (TaskCommandError, ValueError, RuntimeError) as exc:
            return CommandResult([f"Task 操作失败：{exc}"])

    def _current_task(self):
        if self.task_service is None:
            return None
        task_id = getattr(self.session.committer, "current_task_run_id", None)
        if task_id is None:
            return None
        return self.task_service.get(task_id)
