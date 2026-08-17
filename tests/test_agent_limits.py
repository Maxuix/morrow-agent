"""RunPolicy counters, deadline ordering, and batch admission limits."""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel, ConfigDict

from morrow.core.models import (
    AssistantMessage,
    FunctionToolCall,
    ModelEvent,
    ModelRef,
    ToolMessage,
)
from morrow.runtime.agent import AgentLoop
from morrow.runtime.session import Session
from morrow.runtime.tools import (
    ToolExecutor,
    ToolRegistry,
    make_calculate_tool,
    make_tool,
)
from morrow.testing import ScriptedModelProvider, make_context_builder, make_run_policy

MODEL = ModelRef(provider_id="p", model_id="m")


def _call(call_id: str, value: float = 1) -> FunctionToolCall:
    return FunctionToolCall(
        id=call_id,
        name="calculate",
        arguments=json.dumps({"operation": "add", "values": [value, 1]}),
    )


def _executor() -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(make_calculate_tool())
    return ToolExecutor(registry.snapshot(), make_run_policy())


async def _collect(iterator):
    return [event async for event in iterator]


def _error_message(events) -> str:
    return next(event.payload["message"] for event in events if event.type == "error")


def _hold_last(*values: float):
    steps = list(values)
    index = -1

    def now() -> float:
        nonlocal index
        if index + 1 < len(steps):
            index += 1
        return steps[index]

    return now


@pytest.mark.asyncio
async def test_model_attempt_limit_precedes_tool_round_limit_when_both_are_exhausted():
    provider = ScriptedModelProvider(
        [
            AssistantMessage(tool_calls=(_call("c1"),)),
            AssistantMessage(tool_calls=(_call("c2", 2),)),
        ]
    )
    builder = make_context_builder(
        max_model_attempts=2,
        max_tool_rounds=2,
        max_tool_calls=2,
        max_tool_calls_per_cycle=1,
        model_retry_limit=0,
        loop_repeat_limit=2,
        loop_max_pattern_cycles=1,
    )
    session = Session(session_id="s")
    events = await _collect(
        AgentLoop(provider, MODEL, builder, tool_executor=_executor()).run_task(session, "go")
    )

    assert len(provider.stream_calls) == 2
    assert "模型调用次数" in _error_message(events)
    assert session.log.snapshot().records[-1].finish_reason.value == "error"


@pytest.mark.asyncio
async def test_per_cycle_call_limit_rejects_assistant_before_history_admission():
    provider = ScriptedModelProvider([AssistantMessage(tool_calls=(_call("c1"), _call("c2")))])
    builder = make_context_builder(max_tool_calls_per_cycle=1)
    session = Session(session_id="s")
    events = await _collect(
        AgentLoop(provider, MODEL, builder, tool_executor=_executor()).run_task(session, "go")
    )

    assert "单轮工具调用" in _error_message(events)
    assert [message.role for message in session.messages] == ["user"]


@pytest.mark.asyncio
async def test_total_tool_call_limit_accepts_batch_and_closes_all_with_budget_envelopes():
    provider = ScriptedModelProvider(
        [
            AssistantMessage(tool_calls=(_call("c1"),)),
            AssistantMessage(tool_calls=(_call("c2"), _call("c3"))),
        ]
    )
    builder = make_context_builder(
        max_tool_calls=2,
        max_tool_calls_per_cycle=2,
        max_tool_result_chars=62,
    )
    session = Session(session_id="s")
    events = await _collect(
        AgentLoop(provider, MODEL, builder, tool_executor=_executor()).run_task(session, "go")
    )

    results = [message for message in session.messages if isinstance(message, ToolMessage)]
    assert json.loads(results[0].content)["ok"] is True
    assert [json.loads(message.content)["error"]["code"] for message in results[1:]] == [
        "budget_exhausted",
        "budget_exhausted",
    ]
    assert all(len(message.content) <= 62 for message in results[1:])
    assert session.log.unresolved_call_ids == ()
    assert "工具调用总数" in _error_message(events)


