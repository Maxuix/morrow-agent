"""Deterministic test doubles also useful for local demos."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable, Sequence
from datetime import UTC, datetime

from morrow.core.learning import CandidateDraftBatch
from morrow.core.learning_ports import LearningContext
from morrow.core.models import (
    AssistantMessage,
    FinishReason,
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


def make_run_policy(*, request_char_limit: int | None = None, **overrides):
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
        context_window_tokens=None,
        settings=LongHorizonPolicySettings.model_validate(settings_values, strict=True),
    )


def make_context_builder(request_char_limit: int | None = None, **policy_overrides):
    """Explicit canonical ContextBuilder wiring for tests and local demos."""
    from morrow.adapters.models.openai_compatible import estimate_request_chars
    from morrow.application.context import ContextBuilder

    return ContextBuilder(
        run_policy=make_run_policy(request_char_limit=request_char_limit, **policy_overrides),
        estimate_request_chars=estimate_request_chars,
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
    def __init__(self, responses: Iterable[object] = ()) -> None:
        self.responses = list(responses)
        self.stream_calls: list[list[Message]] = []
        self.stream_tools: list[tuple[ToolDefinition, ...]] = []
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
    ) -> AsyncIterator[ModelEvent]:
        del model
        self.stream_calls.append(list(messages))
        self.stream_tools.append(tuple(tools))
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
