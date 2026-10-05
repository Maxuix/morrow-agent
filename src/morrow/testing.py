"""Deterministic test doubles also useful for local demos."""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from morrow.core.activity import ThinkingCapability
from morrow.core.capabilities import (
    OperationIntent,
    OperationKind,
    ToolCallContext,
    ToolHandlerOutcome,
)
from morrow.core.contracts import (
    ExecutionSegmentIdentity,
    TimelineEntryIdentity,
)
from morrow.core.execution import tool_declaration
from morrow.core.learning import CandidateDraftBatch
from morrow.core.learning_ports import LearningContext
from morrow.core.models import (
    AssistantMessage,
    FinishReason,
    GenerationOptions,
    Message,
    ModelErrorCode,
    ModelEvent,
    ModelFailure,
    ModelFailureOrigin,
    ModelFinishReason,
    ModelRef,
    ToolDefinition,
    UserMessage,
)
from morrow.core.preference_review import PreferenceReviewContext, PreferenceReviewOutput
from morrow.runtime.tools import RegisteredTool, ToolErrorCode, ToolExecutionError, make_tool


def make_run_policy(
    *,
    request_char_limit: int | None = None,
    context_window_tokens: int | None = None,
    **overrides,
):
    """Resolve an injected test policy without introducing production defaults."""
    from morrow.runtime.policy import (
        AgentPolicy,
        LongHorizonPolicySettings,
        load_runtime_policy,
    )

    runtime = load_runtime_policy()
    values = runtime.agent_run.model_dump()
    settings_values = runtime.long_horizon.model_dump()
    if request_char_limit is not None:
        values["requested_context_chars"] = request_char_limit
        values["unknown_model_fallback_chars"] = request_char_limit
    for name, value in overrides.items():
        if name in AgentPolicy.model_fields:
            values[name] = value
        elif name in LongHorizonPolicySettings.model_fields:
            settings_values[name] = value
        else:
            raise TypeError(f"unknown v2 run-policy override: {name}")
    policy = AgentPolicy.model_validate(values, strict=True)
    return policy.resolve(
        ModelRef(provider_id="test", model_id="test"),
        tool_protocol="openai_function",
        multiple_tool_calls=True,
        context_window_tokens=context_window_tokens,
        settings=LongHorizonPolicySettings.model_validate(settings_values, strict=True),
    )


def make_context_builder(
    request_char_limit: int | None = None,
    *,
    model: ModelRef | None = None,
    estimate_request_tokens=None,
    attachment_resolver=None,
    payload_char_limit: int | None = None,
    **policy_overrides,
):
    """Explicit canonical ContextBuilder wiring for tests and local demos."""
    from morrow.adapters.models.openai_compatible import (
        estimate_request_chars,
        make_request_token_estimator,
    )
    from morrow.application.context import ContextBuilder

    selected = model or ModelRef(provider_id="test", model_id="test")
    return ContextBuilder(
        run_policy=make_run_policy(request_char_limit=request_char_limit, **policy_overrides),
        estimate_request_chars=estimate_request_chars,
        estimate_request_tokens=estimate_request_tokens or make_request_token_estimator(selected),
        attachment_resolver=attachment_resolver,
        payload_char_limit=payload_char_limit,
    )


class FixedClock:
    def __init__(self, value: datetime | None = None) -> None:
        self.value = value or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class FixedIdSource:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def new_id(self, prefix: str) -> str:
        self.counts[prefix] = self.counts.get(prefix, 0) + 1
        return f"{prefix}_{self.counts[prefix]}"


class FixedLearningClock(FixedClock):
    """Named clock fixture for deterministic Learning tests."""


class FixedLearningIdSource(FixedIdSource):
    """Named ID fixture that shares the normal deterministic prefix contract."""


class ScriptedLearningReviewer:
    """Test-only no-tool Reviewer; never imported by production composition."""

    def __init__(self, responses: Sequence[CandidateDraftBatch | BaseException] = ()) -> None:
        self.responses = list(responses)
        self.calls: list[LearningContext] = []
        self._index = 0

    async def review(
        self,
        context: LearningContext,
        *,
        model: ModelRef,
        timeout_seconds: float,
    ) -> CandidateDraftBatch:
        del model, timeout_seconds
        self.calls.append(context)
        if not self.responses:
            return CandidateDraftBatch()
        response = self.responses[min(self._index, len(self.responses) - 1)]
        self._index += 1
        if isinstance(response, BaseException):
            raise response
        return response