@pytest.mark.asyncio
async def test_run_deadline_stops_before_first_provider_request():
    provider = ScriptedModelProvider(["never"])
    builder = make_context_builder(max_run_seconds=10.0, tool_timeout_seconds=5.0)
    session = Session(session_id="s")
    events = await _collect(
        AgentLoop(
            provider,
            MODEL,
            builder,
            tool_executor=_executor(),
            monotonic=_hold_last(0.0, 11.0),
        ).run_task(session, "go")
    )

    assert provider.stream_calls == []
    assert "总运行时间" in _error_message(events)


@pytest.mark.asyncio
async def test_run_deadline_cancels_a_hanging_provider_stream():
    class HangingProvider:
        def __init__(self):
            self.started = asyncio.Event()

        async def stream(self, model, messages, tools=()):
            del model, messages, tools
            self.started.set()
            await asyncio.Event().wait()
            yield

    provider = HangingProvider()
    builder = make_context_builder(max_run_seconds=0.01, tool_timeout_seconds=0.005)
    session = Session(session_id="s")

    events = await asyncio.wait_for(
        _collect(AgentLoop(provider, MODEL, builder).run_task(session, "go")),
        timeout=0.2,
    )

    assert provider.started.is_set()
    assert events[-2].payload["stop_code"] == "run_timeout"
    assert events[-1].payload["stop_code"] == "run_timeout"


@pytest.mark.asyncio
async def test_run_deadline_does_not_cancel_consumer_paused_on_text_delta():
    class StreamingThenHang:
        async def stream(self, model, messages, tools=()):
            del model, messages, tools
            yield ModelEvent(kind="text_delta", text="hello")
            await asyncio.Event().wait()
            yield

    session = Session(session_id="s")
    events = []

    async def consume():
        async for event in AgentLoop(
            StreamingThenHang(),
            MODEL,
            make_context_builder(max_run_seconds=0.05, tool_timeout_seconds=0.02),
        ).run_task(session, "go"):
            events.append(event)
            if event.type == "text.delta":
                await asyncio.sleep(0.08)

    await asyncio.wait_for(consume(), timeout=1.0)
    assert [event.type for event in events[:2]] == ["turn.started", "text.delta"]
    assert events[-2].payload["stop_code"] == "run_timeout"
    assert events[-1].payload["stop_code"] == "run_timeout"
    assert session.log.has_active_turn is False


@pytest.mark.asyncio
async def test_oversized_tool_call_cycle_is_rejected_before_history_admission():
    provider = ScriptedModelProvider([AssistantMessage(tool_calls=(_call("c1"),))])
    builder = make_context_builder(max_tool_cycle_chars=100)
    session = Session(session_id="s")
    events = await _collect(
        AgentLoop(provider, MODEL, builder, tool_executor=_executor()).run_task(session, "go")
    )

    assert "Cycle 预算" in _error_message(events)
    assert [message.role for message in session.messages] == ["user"]


