"""One input dispatch path for slash commands and ordinary AgentLoop chat."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from morrow.application.agent_runs.preparation import AgentRunPreparationError
from morrow.application.context import ContextBudgetError
from morrow.core.application import ApplicationError
from morrow.core.domain import SessionLifecycle, session_can_start_work
from morrow.core.models import AgentEvent, FinishReason
from morrow.core.runtime_control import (
    RuntimeControlEntry,
    RuntimeControlError,
    RuntimeControlErrorCode,
)

if TYPE_CHECKING:
    from morrow.application.agent_runs.preparation import (
        AgentRunPreparationService,
        PreparedAgentRunRuntime,
    )


@dataclass
class DispatchResult:
    lines: list[str] = field(default_factory=list)
    events: list[AgentEvent] = field(default_factory=list)
    action: str | None = None
    degraded: bool = False
    value: object | None = None


class SessionOrchestrator:
    def __init__(
        self,
        *,
        session,
        runtime,
        command_service,
        context_builder,
        id_source=None,
        preparation: AgentRunPreparationService | None = None,
        runtime_control=None,
    ) -> None:
        self.session = session
        self.runtime = runtime
        self.command_service = command_service
        self.context_builder = context_builder
        self.id_source = id_source
        self.preparation = preparation
        self.runtime_control = runtime_control
        self._run_active = False

    @property
    def run_active(self) -> bool:
        return self._run_active

    async def steer(self, text: str) -> RuntimeControlEntry:
        if not self._run_active or self.runtime_control is None:
            raise RuntimeControlError(
                RuntimeControlErrorCode.IDLE,
                "steering requires an active foreground run",
            )
        return self.runtime_control.enqueue_steering(self.session.session_id, text)

    async def follow_up(self, text: str):
        if self._run_active:
            if self.runtime_control is None:
                raise RuntimeControlError(
                    RuntimeControlErrorCode.IDLE,
                    "follow-up queue is unavailable",
                )
            return self.runtime_control.enqueue_follow_up(self.session.session_id, text)
        return await self.dispatch(text)

    def reset_session(self) -> None:
        if self.id_source is None:
            raise RuntimeError("session ID source is unavailable")
        new_id = self.id_source.new_id("ses")
        starter = getattr(self.session.committer, "start_new_session", None)
        if starter is not None:
            starter(self.session, new_id)
            return
        self.command_service.reset_session(new_id)

    async def stream(self, text: str):
        """Yield model events as they arrive, then a terminal dispatch result."""
        if text.startswith("/"):
            result = self.command_service.execute(text)
            if result.action == "compact":
                instructions = result.value if isinstance(result.value, str) else ""
                try:
                    compact_idle = getattr(self.runtime, "compact_idle", None)
                    if not callable(compact_idle):
                        raise ContextBudgetError("上下文压缩接口不可用")
                    compacted = await compact_idle(self.session, instructions=instructions)
                except ContextBudgetError:
                    result = type(result)(
                        ["当前无法安全执行上下文压缩，请先确保 Session 空闲且上下文可用。"],
                        action="compact",
                    )
                else:
                    result = type(
                        result,
                    )(
                        [
                            "上下文压缩已完成。"
                            if compacted
                            else "当前没有可安全压缩的已完成上下文。"
                        ],
                        action="compact",
                    )
            yield DispatchResult(
                lines=result.lines,
                action=result.action,
                value=getattr(result, "value", None),
            )
            return
        api = getattr(self.command_service, "api", None)
        if api is not None:
            try:
                durable_session = api.get_session(self.session.session_id)
            except ApplicationError as exc:
                yield DispatchResult(lines=[exc.message], degraded=True)
                return
            if durable_session is not None:
                self.session.lifecycle = durable_session.lifecycle
                self.session.health = durable_session.health
        if self.session.lifecycle is not SessionLifecycle.ACTIVE:
            yield DispatchResult(lines=["当前 Session 已归档，无法开始新的 Turn。"])
            return
        if self._run_active:
            yield DispatchResult(lines=["当前已有前台 AgentRun 正在运行。"], degraded=True)
            return
        self._run_active = True
        try:
            next_text = text
            next_client_message_id = None
            while True:
                terminal_reason: FinishReason | None = None
                async for event in self._stream_turn(
                    next_text,
                    client_message_id=next_client_message_id,
                ):
                    if event.type == "turn.completed":
                        try:
                            terminal_reason = FinishReason(event.payload.get("finish_reason"))
                        except (TypeError, ValueError):
                            terminal_reason = FinishReason.ERROR
                    yield event
                if self.runtime_control is None or terminal_reason not in {
                    FinishReason.STOP,
                    FinishReason.STEERED,
                }:
                    break
                entry = self.runtime_control.peek_steering(self.session.session_id)
                if entry is None and terminal_reason is FinishReason.STOP:
                    entry = self.runtime_control.peek_follow_up(self.session.session_id)
                if entry is None:
                    break
                next_text = entry.text
                next_client_message_id = entry.client_message_id
        finally:
            self._run_active = False
        yield DispatchResult()

    async def _stream_turn(
        self,
        text: str,
        *,
        client_message_id: str | None = None,
    ):
        if client_message_id is None and self.id_source is not None:
            client_message_id = self.id_source.new_id("cmsg")
        prepared: PreparedAgentRunRuntime | None = None
        startup_error: str | ApplicationError | None = None
        prepared_agent_run_id: str | None = None
        try:
            if self.preparation is not None:
                durable_runtime = self.session.durable_runtime
                if client_message_id is not None and durable_runtime is not None:
                    try:
                        probe = durable_runtime.probe(self.session, text, client_message_id)
                    except ApplicationError:
                        # Admission refusals (health/lifecycle) surface through
                        # run_task as ordered error events, never here.
                        probe = None
                    if probe is not None and probe.kind == "new":
                        if self.id_source is not None:
                            run_id_source = getattr(
                                getattr(self.runtime, "loop", None), "id_source", None
                            )
                            if run_id_source is not None:
                                prepared_agent_run_id = run_id_source.new_id("arun")
                        try:
                            prepare_new = self.preparation.prepare_new
                            if "agent_run_id" in inspect.signature(prepare_new).parameters:
                                prepared = prepare_new(agent_run_id=prepared_agent_run_id)
                            else:
                                prepared = prepare_new()
                        except (ApplicationError, AgentRunPreparationError, ValueError) as exc:
                            startup_error = _preparation_error(exc)
            async for event in self.runtime.run_turn(
                self.session,
                text,
                client_message_id=client_message_id,
                prepared=prepared,
                startup_error=startup_error,
                agent_run_id=prepared_agent_run_id,
            ):
                yield event
        finally:
            if prepared is not None:
                close = getattr(prepared, "aclose", None)
                if close is None:
                    prepared.close()
                else:
                    result = close()
                    if inspect.isawaitable(result):
                        await result

    async def dispatch(self, text: str) -> DispatchResult:
        """Collect one streaming dispatch for non-streaming callers."""
        events: list[AgentEvent] = []
        result = DispatchResult()
        async for item in self.stream(text):
            if isinstance(item, AgentEvent):
                events.append(item)
            else:
                result = item
        result.events = events
        return result

    async def resume_recovery(self):
        """Continue an already-open Turn after an explicit recovery decision."""

        if not session_can_start_work(self.session.lifecycle, self.session.health):
            raise RuntimeError("only an active healthy Session can resume a Turn")
        if self._run_active:
            raise RuntimeError("a foreground AgentRun is already active")
        self._run_active = True
        terminal_reason: FinishReason | None = None
        prepared = None
        startup_error: str | ApplicationError | None = None
        resume_completed = False
        try:
            if self.preparation is not None and self.session.durable_runtime is not None:
                snapshot = self.session.durable_runtime.get_open_run_snapshot()
                if snapshot is not None:
                    try:
                        rehydrate = self.preparation.rehydrate
                        agent_run_id = self.session.durable_runtime.current_agent_run_id
                        if "agent_run_id" in inspect.signature(rehydrate).parameters:
                            prepared = rehydrate(snapshot, agent_run_id=agent_run_id)
                        else:
                            prepared = rehydrate(snapshot)
                    except (ApplicationError, AgentRunPreparationError, ValueError) as exc:
                        startup_error = _preparation_error(exc)
            async for event in self.runtime.loop.run_task(
                self.session,
                "",
                resume_current_turn=True,
                prepared=prepared,
                startup_error=startup_error,
            ):
                if event.type == "turn.completed":
                    try:
                        terminal_reason = FinishReason(event.payload.get("finish_reason"))
                    except (TypeError, ValueError):
                        terminal_reason = FinishReason.ERROR
                yield event
            resume_completed = True
        finally:
            if prepared is not None:
                close = getattr(prepared, "aclose", None)
                if close is None:
                    prepared.close()
                else:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
            if not resume_completed:
                self._run_active = False
        try:
            while self.runtime_control is not None and terminal_reason in {
                FinishReason.STOP,
                FinishReason.STEERED,
            }:
                entry = self.runtime_control.peek_steering(self.session.session_id)
                if entry is None and terminal_reason is FinishReason.STOP:
                    entry = self.runtime_control.peek_follow_up(self.session.session_id)
                if entry is None:
                    break
                terminal_reason = None
                async for event in self._stream_turn(
                    entry.text,
                    client_message_id=entry.client_message_id,
                ):
                    if event.type == "turn.completed":
                        try:
                            terminal_reason = FinishReason(event.payload.get("finish_reason"))
                        except (TypeError, ValueError):
                            terminal_reason = FinishReason.ERROR
                    yield event
        finally:
            self._run_active = False


def _preparation_error(error: Exception) -> str | ApplicationError:
    """Map preparation failures to bounded, non-secret public event text."""
    if isinstance(error, ApplicationError):
        return error
    if isinstance(error, AgentRunPreparationError):
        return "当前 AgentRun 无法从冻结的 Provider 证据恢复，请检查凭据或运行状态。"
    return "当前 Provider 配置不可用，请检查 active_model、模型和凭据配置。"