class ScriptedPreferenceReviewer:
    """Test-only deterministic no-tool Preference Reviewer."""

    def __init__(self, responses: Sequence[PreferenceReviewOutput | BaseException] = ()) -> None:
        self.responses = list(responses)
        self.calls: list[PreferenceReviewContext] = []
        self._index = 0

    async def review(
        self,
        context: PreferenceReviewContext,
        *,
        model: ModelRef,
        timeout_seconds: float,
    ) -> PreferenceReviewOutput:
        del model, timeout_seconds
        self.calls.append(context)
        if not self.responses:
            return PreferenceReviewOutput()
        response = self.responses[min(self._index, len(self.responses) - 1)]
        self._index += 1
        if isinstance(response, BaseException):
            raise response
        return response


def seed_user_turn(session, content, *, assistant=None, finish=None) -> None:
    """Seed one legal log turn without a model call; mirrors AgentLoop writes.

    Accepting a real User marks the session dirty, exactly like production.
    """
    session.log.begin_turn(UserMessage(content=content))
    if assistant is not None:
        session.log.append_assistant(AssistantMessage(content=assistant))
    terminal = finish or (FinishReason.STOP if assistant is not None else FinishReason.ERROR)
    session.log.finish_turn(terminal)
    session.dirty = True


class ScriptedModelProvider:
    # Scripted providers stream whatever reasoning fragments their script
    # yields (ModelEvent instances), so the four-state mapping is testable.
    reasoning_visibility: ThinkingCapability = "visible_text"

    def __init__(self, responses: Iterable[object] = ()) -> None:
        self.responses = list(responses)
        self.stream_calls: list[list[Message]] = []
        self.stream_tools: list[tuple[ToolDefinition, ...]] = []
        self.stream_generations: list[GenerationOptions | None] = []
        self.complete_calls: list[list[Message]] = []
        self._index = 0

    def _next(self) -> object:
        if not self.responses:
            return "好的。"
        item = self.responses[min(self._index, len(self.responses) - 1)]
        self._index += 1
        return item

    async def stream(
        self,
        model: ModelRef,
        messages: list[Message],
        tools: tuple[ToolDefinition, ...] = (),
        generation: GenerationOptions | None = None,
    ) -> AsyncIterator[ModelEvent]:
        del model
        self.stream_calls.append(list(messages))
        self.stream_tools.append(tuple(tools))
        self.stream_generations.append(generation)
        response = self._next()
        if isinstance(response, BaseException):
            yield ModelEvent(
                kind="error",
                failure=ModelFailure(
                    code=ModelErrorCode.NETWORK,
                    origin=ModelFailureOrigin.PROVIDER,
                    retryable=True,
                    message="scripted failure",
                ),
            )
            return
        if response == "cancel":
            await asyncio.sleep(10)
            return
        if isinstance(response, ModelEvent):
            # Scripted passthrough for reasoning fragments and other events.
            yield response
            return
        if isinstance(response, AssistantMessage):
            reason = ModelFinishReason.TOOL_CALLS if response.tool_calls else ModelFinishReason.STOP
            if response.content:
                yield ModelEvent(kind="text_delta", text=response.content)
            yield ModelEvent(kind="completed", finish_reason=reason, message=response)
            return
        if isinstance(response, (list, tuple)):
            chunks = response
        else:
            chunks = [str(response)]
        assembled = ""
        for chunk in chunks:
            await asyncio.sleep(0)
            if isinstance(chunk, ModelEvent):
                yield chunk
                continue
            assembled += str(chunk)
            yield ModelEvent(kind="text_delta", text=str(chunk))
        yield ModelEvent(
            kind="completed",
            finish_reason=ModelFinishReason.STOP,
            message=AssistantMessage(content=assembled) if assembled else None,
        )

    async def complete(self, model: ModelRef, messages: list[Message]) -> str:
        del model
        self.complete_calls.append(list(messages))
        response = self._next()
        if isinstance(response, BaseException):
            raise response
        if response == "cancel":
            await asyncio.sleep(10)
        return str(response)


class FakeTimelineSink:
    """Test double for the frozen TimelineSinkPort (P01 contract 1.0.0).

    Records identities in call order without assigning positions; lanes A/B
    develop their commit seams against this until lane C's real index lands.
    """

    def __init__(self) -> None:
        self.recorded: list[TimelineEntryIdentity] = []

    def record(self, identity: TimelineEntryIdentity) -> None:
        self.recorded.append(identity)


