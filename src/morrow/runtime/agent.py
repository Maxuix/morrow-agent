"""Single bounded chat state machine: one AgentLoop, one history write path."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from morrow.application.context import ContextBudgetError
from morrow.core.application import ApplicationError
from morrow.core.capabilities import ToolRunContext
from morrow.core.diagnostics import PublicDiagnosticError
from morrow.core.events import completion_payload, make_event
from morrow.core.execution import (
    DurableToolExecution,
    ToolExecutionDisposition,
    ToolExecutionState,
)
from morrow.core.faults import InjectedFault
from morrow.core.models import (
    AgentEvent,
    AgentStopCode,
    AssistantMessage,
    FinishReason,
    FunctionToolCall,
    Message,
    ModelCost,
    ModelErrorCode,
    ModelEvent,
    ModelFinishReason,
    ModelRef,
    ModelUsage,
    ProtocolModel,
    ToolDefinition,
    ToolMessage,
    UserMessage,
    sanitize_text,
    utc_now,
)
from morrow.core.ports import Clock, IdSource, ModelProvider
from morrow.runtime.conversation import ConversationLogError
from morrow.runtime.durable_log import durable_call_id
from morrow.runtime.ids import RandomIdSource
from morrow.runtime.session import Session
from morrow.runtime.tool_cycle import ToolCycleExecutor
from morrow.runtime.tools import (
    MIN_ERROR_ENVELOPE_CHARS,
    ToolErrorCode,
    ToolExecutionOutcome,
    ToolExecutor,
)

if TYPE_CHECKING:
    from morrow.application.agent_runs.preparation import PreparedAgentRunRuntime

TRANSIENT_MODEL_ERRORS = frozenset(
    {ModelErrorCode.NETWORK, ModelErrorCode.RATE_LIMIT, ModelErrorCode.TIMEOUT}
)
MODEL_ERROR_STOPS = {
    ModelErrorCode.AUTH: AgentStopCode.PROVIDER_AUTH,
    ModelErrorCode.NETWORK: AgentStopCode.PROVIDER_NETWORK,
    ModelErrorCode.RATE_LIMIT: AgentStopCode.PROVIDER_RATE_LIMIT,
    ModelErrorCode.TIMEOUT: AgentStopCode.PROVIDER_TIMEOUT,
    ModelErrorCode.INVALID_RESPONSE: AgentStopCode.INVALID_RESPONSE,
    ModelErrorCode.INTERNAL: AgentStopCode.INTERNAL,
}


class ModelCallOutcome(ProtocolModel):
    """One interpreted Provider attempt; carries no SDK objects or fragments."""

    message: AssistantMessage | None = None
    finish_reason: ModelFinishReason | None = None
    error_code: ModelErrorCode | None = None
    error_message: str | None = None
    usage: ModelUsage = ModelUsage.unavailable()
    cost: ModelCost = ModelCost.unavailable()


class ModelCallRunner:
    """Interprets one Provider attempt; never touches Session or history."""

    def __init__(self, provider: ModelProvider, model: ModelRef) -> None:
        self.provider = provider
        self.model = model
        self._made_progress = False
        self._outcome = ModelCallOutcome()

    async def attempt(
        self,
        messages: list[Message],
        tools: tuple[ToolDefinition, ...] = (),
    ) -> AsyncIterator[ModelEvent]:
        self._made_progress = False
        self._outcome = ModelCallOutcome()
        try:
            async for model_event in self.provider.stream(self.model, messages, tools):
                if model_event.kind == "text_delta" and model_event.text:
                    self._made_progress = True
                elif model_event.kind == "completed":
                    self._outcome = self._classify_completion(model_event)
                elif model_event.kind == "error":
                    self._made_progress = self._made_progress or model_event.made_progress
                    self._outcome = ModelCallOutcome(
                        error_code=model_event.error_code or ModelErrorCode.INTERNAL,
                        error_message=model_event.error_message,
                        usage=model_event.usage,
                        cost=model_event.cost,
                    )
                yield model_event
            if self._outcome.message is None and self._outcome.error_code is None:
                self._outcome = ModelCallOutcome(
                    error_code=ModelErrorCode.INVALID_RESPONSE,
                    error_message="模型响应未正常结束",
                    usage=self._outcome.usage,
                    cost=self._outcome.cost,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._outcome = ModelCallOutcome(
                error_code=ModelErrorCode.INTERNAL,
                error_message="模型服务发生未预期错误",
                usage=self._outcome.usage,
                cost=self._outcome.cost,
            )

    @property
    def outcome(self) -> ModelCallOutcome:
        return self._outcome

    @property
    def made_progress(self) -> bool:
        return self._made_progress

    @staticmethod
    def _classify_completion(model_event: ModelEvent) -> ModelCallOutcome:
        reason = model_event.finish_reason
        message = model_event.message
        if reason not in (ModelFinishReason.STOP, ModelFinishReason.TOOL_CALLS):
            return ModelCallOutcome(
                finish_reason=reason,
                error_code=ModelErrorCode.INVALID_RESPONSE,
                error_message="模型响应未正常结束",
                usage=model_event.usage,
                cost=model_event.cost,
            )
        if reason == ModelFinishReason.STOP:
            if message is None or not (message.content or "").strip() or bool(message.tool_calls):
                return ModelCallOutcome(
                    finish_reason=reason,
                    error_code=ModelErrorCode.INVALID_RESPONSE,
                    error_message="模型没有返回可见文本",
                    usage=model_event.usage,
                    cost=model_event.cost,
                )
        elif message is None or not message.tool_calls:
            return ModelCallOutcome(
                finish_reason=reason,
                error_code=ModelErrorCode.INVALID_RESPONSE,
                error_message="模型没有返回工具调用",
                usage=model_event.usage,
                cost=model_event.cost,
            )
        return ModelCallOutcome(
            message=message,
            finish_reason=reason,
            usage=model_event.usage,
            cost=model_event.cost,
        )


def _canonical_json_or_text(value: str) -> str:
    try:
        return json.dumps(
            json.loads(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return value.strip()


def _outcome_signature(outcome: ToolExecutionOutcome) -> tuple:
    try:
        payload = json.loads(outcome.envelope)
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    if outcome.ok:
        return ("ok", _canonical_json_or_text(json.dumps(payload.get("result"))))
    error = payload.get("error") if isinstance(payload, dict) else {}
    return (
        "error",
        str(error.get("code", outcome.error_code.value if outcome.error_code else "internal")),
        bool(error.get("retryable", False)),
    )


def _cycle_signature(message: AssistantMessage, outcomes: list[ToolExecutionOutcome]) -> tuple:
    return tuple(
        (
            call.name,
            _canonical_json_or_text(call.arguments),
            _outcome_signature(outcome),
        )
        for call, outcome in zip(message.tool_calls, outcomes, strict=True)
    )


def _has_repeated_suffix(signatures: list[tuple], repeat: int, max_pattern: int) -> bool:
    for pattern_length in range(1, min(max_pattern, len(signatures) // repeat) + 1):
        pattern = signatures[-pattern_length:]
        if all(
            signatures[-pattern_length * (index + 1) : -pattern_length * index or None] == pattern
            for index in range(repeat)
        ):
            return True
    return False


def _pending_cancellation() -> bool:
    task = asyncio.current_task()
    return bool(task and task.cancelling())


def _consume_cancellation_request() -> None:
    task = asyncio.current_task()
    if task is not None:
        while task.cancelling():
            task.uncancel()


@dataclass
class _AgentRunState:
    """Mutable state for one bounded AgentLoop run."""

    turn_id: str
    run_context: ToolRunContext
    deadline: float
    agent_run_id: str | None = None
    visible: str = ""
    model_attempts: int = 0
    tool_rounds: int = 0
    tool_calls: int = 0
    retry_count: int = 0
    total_retry_count: int = 0
    max_estimated_request_chars: int = 0
    request_char_budget: int = 1
    cleared_cycle_count: int = 0
    dropped_turn_count: int = 0
    dropped_cycle_count: int = 0
    dropped_record_count: int = 0
    cycle_signatures: list[tuple] = field(default_factory=list)
    active_calls: tuple[FunctionToolCall, ...] = ()
    durable_executions: tuple[DurableToolExecution, ...] = ()
    active_running_id: str | None = None
    active_result_limit: int | None = None
    final_committed: bool = False
    facts_retained: bool = False
    started: bool = False
    settled: bool = False
    observation_finalized: bool = False
    crashed: bool = False
    terminal_finish_reason: FinishReason | None = None
    stop_code: AgentStopCode | None = None


class _RunEventEmitter:
    """Render one run's ordered public events without owning loop transitions."""

    def __init__(
        self,
        *,
        new_id: Callable[[str], str],
        session: Session,
        clock: Clock | None,
        turn_id: Callable[[], str],
        visible: Callable[[], str],
        retain_facts: Callable[[str], None],
    ) -> None:
        self.new_id = new_id
        self.session = session
        self.clock = clock
        self.turn_id = turn_id
        self.visible = visible
        self.retain_facts = retain_facts
        self.sequence = 0

    def event(self, event_type: str, payload: dict) -> AgentEvent:
        self.sequence += 1
        return make_event(
            event_type=event_type,
            event_id=self.new_id("evt"),
            session_id=self.session.session_id,
            turn_id=self.turn_id(),
            sequence=self.sequence,
            payload=payload,
            timestamp=self.clock.now() if self.clock else None,
        )

    def fatal(self, message: str, stop_code: AgentStopCode) -> tuple[AgentEvent, AgentEvent]:
        return (
            self.event(
                "error",
                {"message": sanitize_text(message), "stop_code": stop_code.value},
            ),
            self.event(
                "turn.completed",
                completion_payload(FinishReason.ERROR, self.visible(), stop_code=stop_code),
            ),
        )

    def tool_status(
        self,
        call,
        status: str,
        ordinal: int,
        total: int,
        *,
        error_code: ToolErrorCode | None = None,
        truncated: bool = False,
    ) -> AgentEvent:
        payload = {
            "call_id": call.id,
            "name": call.name,
            "status": status,
            "ordinal": ordinal,
            "total": total,
        }
        if error_code is not None:
            payload["error_code"] = error_code.value
        if truncated:
            payload["truncated"] = True
        return self.event("tool.status", payload)

    def terminal_error(
        self,
        message: str,
        stop_code: AgentStopCode,
        *,
        interrupted: tuple[str, ...] = (),
    ) -> tuple[AgentEvent, AgentEvent]:
        self.session.finish_turn(FinishReason.ERROR, interrupted_call_ids=interrupted)
        self.retain_facts(FinishReason.ERROR.value)
        return self.fatal(message, stop_code)

    def synthetic_statuses(
        self,
        unresolved: tuple[str, ...],
        *,
        active_calls,
        active_running_id: str | None,
        code: ToolErrorCode,
        running_status: str,
    ) -> list[AgentEvent]:
        statuses = []
        by_id = {call.id: (index, call) for index, call in enumerate(active_calls, start=1)}
        for call_id in unresolved:
            ordinal, call = by_id[call_id]
            status = running_status if call_id == active_running_id else "skipped"
            statuses.append(
                self.tool_status(
                    call,
                    status,
                    ordinal,
                    len(active_calls),
                    error_code=code,
                )
            )
        return statuses


