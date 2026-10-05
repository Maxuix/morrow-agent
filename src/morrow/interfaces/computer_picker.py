"""Explicit terminal window selection on the ordinary session's owner loop."""

from __future__ import annotations

from datetime import datetime

from morrow.application.computer_permissions import computer_runtime_lines, computer_runtime_summary
from morrow.application.computer_selection import LocalWindowSelectionRequest
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.capabilities import AccessScope, ApprovalMode, ProcessIsolation
from morrow.core.computer_use import TRUSTED_COMPUTER_USE_AUTHORITY
from morrow.core.domain import session_can_start_work


class TerminalComputerPicker:
    def __init__(self, application, products, selection_service):
        self.application = application
        self.products = products
        self.selection_service = selection_service
        self.pending = None
        self.selected_session_id = None
        self.previous_options = products.orchestrator.prepare_options
        products.orchestrator.prepare_options = self.prepare_options

    def _context(self):
        session = self.products.session
        profile = session.permission_profile
        if (
            profile.access_scope is not AccessScope.FULL_ACCESS
            or profile.approval_mode is not ApprovalMode.MANUAL
            or profile.process_isolation is not ProcessIsolation.HOST
            or session.read_only
            or not session_can_start_work(session.lifecycle, session.health)
        ):
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "窗口选择需要健康可写的完整访问会话"
            )
        if session.pending_full_access_grant:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "请先取消待授予的 Host 权限再选择桌面"
            )
        config = self.application.global_store.load().value
        if config is None:
            raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "全局配置需要修复")
        return session, config.active_model

    def clear(self):
        if self.selected_session_id is not None:
            self.selection_service.clear(self.selected_session_id)
        self.pending = None
        self.selected_session_id = None

    def prepare_options(self, client_message_id):
        options = self.previous_options(client_message_id) if self.previous_options else {}
        if self.pending is None:
            return options
        session, model = self._context()
        if session.session_id != self.selected_session_id:
            raise ApplicationError(
                ApplicationErrorCode.STALE, "会话已切换，请重新选择或清除窗口选择"
            )
        selection_id = self.pending["selection_id"]
        self.selection_service.claim(
            session.session_id,
            selection_id,
            client_message_id,
            permission="full-access-manual",
            model=model,
        )
        selection = self.selection_service.consume(
            session.session_id, selection_id, key=client_message_id
        )
        factory = self.products.orchestrator.preparation.computer_factory
        request = factory.select(selection, session, authority=TRUSTED_COMPUTER_USE_AUTHORITY)
        self.clear()
        return options | {"computer_request": request}

    async def handle(self, arguments, terminal, prompt_session):
        if arguments == ("clear",):
            self.clear()
            terminal.console.print("已清除下次运行的窗口选择。")
            return
        if arguments == ("status",):
            summary = computer_runtime_summary(
                self.products.computer_use,
                self.products.api.journal,
                self.products.api.workspace_id,
                self.products.persistence.current_agent_run_id,
            )
            for line in computer_runtime_lines(summary):
                terminal.console.print(line, markup=False)
            terminal.console.print(
                self._summary() if self.pending else "下次运行尚未选择窗口。", markup=False
            )
            if (
                self.pending
                and datetime.fromisoformat(self.pending["expires_at"])
                <= self.selection_service.clock.now()
            ):
                terminal.console.print("窗口选择已过期；请重新 /computer 或使用 /computer clear。")
            return
        if arguments not in {(), ("select",)}:
            terminal.console.print("用法：/computer [select|status|clear]")
            return
        if self.products.orchestrator.run_active:
            terminal.console.print("请等待当前运行结束再选择下次运行的窗口。")
            return
        self.clear()
        try:
            session, model = self._context()
            session_id = session.session_id
            prepared = await self.selection_service.prepare_catalog(permission="full-access-manual")
            current, model = self._context()
            if current.session_id != session_id:
                raise ApplicationError(ApplicationErrorCode.STALE, "会话已切换，请重新读取窗口")
            catalog = self.selection_service.accept_catalog(
                session_id, prepared, permission="full-access-manual"
            )
            candidates = catalog["candidates"]
            if not candidates:
                terminal.console.print("没有可选择的窗口。")
                return
            for index, item in enumerate(candidates, 1):
                terminal.console.print(
                    f"{index}. {item['display_label'] or item['app']['bundle_id']}", markup=False
                )
            answer = await terminal.prompt(prompt_session, "窗口序号（逗号分隔；空白取消） > ")
            if not answer.strip():
                return
            indexes = tuple(int(value.strip()) for value in answer.split(","))
            if len(set(indexes)) != len(indexes) or any(
                index < 1 or index > len(candidates) for index in indexes
            ):
                raise ValueError("invalid window indexes")
            delivery = (
                await terminal.prompt(prompt_session, "投递方式 [foreground/background] > ")
            ).strip()
            if delivery not in {"foreground", "background"}:
                raise ValueError("explicit delivery required")
            action = await self._choice(terminal, prompt_session, "允许操作选中窗口？ [y/N] ")
            images = await self._choice(terminal, prompt_session, "分享受控窗口图像？ [y/N] ")
            # Prompts can take time: re-check permission, model and catalog expiry at selection.
            current, model = self._context()
            if current.session_id != session_id:
                raise ApplicationError(ApplicationErrorCode.STALE, "会话已切换，请重新读取窗口")
            self.pending = self.selection_service.select(
                current.session_id,
                LocalWindowSelectionRequest(
                    candidate_ids=tuple(candidates[index - 1]["candidate_id"] for index in indexes),
                    allow_action=action,
                    share_images=images,
                    delivery=delivery,
                ),
                permission="full-access-manual",
                model=model,
            )
            self.selected_session_id = current.session_id
            terminal.console.print(self._summary(), markup=False)
            terminal.console.print(
                "仅用于下次普通输入；约30秒有效。尚未创建授权；/computer clear 可清除。"
            )
        except ApplicationError as exc:
            terminal.console.print(f"窗口选择失败（{exc.code.value}）：{exc.message}", markup=False)
        except ValueError:
            terminal.console.print("选择无效：请使用窗口序号及明确的投递方式。")
        except (EOFError, KeyboardInterrupt):
            terminal.console.print("已取消窗口选择。")

    @staticmethod
    async def _choice(terminal, prompt_session, message):
        answer = (await terminal.prompt(prompt_session, message)).strip().casefold()
        if answer not in {"", "n", "no", "否", "y", "yes", "是"}:
            raise ValueError("invalid capability choice")
        return answer in {"y", "yes", "是"}

    def _summary(self):
        labels = "、".join(
            item["display_label"] or item["app"]["bundle_id"] for item in self.pending["windows"]
        )
        operations = "观察与操作" if "action" in self.pending["operations"] else "仅观察"
        images = "不分享图像" if self.pending["image_share"] == "none" else "分享受控窗口图像"
        delivery = "前台" if self.pending["delivery"] == "foreground" else "后台"
        return f"下次运行窗口：{labels} · {operations} · {delivery} · {images}"