class FakePostCommitNotifier:
    """Test double for the frozen PostCommitNotifierPort (P01 contract 1.0.0)."""

    def __init__(self) -> None:
        self.notified: list[TimelineEntryIdentity] = []

    def notify_after_commit(self, entry: TimelineEntryIdentity) -> None:
        self.notified.append(entry)


class FakeSegmentDirectory:
    """Test double for the frozen SegmentDirectoryPort (P01 contract 1.0.0)."""

    def __init__(self, segments: Iterable[ExecutionSegmentIdentity] = ()) -> None:
        self._segments: list[ExecutionSegmentIdentity] = list(segments)

    def add(self, segment: ExecutionSegmentIdentity) -> None:
        self._segments.append(segment)

    def current_segment(self, node_run_id: str) -> ExecutionSegmentIdentity | None:
        active = [
            segment
            for segment in self._segments
            if segment.node_run_id == node_run_id and segment.status == "active"
        ]
        return active[-1] if active else None

    def segments(
        self, workflow_run_id: str, node_run_id: str
    ) -> tuple[ExecutionSegmentIdentity, ...]:
        matched = [
            segment
            for segment in self._segments
            if segment.workflow_run_id == workflow_run_id and segment.node_run_id == node_run_id
        ]
        return tuple(sorted(matched, key=lambda segment: segment.ordinal))


class LookupRecordArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: Literal["plans", "regions"]
    key: str

    @field_validator("key")
    @classmethod
    def non_empty_key(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("key must not be empty")
        return value


def make_lookup_record_tool(records: Mapping[tuple[str, str], object]) -> RegisteredTool:
    """Read-only lookup over injected in-memory data; no filesystem access."""
    data = MappingProxyType(dict(records))

    async def handler(arguments: LookupRecordArguments) -> object:
        value = data.get((arguments.dataset, arguments.key))
        if value is None:
            raise ToolExecutionError(
                ToolErrorCode.NOT_FOUND,
                f"记录不存在: {arguments.dataset}/{arguments.key}",
            )
        return ToolHandlerOutcome(payload=value)

    def intent(_: LookupRecordArguments, __: ToolCallContext) -> OperationIntent:
        return OperationIntent(
            kind=OperationKind.INTERNAL_READ,
            preview_summary=("读取注入的内存数据",),
        )

    return make_tool(
        name="lookup_record",
        description="查询注入的内存数据集（plans 或 regions）中的一条记录。",
        arguments_model=LookupRecordArguments,
        handler=handler,
        intent_resolver=intent,
        recovery_declaration=tool_declaration("lookup_record"),
    )


class CalculateArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["add", "subtract", "multiply", "divide"]
    values: tuple[float, ...]

    @field_validator("values")
    @classmethod
    def bounded_finite_values(cls, values: tuple[float, ...]) -> tuple[float, ...]:
        if not 2 <= len(values) <= 32:
            raise ValueError("values must contain between 2 and 32 numbers")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("values must be finite numbers")
        return values


def make_calculate_tool() -> RegisteredTool:
    """Deterministic left-to-right arithmetic over validated finite numbers."""

    async def handler(arguments: CalculateArguments) -> object:
        result = arguments.values[0]
        for value in arguments.values[1:]:
            if arguments.operation == "add":
                result += value
            elif arguments.operation == "subtract":
                result -= value
            elif arguments.operation == "multiply":
                result *= value
            elif value == 0:
                raise ToolExecutionError(ToolErrorCode.DIVISION_BY_ZERO, "除数为零")
            else:
                result /= value
            if not math.isfinite(result):
                raise ToolExecutionError(
                    ToolErrorCode.EXECUTION_FAILED,
                    "计算结果不是有限数字",
                )
        return ToolHandlerOutcome(payload={"operation": arguments.operation, "value": result})

    def intent(_: CalculateArguments, __: ToolCallContext) -> OperationIntent:
        return OperationIntent(
            kind=OperationKind.INTERNAL_READ,
            preview_summary=("执行本地有限数字计算",),
        )

    return make_tool(
        name="calculate",
        description="对 2 到 32 个有限数字做有序四则运算（add/subtract/multiply/divide）。",
        arguments_model=CalculateArguments,
        handler=handler,
        intent_resolver=intent,
        recovery_declaration=tool_declaration("calculate"),
    )
