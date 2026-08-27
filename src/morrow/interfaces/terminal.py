"""Thin terminal interface using prompt-toolkit input and Rich output."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from morrow.application.commands import (
    RecoveryCommandRequest,
)
from morrow.application.orchestrator import DispatchResult
from morrow.core.capabilities import CommandToolFact
from morrow.core.learning import LearningReviewStatus
from morrow.core.learning_payloads import ProjectKnowledgeCandidatePayload
from morrow.core.models import AgentEvent, ToolApprovalDecision, ToolApprovalRequest
from morrow.core.permissions import UNCONFINED_HOST_APPROVAL_LANGUAGE

_MODEL_WAIT_MESSAGE = "正在连接模型并等待首个响应…（Ctrl+C 取消）"
_MODEL_CONTINUE_MESSAGE = "正在等待模型继续响应…（Ctrl+C 取消）"
_MODEL_RETRY_MESSAGE = "模型暂时不可用，正在重试…（Ctrl+C 取消）"
_STOP_HINTS = {
    "provider_auth": "请检查 API Key 或重新配置 Provider。",
    "provider_network": "请检查网络后重试。",
    "provider_rate_limit": "请稍后重试。",
    "provider_timeout": "可按 Ctrl+C 取消后重试。",
    "run_timeout": "任务超过总运行时间。",
}


class Terminal:
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()
        self._text_open = False
        self._tool_activity = False

    def show_event(self, event) -> None:
        if event.type == "turn.started":
            self._text_open = False
            self._tool_activity = False
            self.console.print(_MODEL_WAIT_MESSAGE)
        elif event.type == "status.changed" and event.payload.get("status") == "retrying":
            if self._text_open:
                self.console.print()
            self.console.print(_MODEL_RETRY_MESSAGE)
            self._text_open = False
        elif event.type == "text.delta":
            self.console.print(event.payload.get("text", ""), end="")
            self._text_open = True
        elif event.type == "tool.status" and event.payload.get("status") == "running":
            if self._text_open:
                self.console.print()
            ordinal = event.payload.get("ordinal", "?")
            total = event.payload.get("total", "?")
            name = str(event.payload.get("name", "tool"))[:64]
            self.console.print(f"↳ 工具步骤 {ordinal}/{total}：{name}")
            self._text_open = False
            self._tool_activity = True
        elif event.type == "tool.status":
            ordinal = event.payload.get("ordinal")
            total = event.payload.get("total")
            if ordinal == total and event.payload.get("status") in {
                "succeeded",
                "failed",
                "cancelled",
                "skipped",
            }:
                if self._text_open:
                    self.console.print()
                self.console.print(_MODEL_CONTINUE_MESSAGE)
                self._text_open = False
        elif event.type == "error":
            if self._text_open:
                self.console.print()
            message = str(event.payload.get("message") or "模型调用失败")
            hint = _STOP_HINTS.get(str(event.payload.get("stop_code") or ""))
            rendered = f"错误：{message}" if not hint else f"错误：{message} {hint}"
            self.console.print(f"\n[red]{rendered}[/red]")
            self._text_open = False
        elif event.type == "turn.completed":
            if self._text_open:
                self.console.print()
            self._text_open = False
            self._tool_activity = False

    def show_run_summary(self, session) -> None:
        """Render one bounded fact summary without exposing Diff or tool payloads."""

        metrics = getattr(session, "latest_metrics", None)
        facts = getattr(session, "latest_tool_facts", ())
        if metrics is None:
            return

        validation = {
            "not_run": "未运行",
            "passed": "通过",
            "failed": "失败",
            "timeout": "超时",
            "cancelled": "取消",
        }.get(metrics.validation_outcome, "未知")
        markers: list[str] = []
        command_facts = tuple(fact for fact in facts if isinstance(fact, CommandToolFact))
        successful_commands = sum(
            fact.status == "exited" and fact.exit_code == 0 for fact in command_facts
        )
        failed_commands = len(command_facts) - successful_commands
        if any(fact.output_truncated for fact in command_facts):
            markers.append("输出截断")
        if any(fact.redaction_count for fact in command_facts):
            markers.append("输出脱敏")
        suffix = f"；{'、'.join(markers)}" if markers else ""
        line = (
            f"事实摘要：工具 {metrics.tool_calls} 次，成功工具 {metrics.successful_tool_calls}，"
            f"失败工具 {metrics.failed_tool_calls}，成功命令 {successful_commands}，"
            f"失败命令 {failed_commands}，修改 {metrics.changed_file_count} 个文件，"
            f"验证 {validation}{suffix}"
        )
        self.console.print(line[:200])

    async def prompt(self, session: PromptSession, message: str = "你 > ") -> str:
        return await session.prompt_async(message)


class TerminalApprovalPort:
    """Terminal-only adapter for the generic Core approval boundary."""

    def __init__(self, terminal: Terminal, prompt_session: PromptSession) -> None:
        self.terminal = terminal
        self.prompt_session = prompt_session

    async def request(self, request: ToolApprovalRequest) -> ToolApprovalDecision:
        lines = request.preview or ("未提供额外预览。",)
        self.terminal.console.print("\n".join(lines))
        elevated = any(line.startswith("unconfined_host:") for line in lines)
        if elevated:
            self.terminal.console.print(UNCONFINED_HOST_APPROVAL_LANGUAGE)
        self.terminal.console.print(f"副作用级别：{request.effect.value}")
        if request.approval_id:
            self.terminal.console.print(f"审批编号：{request.approval_id}")
        try:
            prompt = (
                "确认执行这条未受操作系统隔离的 Host 命令？ [y/N] "
                if elevated
                else "确认执行？ [y/N] "
            )
            answer = await self.terminal.prompt(self.prompt_session, prompt)
        except (EOFError, KeyboardInterrupt):
            raise asyncio.CancelledError from None
        return ToolApprovalDecision(approved=answer.strip().casefold() in {"y", "yes", "是"})


async def run_repl(
    orchestrator,
    *,
    session=None,
    terminal: Terminal | None = None,
    prompt_session: PromptSession | None = None,
    resume_current_turn: bool = False,
    review_worker=None,
) -> int:
    if review_worker is not None:
        await review_worker.start()
    try:
        return await _run_repl_loop(
            orchestrator,
            session=session,
            terminal=terminal,
            prompt_session=prompt_session,
            resume_current_turn=resume_current_turn,
            review_worker=review_worker,
        )
    finally:
        if review_worker is not None:
            await review_worker.stop()


async def _run_repl_loop(
    orchestrator,
    *,
    session=None,
    terminal: Terminal | None = None,
    prompt_session: PromptSession | None = None,
    resume_current_turn: bool = False,
    review_worker=None,
) -> int:
    terminal = terminal or Terminal()
    prompt_session = prompt_session or PromptSession()
    terminal.console.print("Morrow 承序 · Workspace terminal agent.")
    with patch_stdout():
        if resume_current_turn:
            try:
                async for item in orchestrator.resume_recovery():
                    terminal.show_event(item)
            except (RuntimeError, ValueError) as exc:
                terminal.console.print(f"Recovery 继续失败：{exc}")
            else:
                if session is not None:
                    terminal.show_run_summary(session)
        while True:
            _show_review_notices(terminal, review_worker)
            try:
                text = await terminal.prompt(prompt_session)
            except EOFError:
                text = "/exit"
            except KeyboardInterrupt:
                terminal.console.print("\n已取消输入。")
                continue
            if not text.strip():
                continue
            dispatch_task = asyncio.create_task(
                _consume_dispatch(orchestrator, text.strip(), terminal)
            )
            try:
                result = await dispatch_task
            except KeyboardInterrupt:
                dispatch_task.cancel()
                await asyncio.gather(dispatch_task, return_exceptions=True)
                terminal.console.print("\n已取消当前操作。")
                continue
            if review_worker is not None:
                try:
                    review_worker.wake()
                except Exception:
                    pass
            if result.action == "exit":
                exit_code = await _exit(session, terminal, prompt_session)
                if exit_code is not None:
                    return exit_code
                continue
            if result.action == "new" and session:
                _reset_session(orchestrator)
                terminal.console.print("已切换到新的独立会话。")
            if result.action == "discard_new" and session:
                confirmation = await _confirm(
                    terminal, prompt_session, "确认丢弃当前进程内对话并开始新会话？"
                )
                if confirmation == "closed":
                    return _closed_input(terminal)
                if confirmation != "yes":
                    continue
                _reset_session(orchestrator)
                terminal.console.print("已切换到新的独立会话。")
            if result.action == "resolve_recovery":
                request = result.value
                if not isinstance(request, RecoveryCommandRequest):
                    terminal.console.print("Recovery 请求无效。")
                    continue
                confirmation = await _confirm(
                    terminal,
                    prompt_session,
                    f"确认执行 Recovery {request.resolution.value}？",
                )
                if confirmation == "closed":
                    return _closed_input(terminal)
                if confirmation != "yes":
                    continue
                try:
                    saved = _command_service(orchestrator).resolve_recovery(request)
                except (ValueError, RuntimeError) as exc:
                    terminal.console.print(f"Recovery 处理失败：{exc}")
                    continue
                terminal.console.print(f"Recovery 已处理：{saved.status.value}。")
                if request.resolution.value == "resume":
                    async for item in orchestrator.resume_recovery():
                        terminal.show_event(item)
                    if session is not None:
                        terminal.show_run_summary(session)
            if result.action == "reset_profile":
                confirmation = await _confirm(terminal, prompt_session, "确认重置 Profile？")
                if confirmation == "closed":
                    return _closed_input(terminal)
                if confirmation == "yes":
                    try:
                        reset = _command_service(orchestrator).reset_profile()
                    except (ValueError, RuntimeError) as exc:
                        terminal.console.print(f"Profile 重置失败：{exc}")
                    else:
                        terminal.console.print(
                            "Profile 已重置。"
                            if reset.status.value in {"applied", "unchanged"}
                            else "Profile 重置失败。"
                        )
            if result.action == "config_preview":
                confirmation = await _confirm(terminal, prompt_session, "确认保存这项配置？")
                if confirmation == "closed":
                    return _closed_input(terminal)
                if confirmation == "yes":
                    try:
                        _command_service(orchestrator).config_service.apply(result.value)
                    except (ValueError, RuntimeError) as exc:
                        terminal.console.print(f"配置保存失败：{exc}")
                    else:
                        terminal.console.print("配置已保存。")
            if result.action == "arm_full_access_grant":
                confirmation = await _confirm(
                    terminal,
                    prompt_session,
                    "确认在下一次前台 AgentRun 授予未受操作系统隔离的 Host 权限？",
                )
                if confirmation == "closed":
                    return _closed_input(terminal)
                if confirmation == "yes":
                    try:
                        _command_service(orchestrator).arm_full_access_grant()
                    except (ValueError, RuntimeError) as exc:
                        terminal.console.print(f"权限授予准备失败：{exc}")
                    else:
                        terminal.console.print("已准备下一次前台 AgentRun 的 Host 权限授予。")
            if result.action == "learning_review_pending":
                terminal.console.print("Learning Review 已排队，继续处理前台输入。")
            if result.action in {"learning_review_run", "learning_review_retry"}:
                await _run_learning_review(
                    orchestrator,
                    terminal,
                    str(result.value),
                    retry=result.action == "learning_review_retry",
                )
            if result.action == "learning_accept_preview":
                if await _handle_learning_accept(
                    orchestrator, terminal, prompt_session, result.value
                ):
                    return _closed_input(terminal)
            if result.action == "learning_edit_preview":
                if await _handle_learning_edit(
                    orchestrator, terminal, prompt_session, result.value
                ):
                    return _closed_input(terminal)
            if result.action == "learning_reject_preview":
                if await _handle_learning_reject(
                    orchestrator, terminal, prompt_session, result.value
                ):
                    return _closed_input(terminal)
            if result.action == "learning_undo_preview":
                confirmation = await _confirm(
                    terminal,
                    prompt_session,
                    "确认撤销这项配置 activation？",
                )
                if confirmation == "closed":
                    return _closed_input(terminal)
                if confirmation != "yes":
                    continue
                try:
                    value = _command_service(orchestrator).undo_learning_activation(result.value)
                except (ValueError, RuntimeError) as exc:
                    terminal.console.print(f"配置撤销失败：{exc}")
                else:
                    _show_learning_result(terminal, value)
            if result.action == "learning_promotion_recovery_preview":
                confirmation = await _confirm(
                    terminal,
                    prompt_session,
                    "确认执行这项配置 promotion 恢复动作？",
                )
                if confirmation == "closed":
                    return _closed_input(terminal)
                if confirmation != "yes":
                    terminal.console.print("已取消，未写入状态。")
                    continue
                try:
                    value = _command_service(orchestrator).recover_learning_promotion(result.value)
                except (ValueError, RuntimeError) as exc:
                    terminal.console.print(f"配置 promotion 恢复失败：{exc}")
                else:
                    _show_learning_result(
                        terminal, value.value if hasattr(value, "value") else value
                    )
            if result.action == "memory_lifecycle_preview":
                if await _handle_memory_lifecycle(
                    orchestrator, terminal, prompt_session, result.value
                ):
                    return _closed_input(terminal)
            if result.action == "preference_preview":
                confirmation = await _confirm(
                    terminal, prompt_session, "确认执行这项 Preference 生命周期操作？"
                )
                if confirmation == "closed":
                    return _closed_input(terminal)
                if confirmation != "yes":
                    terminal.console.print("已取消，未写入状态。")
                    continue
                try:
                    value = _command_service(orchestrator).apply_preferences(result.value)
                except (ValueError, RuntimeError) as exc:
                    terminal.console.print(f"Preference 操作失败：{exc}")
                else:
                    terminal.console.print(
                        f"Preference 已写入：scope={value['scope']}；revision={value['revision']}。"
                    )


async def _run_learning_review(orchestrator, terminal: Terminal, review_id: str, *, retry=False):
    service = _command_service(orchestrator)
    terminal.console.print(f"正在审查 Learning Review {review_id}…")
    try:
        review_result = await (
            service.retry_learning_review(review_id)
            if retry
            else service.run_learning_review(review_id)
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        try:
            cancelled = service.cancel_learning_review(review_id)
        except (ValueError, RuntimeError) as exc:
            terminal.console.print(f"Learning Review 取消失败：{exc}")
        else:
            status = cancelled.review.status.value
            failure = (
                cancelled.review.failure_code.value
                if cancelled.review.failure_code is not None
                else "lease_expired"
            )
            terminal.console.print(f"Learning Review {review_id} 已停止：{status}（{failure}）。")
    except (ValueError, RuntimeError) as exc:
        terminal.console.print(f"Learning Review 处理失败：{exc}")
    else:
        review = review_result.review
        if review.status is not LearningReviewStatus.COMPLETED:
            failure = (
                review.failure_code.value if review.failure_code is not None else "not_completed"
            )
            terminal.console.print(
                f"Learning Review {review_id} 未完成：{review.status.value}（{failure}）。"
                f"可使用 /learn retry {review_id} 重试。"
            )
        else:
            candidate_count = len(review_result.candidate_ids)
            if candidate_count:
                terminal.console.print(
                    f"Learning Review {review.status.value}：新增候选 "
                    f"{candidate_count} 个；可用 /learn inbox 查看。"
                )
            else:
                terminal.console.print(f"Learning Review {review.status.value}：没有生成候选。")


def _show_review_notices(terminal: Terminal, review_worker) -> None:
    """Render only bounded worker outcomes; zero-operation completions remain silent."""

    if review_worker is None:
        return
    drain = getattr(review_worker, "drain_notices", None)
    if drain is None:
        return
    try:
        notices = drain()
    except Exception:
        return
    for notice in notices:
        if notice.kind == "proposals":
            terminal.console.print(
                f"Preference Review 已生成 {notice.proposal_count} 个新 proposal；"
                "可用 `morrow preferences inbox list` 查看。"
            )
        elif notice.kind == "exhausted":
            detail = f"（{notice.error_code}）" if notice.error_code else ""
            terminal.console.print(
                f"Preference Review 重试已耗尽{detail}；job {notice.job_id} 可用 "
                "`morrow preferences inbox retry` 重试。"
            )


async def _consume_dispatch(orchestrator, text: str, terminal: Terminal) -> DispatchResult:
    result = DispatchResult()
    completed = False
    async for item in orchestrator.stream(text):
        if isinstance(item, AgentEvent):
            terminal.show_event(item)
            completed = item.type == "turn.completed"
        else:
            result = item
            for line in result.lines:
                terminal.console.print(line)
    if completed and getattr(orchestrator, "session", None) is not None:
        terminal.show_run_summary(orchestrator.session)
    return result


def _command_service(orchestrator):
    return orchestrator.command_service


def _show_learning_result(terminal: Terminal, value) -> None:
    if hasattr(value, "outcome"):
        if value.outcome == "candidate_only":
            terminal.console.print(
                "候选已接受为候选/反馈；未创建或激活 Skill、Workflow 或 Orchestration 状态。"
            )
        else:
            if getattr(value, "activation_id", None) is not None:
                action = "撤销" if value.outcome == "reversed" else "激活"
                label = (
                    "Active Preference" if value.target == "preferences" else "Active Profile field"
                )
                terminal.console.print(
                    f"{action}{label}：scope={value.scope.value}；"
                    f"path={value.path}；revision={value.revision}；"
                    f"activation={value.activation_id}。"
                )
            else:
                terminal.console.print(
                    f"Learning Candidate 已处理：{value.outcome}；候选 {value.candidate.candidate_id}。"
                )
    elif hasattr(value, "operation") and hasattr(value, "head"):
        terminal.console.print(
            f"Project Knowledge 已处理：{value.operation}；状态 {value.head.status.value}。"
        )
    elif hasattr(value, "candidate"):
        terminal.console.print(
            f"Learning Candidate 已处理：{value.candidate.status.value}；候选 {value.candidate.candidate_id}。"
        )
    else:
        terminal.console.print("Learning 操作已完成。")


async def _handle_learning_accept(orchestrator, terminal, prompt_session, request) -> bool:
    confirmation = await _confirm(terminal, prompt_session, "确认接受这项 Learning Candidate？")
    if confirmation == "closed":
        return True
    if confirmation != "yes":
        terminal.console.print("已取消，未写入状态。")
        return False
    try:
        value = _command_service(orchestrator).accept_learning_candidate(request)
    except (ValueError, RuntimeError) as exc:
        terminal.console.print(f"Learning 接受失败：{exc}")
    else:
        _show_learning_result(terminal, value)
    return False


async def _handle_learning_edit(orchestrator, terminal, prompt_session, request) -> bool:
    service = _command_service(orchestrator)
    view = service.api.get_learning_candidate_view(request.candidate_id)
    if view is None:
        terminal.console.print("Learning Candidate 不存在。")
        return False
    payload = view.candidate.proposed_payload
    final_payload = payload
    if isinstance(payload, ProjectKnowledgeCandidatePayload):
        try:
            statement = await terminal.prompt(
                prompt_session,
                f"Project Knowledge statement（留空保留当前：{payload.statement}）：",
            )
        except EOFError:
            return True
        except KeyboardInterrupt:
            terminal.console.print("已取消编辑。")
            return False
        if statement.strip():
            try:
                final_payload = payload.model_copy(update={"statement": statement})
            except ValueError as exc:
                terminal.console.print(f"编辑值无效：{exc}")
                return False
    preview = service.api.preview_learning_candidate_decision(
        request.candidate_id,
        edit=final_payload,
        scope=request.scope,
        conflict_resolution=request.conflict_resolution,
    )
    for line in service._preview_lines(preview):
        terminal.console.print(line)
    confirmation = await _confirm(terminal, prompt_session, "确认保存编辑后的 Candidate？")
    if confirmation == "closed":
        return True
    if confirmation != "yes":
        terminal.console.print("已取消，未写入状态。")
        return False
    try:
        value = service.edit_learning_candidate(
            replace(
                request,
                candidate_id=preview.candidate.candidate_id,
                expected_row_version=preview.expected_row_version,
                final_payload=final_payload,
            )
        )
    except (ValueError, RuntimeError) as exc:
        terminal.console.print(f"Learning 编辑失败：{exc}")
    else:
        _show_learning_result(terminal, value)
    return False


async def _handle_learning_reject(orchestrator, terminal, prompt_session, request) -> bool:
    confirmation = await _confirm(terminal, prompt_session, "确认拒绝这项 Learning Candidate？")
    if confirmation == "closed":
        return True
    if confirmation != "yes":
        terminal.console.print("已取消，未写入状态。")
        return False
    try:
        value = _command_service(orchestrator).reject_learning_candidate(request)
    except (ValueError, RuntimeError) as exc:
        terminal.console.print(f"Learning 拒绝失败：{exc}")
    else:
        _show_learning_result(terminal, value)
    return False


async def _handle_memory_lifecycle(orchestrator, terminal, prompt_session, request) -> bool:
    if request.operation == "delete":
        terminal.console.print(
            "这是逻辑删除：历史修订和备份仍会保留，当前记录将不再参与 Memory 选择。"
        )
    confirmation = await _confirm(
        terminal, prompt_session, f"确认执行 Project Knowledge {request.operation}？"
    )
    if confirmation == "closed":
        return True
    if confirmation != "yes":
        terminal.console.print("已取消，未写入状态。")
        return False
    try:
        value = _command_service(orchestrator).mutate_knowledge(request)
    except (ValueError, RuntimeError) as exc:
        terminal.console.print(f"Project Knowledge 操作失败：{exc}")
    else:
        _show_learning_result(terminal, value)
    return False


def _reset_session(orchestrator) -> None:
    orchestrator.reset_session()


def _closed_input(terminal) -> int:
    terminal.console.print("[yellow]输入已关闭，未执行待确认操作。[/yellow]")
    return 2


async def _confirm(terminal, prompt_session, question: str) -> str:
    try:
        answer = await terminal.prompt(prompt_session, question + " [y/N] ")
    except EOFError:
        return "closed"
    except KeyboardInterrupt:
        return "no"
    return "yes" if answer.strip().casefold() in {"y", "yes", "是"} else "no"


async def _exit(
    session,
    terminal,
    prompt_session,
) -> int | None:
    if session and session.dirty and not getattr(session, "persisted", False):
        confirmation = await _confirm(terminal, prompt_session, "确认退出并丢弃当前内存内容？")
        if confirmation == "closed":
            return _closed_input(terminal)
        if confirmation != "yes":
            return None
    closer = getattr(getattr(session, "committer", None), "close", None)
    if closer is not None:
        closer()
    terminal.console.print("再见。")
    return 0
