"""Host-supplied run deadlines use monotonic budgets and preserve terminal facts."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from morrow.core.models import AssistantMessage, FunctionToolCall, ModelEvent, ModelRef
from morrow.runtime.agent import AgentLoop
from morrow.runtime.deadline import RunDeadline, RunDeadlineExceeded
from morrow.runtime.session import Session
from morrow.runtime.tools import ToolExecutor, ToolRegistry, make_tool
from morrow.testing import ScriptedModelProvider, make_context_builder

MODEL = ModelRef(provider_id="p", model_id="m")


class FakeMonotonic:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_deadline_reserves_terminal_time_and_rejects_invalid_values():
    clock = FakeMonotonic()
    deadline = RunDeadline.from_seconds(100, clock=clock)
    assert deadline.remaining_seconds() == 90
    clock.advance(89)
    assert deadline.require_work() == 1
    clock.advance(1)
    with pytest.raises(RunDeadlineExceeded):
        deadline.require_work()
    for invalid in (0, -1, float("inf"), float("nan"), True):
        with pytest.raises(ValueError):
            RunDeadline.from_seconds(invalid, clock=clock)


@pytest.mark.asyncio
async def test_continuous_chunks_still_reach_run_deadline():
    clock = FakeMonotonic()

    class BusyProvider:
        calls = 0
        messages = None

        async def stream(self, model, messages, tools=(), *, generation=None):
            del model, tools, generation
            self.calls += 1
            self.messages = messages
            while True:
                clock.advance(1)
                yield ModelEvent(kind="activity", activity="reasoning")

    provider = BusyProvider()
    session = Session(session_id="s")
    events = [
        item
        async for item in AgentLoop(
            provider, MODEL, make_context_builder(), monotonic_clock=clock
        ).run_task(session, "work", run_timeout_seconds=10)
    ]

    assert provider.calls == 1
    assert provider.messages is not None
    assert "优先验证和保存已有成果" in provider.messages[-1].content
    activity = [
        item.payload
        for item in events
        if item.type == "status.changed" and item.payload.get("status") == "model_activity"
    ]
    assert activity == [
        {
            "status": "model_activity",
            "activity": "reasoning",
            "chunk_count": 1,
            "elapsed_seconds": 1.0,
            "attempt_ordinal": 1,
        }
    ]
    assert events[-1].payload["finish_reason"] == "error"
    assert events[-1].payload["stop_code"] == "run_timeout"
    assert session.log.has_active_turn is False


@pytest.mark.asyncio
async def test_retry_cannot_start_after_run_deadline():
    clock = FakeMonotonic()
    provider = ScriptedModelProvider([ConnectionError("temporary")])

    async def consume_retry_budget(_delay: float) -> None:
        clock.advance(10)

    events = [
        item
        async for item in AgentLoop(
            provider,
            MODEL,
            make_context_builder(),
            retry_sleep=consume_retry_budget,
            monotonic_clock=clock,
        ).run_task(Session(session_id="s"), "work", run_timeout_seconds=10)
    ]

    assert len(provider.stream_calls) == 1
    assert events[-1].payload["stop_code"] == "run_timeout"


@pytest.mark.asyncio
async def test_tool_result_is_kept_but_no_new_request_starts_after_deadline():
    class EmptyArguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

    clock = FakeMonotonic()

    async def finish_tool(_arguments: EmptyArguments) -> str:
        clock.advance(10)
        return "saved"

    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="finish_tool",
            description="finish work",
            arguments_model=EmptyArguments,
            handler=finish_tool,
        )
    )
    builder = make_context_builder()
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(FunctionToolCall(id="call_1", name="finish_tool", arguments="{}"),)
            ),
            AssistantMessage(content="should not be requested"),
        ]
    )
    session = Session(session_id="s")
    events = [
        item
        async for item in AgentLoop(
            provider,
            MODEL,
            builder,
            tool_executor=ToolExecutor(registry.snapshot(), builder.run_policy),
            monotonic_clock=clock,
        ).run_task(session, "work", run_timeout_seconds=10)
    ]

    assert len(provider.stream_calls) == 1
    assert events[-1].payload["stop_code"] == "run_timeout"
    assert any(message.role == "tool" for message in session.log.messages_view())
