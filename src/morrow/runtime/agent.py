"""Single bounded chat state machine: one AgentLoop, one history write path."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

from morrow.application.context import ContextBudgetError
from morrow.core.capabilities import ToolRunContext
from morrow.core.events import completion_payload, make_event
from morrow.core.models import (
    AgentEvent,
    AgentStopCode,
    AssistantMessage,
    FinishReason,
    Message,
    ModelErrorCode,
    ModelEvent,
    ModelFinishReason,
    ModelRef,
    ProtocolModel,
    ToolDefinition,
    ToolMessage,
    UserMessage,
    sanitize_text,
)
from morrow.core.ports import Clock, IdSource, ModelProvider
from morrow.runtime.conversation import ConversationLogError
from morrow.runtime.ids import RandomIdSource
from morrow.runtime.session import Session
from morrow.runtime.tools import (
    MIN_ERROR_ENVELOPE_CHARS,
    ToolErrorCode,
    ToolExecutionOutcome,
    ToolExecutor,
)

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
                    )
                yield model_event
        except asyncio.CancelledError:
            raise
        except Exception:
            self._outcome = ModelCallOutcome(
                error_code=ModelErrorCode.INTERNAL,
                error_message="模型服务发生未预期错误",
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
            )
        if reason == ModelFinishReason.STOP:
            if message is None or not (message.content or "").strip() or bool(message.tool_calls):
                return ModelCallOutcome(
                    finish_reason=reason,
                    error_code=ModelErrorCode.INVALID_RESPONSE,
                    error_message="模型没有返回可见文本",
                )
        elif message is None or not message.tool_calls:
            return ModelCallOutcome(
                finish_reason=reason,
                error_code=ModelErrorCode.INVALID_RESPONSE,
                error_message="模型没有返回工具调用",
            )
        return ModelCallOutcome(message=message, finish_reason=reason)


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
        monotonic=None,
    ) -> None:
        self.runner = ModelCallRunner(provider, model)
        self.context_builder = context_builder
        self.id_source = id_source or RandomIdSource()
        self.clock = clock
        self.run_policy = context_builder.run_policy
        self.tool_executor = tool_executor
        self.monotonic = monotonic or time.monotonic

    def _id(self, prefix: str) -> str:
        return self.id_source.new_id(prefix)

    async def run_task(self, session: Session, user_input: str) -> AsyncIterator[AgentEvent]:
        turn_id = self._id("turn")
        run_context = ToolRunContext(run_id=turn_id, session_id=session.session_id)
        sequence = 0
        visible = ""
        policy = self.run_policy
        deadline = self.monotonic() + policy.max_run_seconds
        model_attempts = 0
        tool_rounds = 0
        tool_calls = 0
        retry_count = 0
        cycle_signatures: list[tuple] = []
        active_calls = ()
        active_running_id: str | None = None
        active_result_limit: int | None = None
        final_committed = False
        facts_retained = False

        def retain_facts(finish_reason: str = "unknown") -> None:
            nonlocal facts_retained
            if not facts_retained:
                session.retain_run_facts(run_context, finish_reason=finish_reason)
                facts_retained = True

        def event(event_type: str, payload: dict) -> AgentEvent:
            nonlocal sequence
            sequence += 1
            return make_event(
                event_type=event_type,
                event_id=self._id("evt"),
                session_id=session.session_id,
                turn_id=turn_id,
                sequence=sequence,
                payload=payload,
                timestamp=self.clock.now() if self.clock else None,
            )

        def fatal(message: str, stop_code: AgentStopCode) -> tuple[AgentEvent, AgentEvent]:
            return (
                event(
                    "error",
                    {"message": sanitize_text(message), "stop_code": stop_code.value},
                ),
                event(
                    "turn.completed",
                    completion_payload(FinishReason.ERROR, visible, stop_code=stop_code),
                ),
            )

        def tool_status(
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
            return event("tool.status", payload)

        def terminal_error(
            message: str,
            stop_code: AgentStopCode,
            *,
            interrupted: tuple[str, ...] = (),
        ) -> tuple[AgentEvent, AgentEvent]:
            session.log.finish_turn(FinishReason.ERROR, interrupted_call_ids=interrupted)
            retain_facts(FinishReason.ERROR.value)
            return fatal(message, stop_code)

        def synthetic_statuses(
            unresolved: tuple[str, ...], *, code: ToolErrorCode, running_status: str
        ) -> list[AgentEvent]:
            statuses = []
            by_id = {call.id: (index, call) for index, call in enumerate(active_calls, start=1)}
            for call_id in unresolved:
                ordinal, call = by_id[call_id]
                status = running_status if call_id == active_running_id else "skipped"
                statuses.append(
                    tool_status(
                        call,
                        status,
                        ordinal,
                        len(active_calls),
                        error_code=code,
                    )
                )
            return statuses

        yield event("turn.started", {})
        try:
            session.log.begin_turn(UserMessage(content=user_input))
            session.dirty = True
            tools = self.tool_executor.definitions if self.tool_executor else ()

            while True:
                if _pending_cancellation():
                    _consume_cancellation_request()
                    raise asyncio.CancelledError
                if self.monotonic() >= deadline:
                    for item in terminal_error("任务超过总运行时间", AgentStopCode.RUN_TIMEOUT):
                        yield item
                    return
                if model_attempts >= policy.max_model_attempts:
                    for item in terminal_error(
                        "模型调用次数已达上限", AgentStopCode.MODEL_CALL_LIMIT
                    ):
                        yield item
                    return
                if tool_rounds >= policy.max_tool_rounds:
                    for item in terminal_error("工具轮次已达上限", AgentStopCode.TOOL_CALL_LIMIT):
                        yield item
                    return
                try:
                    context = self.context_builder.build(session, tools=tools)
                    call_messages = list(context.messages)
                    self.context_builder.validate_request(call_messages, tools)
                except ContextBudgetError as exc:
                    for item in terminal_error(str(exc), AgentStopCode.CONTEXT_BUDGET):
                        yield item
                    return

                model_attempts += 1
                remaining_model_time = deadline - self.monotonic()
                if remaining_model_time <= 0:
                    for item in terminal_error("任务超过总运行时间", AgentStopCode.RUN_TIMEOUT):
                        yield item
                    return
                stream = self.runner.attempt(call_messages, tools)
                try:
                    while True:
                        try:
                            async with asyncio.timeout(remaining_model_time):
                                model_event = await anext(stream)
                        except StopAsyncIteration:
                            break
                        except TimeoutError:
                            for item in terminal_error(
                                "任务超过总运行时间", AgentStopCode.RUN_TIMEOUT
                            ):
                                yield item
                            return
                        if model_event.kind == "text_delta" and model_event.text:
                            visible += model_event.text
                            yield event("text.delta", {"text": model_event.text})
                        remaining_model_time = deadline - self.monotonic()
                        if remaining_model_time <= 0:
                            for item in terminal_error(
                                "任务超过总运行时间", AgentStopCode.RUN_TIMEOUT
                            ):
                                yield item
                            return
                finally:
                    close = getattr(stream, "aclose", None)
                    if close is not None:
                        await close()
                if _pending_cancellation():
                    _consume_cancellation_request()
                    raise asyncio.CancelledError
                outcome = self.runner.outcome
                if outcome.error_code is not None:
                    if (
                        not self.runner.made_progress
                        and retry_count < policy.model_retry_limit
                        and outcome.error_code in TRANSIENT_MODEL_ERRORS
                    ):
                        retry_count += 1
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
                retry_count = 0
                message = outcome.message
                is_final_text = (
                    outcome.finish_reason == ModelFinishReason.STOP
                    and message is not None
                    and not message.tool_calls
                )
                if is_final_text:
                    try:
                        session.log.append_assistant(message)
                    except ConversationLogError:
                        for item in terminal_error(
                            "模型响应未正常结束", AgentStopCode.INVALID_RESPONSE
                        ):
                            yield item
                        return
                    final_committed = True
                    current = asyncio.current_task()
                    if current is not None:
                        while current.cancelling():
                            current.uncancel()
                    session.log.finish_turn(FinishReason.STOP)
                    retain_facts(FinishReason.STOP.value)
                    yield event(
                        "turn.completed",
                        completion_payload(FinishReason.STOP, visible),
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
                per_call_result_limit = self._cycle_result_limit(message)
                if per_call_result_limit is None:
                    for item in terminal_error(
                        "模型工具调用输出超过 Cycle 预算",
                        AgentStopCode.MODEL_OUTPUT_LIMIT,
                    ):
                        yield item
                    return

                try:
                    session.log.append_assistant(message)
                except ConversationLogError:
                    for item in terminal_error(
                        "模型响应未正常结束", AgentStopCode.INVALID_RESPONSE
                    ):
                        yield item
                    return
                active_calls = calls
                active_running_id = None
                active_result_limit = per_call_result_limit
                if tool_calls + len(calls) > policy.max_tool_calls:
                    interrupted = tuple(call.id for call in calls)
                    for index, call in enumerate(calls, start=1):
                        outcome = self.tool_executor.error_outcome(
                            call,
                            ToolErrorCode.BUDGET_EXHAUSTED,
                            "工具调用总数已达上限",
                            result_limit=per_call_result_limit,
                        )
                        session.log.append_tool_result(call.id, outcome.envelope)
                        yield tool_status(
                            call,
                            "skipped",
                            index,
                            len(calls),
                            error_code=ToolErrorCode.BUDGET_EXHAUSTED,
                        )
                    tool_calls += len(calls)
                    tool_rounds += 1
                    for item in terminal_error(
                        "工具调用总数已达上限",
                        AgentStopCode.TOOL_CALL_LIMIT,
                        interrupted=interrupted,
                    ):
                        yield item
                    return

                tool_calls += len(calls)
                cycle_outcomes: list[ToolExecutionOutcome] = []
                for index, call in enumerate(calls, start=1):
                    if _pending_cancellation():
                        _consume_cancellation_request()
                        raise asyncio.CancelledError
                    now = self.monotonic()
                    if now >= deadline:
                        unresolved = session.log.unresolved_call_ids
                        interrupted = self._close_unresolved(
                            session,
                            active_calls,
                            ToolErrorCode.BUDGET_EXHAUSTED,
                            "任务总运行时间已耗尽",
                            result_limit=active_result_limit,
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
                    active_running_id = call.id
                    yield tool_status(call, "running", index, len(calls))
                    try:
                        execute_with_context = getattr(
                            self.tool_executor, "execute_with_context", None
                        )
                        if execute_with_context is not None:
                            execution = execute_with_context(
                                call,
                                result_limit=per_call_result_limit,
                                run_context=run_context,
                                ordinal=index,
                                total=len(calls),
                            )
                        else:
                            execution = self.tool_executor.execute(
                                call, result_limit=per_call_result_limit
                            )
                        result = await asyncio.wait_for(
                            execution,
                            timeout=min(policy.tool_timeout_seconds, deadline - now),
                        )
                    except TimeoutError:
                        result = self.tool_executor.error_outcome(
                            call,
                            ToolErrorCode.TIMEOUT,
                            "工具执行超时",
                            result_limit=per_call_result_limit,
                        )
                    session.log.append_tool_result(call.id, result.envelope)
                    cycle_outcomes.append(result)
                    run_context.note_tool_outcome(ok=result.ok, error_code=result.error_code)
                    active_running_id = None
                    yield tool_status(
                        call,
                        "succeeded" if result.ok else "failed",
                        index,
                        len(calls),
                        error_code=result.error_code,
                        truncated=result.truncated,
                    )
                tool_rounds += 1
                active_calls = ()
                active_result_limit = None
                cycle_signatures.append(_cycle_signature(message, cycle_outcomes))
                if policy.loop_detection_enabled and _has_repeated_suffix(
                    cycle_signatures,
                    policy.loop_repeat_limit,
                    policy.loop_max_pattern_cycles,
                ):
                    for item in terminal_error("检测到重复工具循环", AgentStopCode.LOOP_DETECTED):
                        yield item
                    return
        except asyncio.CancelledError:
            if final_committed:
                return
            _consume_cancellation_request()
            unresolved = session.log.unresolved_call_ids
            interrupted = self._close_unresolved(
                session,
                active_calls,
                ToolErrorCode.CANCELLED,
                "任务已取消，工具调用未完成",
                result_limit=active_result_limit,
            )
            for status_event in synthetic_statuses(
                unresolved,
                code=ToolErrorCode.CANCELLED,
                running_status="cancelled",
            ):
                yield status_event
            if session.log.has_active_turn:
                try:
                    session.log.finish_turn(
                        FinishReason.CANCELLED, interrupted_call_ids=interrupted
                    )
                except ConversationLogError:
                    pass
            retain_facts(FinishReason.CANCELLED.value)
            yield event(
                "turn.completed",
                completion_payload(FinishReason.CANCELLED, visible),
            )
            return
        except Exception:
            unresolved = session.log.unresolved_call_ids
            interrupted = self._close_unresolved(
                session,
                active_calls,
                ToolErrorCode.INTERNAL,
                "内部错误，工具调用未完成",
                result_limit=active_result_limit,
            )
            for status_event in synthetic_statuses(
                unresolved,
                code=ToolErrorCode.INTERNAL,
                running_status="failed",
            ):
                yield status_event
            if session.log.has_active_turn:
                try:
                    session.log.finish_turn(FinishReason.ERROR, interrupted_call_ids=interrupted)
                except ConversationLogError:
                    pass
            retain_facts(FinishReason.ERROR.value)
            for item in fatal("模型服务发生未预期错误", AgentStopCode.INTERNAL):
                yield item
            return
        finally:
            retain_facts()
            if session.log.has_active_turn:
                try:
                    interrupted = self._close_unresolved(
                        session,
                        active_calls,
                        ToolErrorCode.CANCELLED,
                        "任务已取消，工具调用未完成",
                        result_limit=active_result_limit,
                    )
                    session.log.finish_turn(
                        FinishReason.CANCELLED, interrupted_call_ids=interrupted
                    )
                except Exception:
                    pass

    def _cycle_result_limit(self, message: AssistantMessage) -> int | None:
        """Largest equal raw envelope cap safe under worst-case JSON escaping."""
        calls = message.tool_calls
        high = self.run_policy.effective_result_limit
        low = MIN_ERROR_ENVELOPE_CHARS

        def estimated(limit: int) -> int:
            worst_case = tuple(
                ToolMessage(tool_call_id=call.id, content="\\" * limit) for call in calls
            )
            return self.context_builder.estimate_request_chars((message, *worst_case), ())

        if high < low or estimated(low) > self.run_policy.effective_cycle_limit:
            return None
        accepted = low
        while low <= high:
            middle = (low + high) // 2
            if estimated(middle) <= self.run_policy.effective_cycle_limit:
                accepted = middle
                low = middle + 1
            else:
                high = middle - 1
        return accepted

    def _close_unresolved(
        self,
        session: Session,
        active_calls,
        code: ToolErrorCode,
        message: str,
        *,
        result_limit: int | None,
    ) -> tuple[str, ...]:
        """One synthetic envelope per unresolved call, in original order."""
        interrupted = session.log.unresolved_call_ids
        calls_by_id = {call.id: call for call in active_calls}
        while session.log.unresolved_call_ids:
            call_id = session.log.unresolved_call_ids[0]
            call = calls_by_id[call_id]
            if self.tool_executor is None:
                raise RuntimeError("open ToolCycle requires a ToolExecutor")
            outcome = self.tool_executor.error_outcome(
                call,
                code,
                message,
                result_limit=result_limit,
            )
            session.log.append_tool_result(call_id, outcome.envelope)
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
    ) -> None:
        self._loop = AgentLoop(
            provider,
            model,
            context_builder,
            id_source=id_source,
            clock=clock,
            tool_executor=tool_executor,
        )

    @property
    def loop(self) -> AgentLoop:
        return self._loop

    def run_turn(self, session: Session, user_input: str) -> AsyncIterator[AgentEvent]:
        return self._loop.run_task(session, user_input)