@pytest.mark.asyncio
async def test_multi_call_cycle_allocates_the_same_bounded_result_limit_to_each_call():
    class RecordingExecutor(ToolExecutor):
        def __init__(self, tool_set, run_policy):
            super().__init__(tool_set, run_policy)
            self.result_limits = []

        async def execute(self, call, *, result_limit=None):
            self.result_limits.append(result_limit)
            return await super().execute(call, result_limit=result_limit)

    message = AssistantMessage(tool_calls=(_call("c1"), _call("c2")))
    provider = ScriptedModelProvider([message, AssistantMessage(content="done")])
    builder = make_context_builder(max_tool_cycle_chars=1000, max_tool_result_chars=1000)
    registry = ToolRegistry()
    registry.register(make_calculate_tool())
    executor = RecordingExecutor(registry.snapshot(), builder.run_policy)
    session = Session(session_id="s")

    events = await _collect(
        AgentLoop(provider, MODEL, builder, tool_executor=executor).run_task(session, "go")
    )

    worst_case_results = tuple(
        ToolMessage(
            tool_call_id=call.id,
            content="\\" * executor.result_limits[0],
        )
        for call in message.tool_calls
    )
    assert executor.result_limits == [executor.result_limits[0]] * 2
    assert (
        builder.estimate_request_chars((message, *worst_case_results), ())
        <= builder.run_policy.effective_cycle_limit
    )
    assert (
        builder.estimate_request_chars(
            (
                message,
                *tuple(
                    result.model_copy(update={"content": result.content + "\\"})
                    for result in worst_case_results
                ),
            ),
            (),
        )
        > builder.run_policy.effective_cycle_limit
    )
    assert events[-1].payload["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_quote_heavy_result_stays_within_the_canonical_cycle_wire_budget():
    class _Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

    async def quote_heavy(_: _Args) -> object:
        return {"payload": '\\"' * 1000}

    registry = ToolRegistry()
    registry.register(
        make_tool(name="quoted", description="quoted", arguments_model=_Args, handler=quote_heavy)
    )
    builder = make_context_builder(max_tool_cycle_chars=600, max_tool_result_chars=1000)
    executor = ToolExecutor(registry.snapshot(), builder.run_policy)
    assistant = AssistantMessage(
        tool_calls=(FunctionToolCall(id="c1", name="quoted", arguments="{}"),)
    )
    provider = ScriptedModelProvider([assistant, AssistantMessage(content="done")])
    session = Session(session_id="s")

    events = await _collect(
        AgentLoop(provider, MODEL, builder, tool_executor=executor).run_task(session, "go")
    )

    result = next(message for message in session.messages if isinstance(message, ToolMessage))
    assert (
        builder.estimate_request_chars((assistant, result), ())
        <= builder.run_policy.effective_cycle_limit
    )
    assert events[-1].payload["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_tool_timeout_becomes_one_bounded_result_and_loop_continues():
    class _Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

    async def slow(_: _Args) -> object:
        await asyncio.sleep(10)
        return "late"

    registry = ToolRegistry()
    registry.register(
        make_tool(name="slow", description="slow", arguments_model=_Args, handler=slow)
    )
    builder = make_context_builder(tool_timeout_seconds=0.01)
    executor = ToolExecutor(registry.snapshot(), builder.run_policy)
    provider = ScriptedModelProvider(
        [
            AssistantMessage(tool_calls=(FunctionToolCall(id="c1", name="slow", arguments="{}"),)),
            AssistantMessage(content="handled timeout"),
        ]
    )
    session = Session(session_id="s")
    events = await _collect(
        AgentLoop(provider, MODEL, builder, tool_executor=executor).run_task(session, "go")
    )

    result = next(message for message in session.messages if isinstance(message, ToolMessage))
    assert json.loads(result.content)["error"]["code"] == "timeout"
    assert events[-1].payload["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_effective_tool_timeout_is_capped_by_remaining_run_time(monkeypatch):
    recorded_timeouts = []
    original_wait_for = asyncio.wait_for

    async def recording_wait_for(awaitable, timeout):
        recorded_timeouts.append(timeout)
        return await original_wait_for(awaitable, timeout)

    monkeypatch.setattr("morrow.runtime.agent.asyncio.wait_for", recording_wait_for)
    provider = ScriptedModelProvider(
        [AssistantMessage(tool_calls=(_call("c1"),)), AssistantMessage(content="done")]
    )
    builder = make_context_builder(max_run_seconds=10.0, tool_timeout_seconds=5.0)

    events = await _collect(
        AgentLoop(
            provider,
            MODEL,
            builder,
            tool_executor=_executor(),
            monotonic=_hold_last(0.0, 0.0, 8.0),
        ).run_task(Session(session_id="s"), "go")
    )

    assert recorded_timeouts == [2.0]
    assert events[-1].payload["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_deadline_between_tools_preserves_result_and_budget_closes_unstarted_call():
    provider = ScriptedModelProvider([AssistantMessage(tool_calls=(_call("c1"), _call("c2")))])
    builder = make_context_builder(max_run_seconds=10.0, tool_timeout_seconds=5.0)
    session = Session(session_id="s")
    events = await _collect(
        AgentLoop(
            provider,
            MODEL,
            builder,
            tool_executor=_executor(),
            monotonic=_hold_last(0.0, 0.0, 0.0, 0.0, 0.0, 11.0),
        ).run_task(session, "go")
    )

    results = [message for message in session.messages if isinstance(message, ToolMessage)]
    assert json.loads(results[0].content)["ok"] is True
    assert json.loads(results[1].content)["error"]["code"] == "budget_exhausted"
    statuses = [event.payload for event in events if event.type == "tool.status"]
    assert [status["status"] for status in statuses] == ["running", "succeeded", "skipped"]
    assert statuses[-1]["error_code"] == "budget_exhausted"
    assert events[-2].payload["stop_code"] == "run_timeout"