class AgentLoop:
    """Owns task lifecycle, budgets, tool execution and every chat history write."""

    def __init__(
        self,
        provider: ModelProvider,
        model: ModelRef,
        context_builder,
        *,
        id_source: IdSource | None = None,
        clock: Clock | None = None,
        tool_executor: ToolExecutor | None = None,
        grant_provider=None,
        monotonic=None,
    ) -> None:
        self.runner = ModelCallRunner(provider, model)
        self.context_builder = context_builder
        self.id_source = id_source or RandomIdSource()
        self.clock = clock
        self.run_policy = context_builder.run_policy
        self.tool_executor = tool_executor
        self.grant_provider = grant_provider
        self.monotonic = monotonic or time.monotonic
        self.tool_cycle = (
            ToolCycleExecutor(
                tool_executor,
                self.run_policy,
                wall_now=self._wall_now,
            )
            if tool_executor is not None
            else None
        )

    def _id(self, prefix: str) -> str:
        return self.id_source.new_id(prefix)

    def _wall_now(self, session: Session | None = None):
        if self.clock is not None:
            return self.clock.now()
        if session is not None and session.durable_runtime is not None:
            return session.durable_runtime.now()
        return utc_now()

    async def _request_pending_grant(self, session: Session) -> None:
        if not session.pending_full_access_grant:
            return
        if self.grant_provider is None:
            raise RuntimeError("本地 Host 权限授予接口不可用")
        result = self.grant_provider(session)
        if inspect.isawaitable(result):
            result = await result
        if not result:
            raise RuntimeError("本地 Host 权限授予未完成")
        session.pending_full_access_grant = False

    async def run_task(
        self,
        session: Session,
        user_input: str,
        *,
        client_message_id: str | None = None,
        resume_current_turn: bool = False,
        prepared: PreparedAgentRunRuntime | None = None,
        startup_error: str | None = None,
        agent_run_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        client_message_id = client_message_id or self._id("cmsg")
        if prepared is not None:
            provider = prepared.provider
            model = prepared.model
            context_builder = prepared.context_builder
            tool_executor = prepared.tool_executor
            policy = prepared.run_policy
        else:
            provider = self.runner.provider
            model = self.runner.model
            context_builder = self.context_builder
            tool_executor = self.tool_executor
            policy = self.run_policy
        runner = ModelCallRunner(provider, model)
        tool_cycle = (
            ToolCycleExecutor(tool_executor, policy, wall_now=self._wall_now)
            if tool_executor is not None
            else None
        )
        durable_runtime = session.durable_runtime
        initial_turn_id = self._id("turn")
        initial_agent_run_id = (
            getattr(durable_runtime, "current_agent_run_id", None)
            if durable_runtime is not None and resume_current_turn
            else None
        )
        state = _AgentRunState(
            turn_id=initial_turn_id,
            run_context=ToolRunContext(
                run_id=initial_agent_run_id or initial_turn_id,
                session_id=session.session_id,
            ),
            agent_run_id=initial_agent_run_id,
            deadline=self.monotonic() + policy.max_run_seconds,
        )

        observation_runtime = (
            durable_runtime
            if durable_runtime is not None
            and callable(getattr(durable_runtime, "admit_model_request", None))
            else None
        )

        def install_agent_run(agent_run_id: str | None) -> None:
            if agent_run_id is None:
                return
            state.agent_run_id = agent_run_id
            state.run_context = ToolRunContext(
                run_id=agent_run_id,
                session_id=session.session_id,
            )

        def restore_observation_progress() -> None:
            """Continue a resumed AgentRun after already-settled request rows."""

            if not resume_current_turn or observation_runtime is None or state.agent_run_id is None:
                return
            getter = getattr(observation_runtime, "get_agent_run_observation", None)
            if not callable(getter):
                return
            observation = getter(state.agent_run_id)
            if observation is None or not observation.requests:
                return
            requests = observation.requests
            latest = observation.requests[-1]
            latest_is_open = latest.state.value == "admitted"
            state.model_attempts = max(
                0,
                max(item.attempt_ordinal for item in requests) - (1 if latest_is_open else 0),
            )
            state.max_estimated_request_chars = max(
                item.estimated_request_chars for item in requests
            )
            state.request_char_budget = latest.request_char_budget
            state.cleared_cycle_count = sum(item.cleared_cycle_count for item in requests)
            state.dropped_turn_count = sum(item.dropped_turn_count for item in requests)
            state.dropped_cycle_count = sum(item.dropped_cycle_count for item in requests)
            state.dropped_record_count = sum(item.dropped_record_count for item in requests)
            state.tool_rounds = max(item.tool_rounds for item in requests)
            state.tool_calls = max(item.tool_calls for item in requests)
            settled_before_latest = requests[:-1] if latest_is_open else requests
            state.total_retry_count = sum(
                item.state.value == "failed" for item in settled_before_latest
            )
            trailing_failures = 0
            for item in reversed(settled_before_latest):
                if item.state.value != "failed":
                    break
                trailing_failures += 1
            state.retry_count = trailing_failures

        def settle_model_request(
            admission,
            *,
            state_name: str,
            finish_reason: ModelFinishReason | None = None,
            error_code: ModelErrorCode | None = None,
            usage: ModelUsage | None = None,
            cost: ModelCost | None = None,
        ) -> None:
            if admission is None or observation_runtime is None:
                return
            try:
                observation_runtime.settle_model_request(
                    admission.model_request_id,
                    state=state_name,
                    finish_reason=finish_reason,
                    error_code=error_code,
                    usage=usage or ModelUsage.unavailable(),
                    cost=cost or ModelCost.unavailable(),
                )
            except Exception:
                # A failed observation write remains visible as an open request;
                # it must never replace the existing public terminal lifecycle.
                return

        def finalize_observation() -> None:
            if (
                observation_runtime is None
                or state.agent_run_id is None
                or state.observation_finalized
                or state.crashed
            ):
                return
            finish_reason = state.terminal_finish_reason or FinishReason.ERROR
            try:
                observation_runtime.finalize_agent_run(
                    agent_run_id=state.agent_run_id,
                    finish_reason=finish_reason,
                    stop_code=state.stop_code,
                    model_attempts=state.model_attempts,
                    retry_count=state.total_retry_count,
                    tool_rounds=state.tool_rounds,
                    tool_calls=state.tool_calls,
                    max_estimated_request_chars=state.max_estimated_request_chars,
                    request_char_budget=state.request_char_budget,
                    cleared_cycle_count=state.cleared_cycle_count,
                    dropped_turn_count=state.dropped_turn_count,
                    dropped_cycle_count=state.dropped_cycle_count,
                    dropped_record_count=state.dropped_record_count,
                )
            except Exception:
                # Observation persistence must never leak raw storage details into
                # the existing public event lifecycle.
                return
            state.observation_finalized = True

        def retain_facts(finish_reason: str = "unknown") -> None:
            if not state.facts_retained:
                session.retain_run_facts(state.run_context, finish_reason=finish_reason)
                state.facts_retained = True

        events = _RunEventEmitter(
            new_id=self._id,
            session=session,
            clock=self.clock,
            turn_id=lambda: state.turn_id,
            visible=lambda: state.visible,
            retain_facts=retain_facts,
        )
        event = events.event
        fatal = events.fatal
        tool_status = events.tool_status
        emit_terminal_error = events.terminal_error

        def terminal_error(
            message: str,
            stop_code: AgentStopCode,
            *,
            interrupted: tuple[str, ...] = (),
        ) -> tuple[AgentEvent, AgentEvent]:
            state.terminal_finish_reason = FinishReason.ERROR
            state.stop_code = stop_code
            return emit_terminal_error(message, stop_code, interrupted=interrupted)

        def synthetic_statuses(
            unresolved: tuple[str, ...], *, code: ToolErrorCode, running_status: str
        ) -> list[AgentEvent]:
            return events.synthetic_statuses(
                unresolved,
                active_calls=state.active_calls,
                active_running_id=state.active_running_id,
                code=code,
                running_status=running_status,
            )

        try:
            if startup_error is not None:
                if resume_current_turn:
                    current_turn_id = (
                        durable_runtime.current_turn_id if durable_runtime is not None else None
                    )
                    if current_turn_id:
                        state.turn_id = current_turn_id
                        install_agent_run(getattr(durable_runtime, "current_agent_run_id", None))
                        restore_observation_progress()
                        if state.agent_run_id is None:
                            state.run_context = ToolRunContext(
                                run_id=state.turn_id,
                                session_id=session.session_id,
                            )
                state.started = True
                yield event("turn.started", {})
                if session.log.has_active_turn:
                    try:
                        session.finish_turn(FinishReason.ERROR)
                    except ConversationLogError:
                        pass
                state.settled = True
                state.terminal_finish_reason = FinishReason.ERROR
                state.stop_code = AgentStopCode.INTERNAL
                retain_facts(FinishReason.ERROR.value)
                for item in fatal(startup_error, AgentStopCode.INTERNAL):
                    yield item
                return
            if resume_current_turn:
                if not session.log.has_active_turn:
                    raise ConversationLogError("no active turn is available to resume")
                current_turn_id = (
                    durable_runtime.current_turn_id if durable_runtime is not None else None
                )
                if current_turn_id:
                    state.turn_id = current_turn_id
                    install_agent_run(getattr(durable_runtime, "current_agent_run_id", None))
                    restore_observation_progress()
                    if state.agent_run_id is None:
                        state.run_context = ToolRunContext(
                            run_id=state.turn_id,
                            session_id=session.session_id,
                        )
            else:
                if durable_runtime is not None:
                    requested_agent_run_id = (
                        agent_run_id
                        or (
                            getattr(prepared, "agent_run_id", None)
                            if prepared is not None
                            else None
                        )
                        or self._id("arun")
                    )
                    submit_outcome = durable_runtime.submit_user(
                        session,
                        user_input,
                        client_message_id,
                        turn_id=state.turn_id,
                        agent_run_id=requested_agent_run_id,
                        tools=tool_executor.definitions if tool_executor else (),
                        prepared_spec=prepared.spec if prepared is not None else None,
                        prepared_mcp_run=(
                            getattr(prepared, "mcp_run", None) if prepared is not None else None
                        ),
                    )
                    if submit_outcome.turn_id:
                        state.turn_id = submit_outcome.turn_id
                        if submit_outcome.kind == "accepted":
                            install_agent_run(
                                getattr(durable_runtime, "current_agent_run_id", None)
                                or requested_agent_run_id
                            )
                        else:
                            state.agent_run_id = None
                            state.run_context = ToolRunContext(
                                run_id=state.turn_id,
                                session_id=session.session_id,
                            )
                    if submit_outcome.kind != "accepted":
                        yield event("turn.started", {})
                        state.started = True
                        state.settled = True
                        if submit_outcome.kind == "closed_replay":
                            text = submit_outcome.assistant_text or ""
                            if text:
                                yield event("text.delta", {"text": text})
                            state.terminal_finish_reason = FinishReason.STOP
                            retain_facts(FinishReason.STOP.value)
                            yield event(
                                "turn.completed", completion_payload(FinishReason.STOP, text)
                            )
                            return
                        message = (
                            "当前回合需要恢复后才能继续"
                            if submit_outcome.kind == "recovery"
                            else "client_message_id 与已有请求冲突"
                        )
                        state.terminal_finish_reason = FinishReason.ERROR
                        state.stop_code = AgentStopCode.INTERNAL
                        yield event(
                            "error",
                            {"message": message, "stop_code": AgentStopCode.INTERNAL.value},
                        )
                        retain_facts(FinishReason.ERROR.value)
                        yield event(
                            "turn.completed",
                            completion_payload(
                                FinishReason.ERROR, "", stop_code=AgentStopCode.INTERNAL
                            ),
                        )
                        return
                else:
                    session.begin_user_turn(UserMessage(content=user_input))

            state.started = True
            tools = tool_executor.definitions if tool_executor else ()
            yield event("turn.started", {})
            permission_snapshot = None
            await self._request_pending_grant(session)

            def freeze_permissions() -> None:
                nonlocal permission_snapshot
                if durable_runtime is None or permission_snapshot is not None:
                    return
                permission_snapshot = durable_runtime.freeze_permission_snapshot(
                    session,
                    tools=tools,
                    mcp_review_evidence=(
                        getattr(getattr(prepared, "mcp_run", None), "review_evidence", ())
                        if prepared is not None
                        else ()
                    ),
                )

            while True:
                if _pending_cancellation():
                    _consume_cancellation_request()
                    raise asyncio.CancelledError
                if self.monotonic() >= state.deadline:
                    for item in terminal_error("任务超过总运行时间", AgentStopCode.RUN_TIMEOUT):
                        yield item
                    return
                if state.model_attempts >= policy.max_model_attempts:
                    for item in terminal_error(
                        "模型调用次数已达上限", AgentStopCode.MODEL_CALL_LIMIT
                    ):
                        yield item
                    return
                if state.tool_rounds >= policy.max_tool_rounds:
                    for item in terminal_error("工具轮次已达上限", AgentStopCode.TOOL_CALL_LIMIT):
                        yield item
                    return
                try:
                    context = context_builder.build(session, tools=tools)
                    call_messages = list(context.messages)
                    context_builder.validate_request(call_messages, tools)
                except ContextBudgetError as exc:
                    for item in terminal_error(str(exc), AgentStopCode.CONTEXT_BUDGET):
                        yield item
                    return

                state.max_estimated_request_chars = max(
                    state.max_estimated_request_chars, context.estimated_request_chars
                )
                state.request_char_budget = context_builder.request_char_limit
                state.cleared_cycle_count += context.cleared_cycle_count
                state.dropped_turn_count += context.dropped_turn_count
                state.dropped_cycle_count += context.dropped_cycle_count
                state.dropped_record_count += context.dropped_record_count
                state.model_attempts += 1
                admission = None
                request_state: str | None = None
                request_error: ModelErrorCode | None = None
                if observation_runtime is not None and state.agent_run_id is not None:
                    admission = observation_runtime.admit_model_request(
                        agent_run_id=state.agent_run_id,
                        attempt_ordinal=state.model_attempts,
                        estimated_request_chars=context.estimated_request_chars,
                        request_char_budget=context_builder.request_char_limit,
                        cleared_cycle_count=context.cleared_cycle_count,
                        dropped_turn_count=context.dropped_turn_count,
                        dropped_cycle_count=context.dropped_cycle_count,
                        dropped_record_count=context.dropped_record_count,
                        tool_rounds=state.tool_rounds,
                        tool_calls=state.tool_calls,
                    )
                remaining_model_time = state.deadline - self.monotonic()
                if remaining_model_time <= 0:
                    request_state = "failed"
                    request_error = ModelErrorCode.TIMEOUT
                    settle_model_request(
                        admission,
                        state_name="failed",
                        error_code=request_error,
                    )
                    for item in terminal_error("任务超过总运行时间", AgentStopCode.RUN_TIMEOUT):
                        yield item
                    return
                stream = runner.attempt(call_messages, tools)
                try:
                    while True:
                        try:
                            async with asyncio.timeout(remaining_model_time):
                                model_event = await anext(stream)
                        except StopAsyncIteration:
                            break
                        except TimeoutError:
                            request_state = "failed"
                            request_error = ModelErrorCode.TIMEOUT
                            for item in terminal_error(
                                "任务超过总运行时间", AgentStopCode.RUN_TIMEOUT
                            ):
                                yield item
                            return
                        if model_event.kind == "text_delta" and model_event.text:
                            state.visible += model_event.text
                            yield event("text.delta", {"text": model_event.text})
                        remaining_model_time = state.deadline - self.monotonic()
                        if remaining_model_time <= 0:
                            request_state = "failed"
                            request_error = ModelErrorCode.TIMEOUT
                            for item in terminal_error(
                                "任务超过总运行时间", AgentStopCode.RUN_TIMEOUT
                            ):
                                yield item
                            return
                finally:
                    try:
                        close = getattr(stream, "aclose", None)
                        if close is not None:
                            await close()
                    finally:
                        if admission is not None:
                            if _pending_cancellation():
                                settle_model_request(
                                    admission,
                                    state_name="cancelled",
                                    error_code=ModelErrorCode.TIMEOUT
                                    if request_error is ModelErrorCode.TIMEOUT
                                    else None,
                                )
                            else:
                                attempt_outcome = runner.outcome
                                if request_state == "failed":
                                    settled_state = "failed"
                                elif attempt_outcome.error_code is not None:
                                    settled_state = "failed"
                                else:
                                    settled_state = "completed"
                                settle_model_request(
                                    admission,
                                    state_name=settled_state,
                                    finish_reason=attempt_outcome.finish_reason,
                                    error_code=request_error or attempt_outcome.error_code,
                                    usage=attempt_outcome.usage,
                                    cost=attempt_outcome.cost,
                                )
                if _pending_cancellation():
                    _consume_cancellation_request()
                    raise asyncio.CancelledError
                outcome = runner.outcome
                if outcome.error_code is not None:
                    if (
                        not runner.made_progress
                        and state.retry_count < policy.model_retry_limit
                        and outcome.error_code in TRANSIENT_MODEL_ERRORS
                    ):
                        state.retry_count += 1
                        state.total_retry_count += 1
                        yield event("status.changed", {"status": "retrying"})
                        continue
                    if outcome.finish_reason == ModelFinishReason.LENGTH:
                        stop_code = AgentStopCode.MODEL_OUTPUT_LIMIT
                    elif outcome.finish_reason == ModelFinishReason.CONTENT_FILTER:
                        stop_code = AgentStopCode.CONTENT_FILTERED
                    else:
                        stop_code = MODEL_ERROR_STOPS[outcome.error_code]
                    for item in terminal_error(outcome.error_message or "模型调用失败", stop_code):
                        yield item
                    return
                state.retry_count = 0
                message = outcome.message
                is_final_text = (
                    outcome.finish_reason == ModelFinishReason.STOP
                    and message is not None
                    and not message.tool_calls
                )
                if is_final_text:
                    try:
                        freeze_permissions()
                        session.append_assistant(message)
                    except ConversationLogError:
                        for item in terminal_error(
                            "模型响应未正常结束", AgentStopCode.INVALID_RESPONSE
                        ):
                            yield item
                        return
                    state.final_committed = True
                    current = asyncio.current_task()
                    if current is not None:
                        while current.cancelling():
                            current.uncancel()
                    session.finish_turn(FinishReason.STOP)
                    state.terminal_finish_reason = FinishReason.STOP
                    state.stop_code = None
                    retain_facts(FinishReason.STOP.value)
                    yield event(
                        "turn.completed",
                        completion_payload(FinishReason.STOP, state.visible),
                    )
                    return
                if self.tool_executor is None or message is None:
                    for item in terminal_error(
                        "模型响应未正常结束", AgentStopCode.INVALID_RESPONSE
                    ):
                        yield item
                    return

                calls = message.tool_calls
                if len(calls) > 1 and not policy.provider_tool_support.multiple_tool_calls:
                    for item in terminal_error(
                        "当前 Provider 不支持并行工具调用", AgentStopCode.INVALID_RESPONSE
                    ):
                        yield item
                    return
                if len(calls) > policy.max_tool_calls_per_cycle:
                    for item in terminal_error(
                        "单轮工具调用数量已达上限", AgentStopCode.TOOL_CALL_LIMIT
                    ):
                        yield item
                    return
                per_call_result_limit = self._cycle_result_limit(message, policy, context_builder)
                if per_call_result_limit is None:
                    for item in terminal_error(
                        "模型工具调用输出超过 Cycle 预算",
                        AgentStopCode.MODEL_OUTPUT_LIMIT,
                    ):
                        yield item
                    return

                try:
                    freeze_permissions()
                    planned = session.log.plan_append_assistant(message)
                    if durable_runtime is not None:
                        state.durable_executions = durable_runtime.prepare_and_commit_assistant(
                            planned,
                            message,
                            run_context=state.run_context,
                            tool_executor=tool_executor,
                        )
                        missing = [
                            item.tool_execution_id
                            for item in state.durable_executions
                            if not durable_runtime.execution_is_visible(item.tool_execution_id)
                        ]
                        if missing:
                            raise ConversationLogError("committed tool intent is not observable")
                    else:
                        session.commit_append(planned)
                except ConversationLogError:
                    for item in terminal_error(
                        "模型响应未正常结束", AgentStopCode.INVALID_RESPONSE
                    ):
                        yield item
                    return
                state.active_calls = calls
                state.active_running_id = None
                state.active_result_limit = per_call_result_limit
                if state.tool_calls + len(calls) > policy.max_tool_calls:
                    unresolved = session.log.unresolved_call_ids
                    interrupted = self._close_unresolved(
                        session,
                        state.active_calls,
                        state.durable_executions,
                        ToolErrorCode.BUDGET_EXHAUSTED,
                        "工具调用总数已达上限",
                        tool_executor=tool_executor,
                        result_limit=per_call_result_limit,
                    )
                    for status_event in synthetic_statuses(
                        unresolved,
                        code=ToolErrorCode.BUDGET_EXHAUSTED,
                        running_status="skipped",
                    ):
                        yield status_event
                    state.tool_calls += len(calls)
                    state.tool_rounds += 1
                    for item in terminal_error(
                        "工具调用总数已达上限",
                        AgentStopCode.TOOL_CALL_LIMIT,
                        interrupted=interrupted,
                    ):
                        yield item
                    return

                state.tool_calls += len(calls)
                state.tool_rounds += 1
                cycle_outcomes: list[ToolExecutionOutcome] = []
                for index, call in enumerate(calls, start=1):
                    if _pending_cancellation():
                        _consume_cancellation_request()
                        raise asyncio.CancelledError
                    now = self.monotonic()
                    if now >= state.deadline:
                        unresolved = session.log.unresolved_call_ids
                        interrupted = self._close_unresolved(
                            session,
                            state.active_calls,
                            state.durable_executions,
                            ToolErrorCode.BUDGET_EXHAUSTED,
                            "任务总运行时间已耗尽",
                            tool_executor=tool_executor,
                            result_limit=state.active_result_limit,
                        )
                        for status_event in synthetic_statuses(
                            unresolved,
                            code=ToolErrorCode.BUDGET_EXHAUSTED,
                            running_status="skipped",
                        ):
                            yield status_event
                        for item in terminal_error(
                            "任务超过总运行时间",
                            AgentStopCode.RUN_TIMEOUT,
                            interrupted=interrupted,
                        ):
                            yield item
                        return
                    state.active_running_id = call.id
                    yield tool_status(call, "running", index, len(calls))
                    durable = (
                        state.durable_executions[index - 1] if state.durable_executions else None
                    )
                    if tool_cycle is None:
                        raise RuntimeError("tool cycle executor is unavailable")
                    call_execution = await tool_cycle.execute_call(
                        session,
                        call,
                        durable_execution=durable,
                        run_context=state.run_context,
                        ordinal=index,
                        total=len(calls),
                        result_limit=per_call_result_limit,
                        remaining_run_seconds=state.deadline - now,
                    )
                    result = call_execution.outcome
                    durable = call_execution.durable_execution
                    if durable is not None:
                        if durable_runtime is None:
                            raise RuntimeError(
                                "durable execution requires a durable runtime coordinator"
                            )
                        planned_tool = session.log.plan_append_tool_result(call.id, result.envelope)
                        durable_runtime.commit_tool_message(
                            planned_tool, durable, now=self._wall_now(session)
                        )
                    else:
                        session.append_tool_result(call.id, result.envelope)
                    cycle_outcomes.append(result)
                    state.run_context.note_tool_outcome(ok=result.ok, error_code=result.error_code)
                    state.active_running_id = None
                    yield tool_status(
                        call,
                        "succeeded" if result.ok else "failed",
                        index,
                        len(calls),
                        error_code=result.error_code,
                        truncated=result.truncated,
                    )
                state.active_calls = ()
                state.active_result_limit = None
                state.cycle_signatures.append(_cycle_signature(message, cycle_outcomes))
                if policy.loop_detection_enabled and _has_repeated_suffix(
                    state.cycle_signatures,
                    policy.loop_repeat_limit,
                    policy.loop_max_pattern_cycles,
                ):
                    for item in terminal_error("检测到重复工具循环", AgentStopCode.LOOP_DETECTED):
                        yield item
                    return
        except InjectedFault:
            state.settled = True
            state.crashed = True
            raise
        except asyncio.CancelledError:
            if state.final_committed:
                return
            if not state.started:
                yield event("turn.started", {})
            _consume_cancellation_request()
            unresolved = session.log.unresolved_call_ids
            interrupted = self._close_unresolved(
                session,
                state.active_calls,
                state.durable_executions,
                ToolErrorCode.CANCELLED,
                "任务已取消，工具调用未完成",
                tool_executor=tool_executor,
                result_limit=state.active_result_limit,
            )
            for status_event in synthetic_statuses(
                unresolved,
                code=ToolErrorCode.CANCELLED,
                running_status="cancelled",
            ):
                yield status_event
            if session.log.has_active_turn:
                try:
                    session.finish_turn(FinishReason.CANCELLED, interrupted_call_ids=interrupted)
                except ConversationLogError:
                    pass
            state.terminal_finish_reason = FinishReason.CANCELLED
            state.stop_code = None
            retain_facts(FinishReason.CANCELLED.value)
            yield event(
                "turn.completed",
                completion_payload(FinishReason.CANCELLED, state.visible),
            )
            return
        except Exception as exc:
            if not state.started:
                state.started = True
                yield event("turn.started", {})
            unresolved = session.log.unresolved_call_ids
            interrupted = self._close_unresolved(
                session,
                state.active_calls,
                state.durable_executions,
                ToolErrorCode.INTERNAL,
                "内部错误，工具调用未完成",
                tool_executor=tool_executor,
                result_limit=state.active_result_limit,
            )
            for status_event in synthetic_statuses(
                unresolved,
                code=ToolErrorCode.INTERNAL,
                running_status="failed",
            ):
                yield status_event
            if session.log.has_active_turn:
                try:
                    session.finish_turn(FinishReason.ERROR, interrupted_call_ids=interrupted)
                except ConversationLogError:
                    pass
            state.terminal_finish_reason = FinishReason.ERROR
            state.stop_code = AgentStopCode.INTERNAL
            retain_facts(FinishReason.ERROR.value)
            if isinstance(exc, ApplicationError):
                message = exc.message
            elif isinstance(exc, PublicDiagnosticError):
                message = exc.public_message
            else:
                message = "任务执行发生未预期错误"
            for item in fatal(message, AgentStopCode.INTERNAL):
                yield item
            return
        finally:
            retain_facts()
            if not state.settled and session.log.has_active_turn:
                try:
                    interrupted = self._close_unresolved(
                        session,
                        state.active_calls,
                        state.durable_executions,
                        ToolErrorCode.CANCELLED,
                        "任务已取消，工具调用未完成",
                        tool_executor=tool_executor,
                        result_limit=state.active_result_limit,
                    )
                    session.finish_turn(FinishReason.CANCELLED, interrupted_call_ids=interrupted)
                    state.terminal_finish_reason = FinishReason.CANCELLED
                    state.stop_code = None
                except Exception:
                    pass
            finalize_observation()
            if prepared is not None:
                close = getattr(prepared, "aclose", None)
                if close is None:
                    prepared.close()
                else:
                    result = close()
                    if inspect.isawaitable(result):
                        await result

    def _cycle_result_limit(self, message: AssistantMessage, policy, context_builder) -> int | None:
        """Largest equal raw envelope cap safe under worst-case JSON escaping."""
        calls = message.tool_calls
        high = policy.effective_result_limit
        low = MIN_ERROR_ENVELOPE_CHARS

        def estimated(limit: int) -> int:
            worst_case = tuple(
                ToolMessage(tool_call_id=call.id, content="\\" * limit) for call in calls
            )
            return context_builder.estimate_request_chars((message, *worst_case), ())

        if high < low or estimated(low) > policy.effective_cycle_limit:
            return None
        accepted = low
        while low <= high:
            middle = (low + high) // 2
            if estimated(middle) <= policy.effective_cycle_limit:
                accepted = middle
                low = middle + 1
            else:
                high = middle - 1
        return accepted

    def _close_unresolved(
        self,
        session: Session,
        active_calls,
        durable_executions,
        code: ToolErrorCode,
        message: str,
        *,
        tool_executor,
        result_limit: int | None,
    ) -> tuple[str, ...]:
        """One synthetic envelope per unresolved call, in original order."""
        interrupted = session.log.unresolved_call_ids
        calls_by_id = {call.id: call for call in active_calls}
        executions_by_call_id = {durable_call_id(item.call_id): item for item in durable_executions}
        durable_runtime = session.durable_runtime
        while session.log.unresolved_call_ids:
            call_id = session.log.unresolved_call_ids[0]
            call = calls_by_id.get(call_id)
            if call is None:
                raise RuntimeError("open ToolCycle call is missing from the active batch")
            if tool_executor is None:
                raise RuntimeError("open ToolCycle requires a ToolExecutor")
            outcome = tool_executor.error_outcome(
                call,
                code,
                message,
                result_limit=result_limit,
            )
            durable = executions_by_call_id.get(durable_call_id(call_id))
            if durable is None or durable_runtime is None:
                session.append_tool_result(call_id, outcome.envelope)
                continue

            durable = ToolCycleExecutor.reload_durable(session, durable)
            if durable.state in {
                ToolExecutionState.PREPARED,
                ToolExecutionState.AWAITING_APPROVAL,
            }:
                durable = durable_runtime.cancel_execution_before_handler(
                    durable, now=self._wall_now(session)
                )
            elif durable.state is ToolExecutionState.EXECUTING:
                durable = durable_runtime.record_handler_completed(
                    durable,
                    outcome,
                    now=self._wall_now(session),
                    disposition=ToolExecutionDisposition.UNKNOWN,
                )
            if durable.state not in {
                ToolExecutionState.CLOSED,
                ToolExecutionState.HANDLER_COMPLETED,
            }:
                session.append_tool_result(call_id, outcome.envelope)
                continue
            planned = session.log.plan_append_tool_result(call_id, outcome.envelope)
            durable_runtime.commit_tool_message(planned, durable, now=self._wall_now(session))
        return interrupted


class AgentRuntime:
    """Compatibility wrapper: plain chat is the same AgentLoop with no tools."""

    def __init__(
        self,
        provider: ModelProvider,
        model: ModelRef,
        context_builder,
        *,
        id_source: IdSource | None = None,
        clock: Clock | None = None,
        tool_executor: ToolExecutor | None = None,
        grant_provider=None,
    ) -> None:
        self._loop = AgentLoop(
            provider,
            model,
            context_builder,
            id_source=id_source,
            clock=clock,
            tool_executor=tool_executor,
            grant_provider=grant_provider,
        )

    @property
    def loop(self) -> AgentLoop:
        return self._loop

    def run_turn(
        self,
        session: Session,
        user_input: str,
        *,
        client_message_id: str | None = None,
        prepared: PreparedAgentRunRuntime | None = None,
        startup_error: str | None = None,
        agent_run_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        return self._loop.run_task(
            session,
            user_input,
            client_message_id=client_message_id,
            prepared=prepared,
            startup_error=startup_error,
            agent_run_id=agent_run_id,
        )
