"""Current per-operation AgentRun limits."""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel, ConfigDict

from morrow.core.models import AssistantMessage, FunctionToolCall, ModelRef, ToolMessage
from morrow.runtime.agent import AgentLoop
from morrow.runtime.session import Session
from morrow.runtime.tools import ToolExecutor, ToolRegistry, make_calculate_tool, make_tool
from morrow.testing import ScriptedModelProvider, make_context_builder, make_run_policy

MODEL = ModelRef(provider_id="p", model_id="m")


def _call(call_id: str, value: float = 1) -> FunctionToolCall:
    return FunctionToolCall(
        id=call_id,
        name="calculate",
        arguments=json.dumps({"operation": "add", "values": [value, 1]}),
    )


def _executor(policy=None) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(make_calculate_tool())
    return ToolExecutor(registry.snapshot(), policy or make_run_policy())


async def _collect(iterator):
    return [event async for event in iterator]


@pytest.mark.asyncio
async def test_multi_call_cycle_uses_the_same_per_call_result_limit():
    class RecordingExecutor(ToolExecutor):
        def __init__(self, tool_set, run_policy):
            super().__init__(tool_set, run_policy)
            self.result_limits = []

        async def execute(self, call, *, result_limit=None):
            self.result_limits.append(result_limit)
            return await super().execute(call, result_limit=result_limit)

    message = AssistantMessage(tool_calls=(_call("c1"), _call("c2")))
    provider = ScriptedModelProvider([message, AssistantMessage(content="done")])
    builder = make_context_builder(max_tool_result_chars=1000)
    registry = ToolRegistry()
    registry.register(make_calculate_tool())
    executor = RecordingExecutor(registry.snapshot(), builder.run_policy)

    events = await _collect(
        AgentLoop(provider, MODEL, builder, tool_executor=executor).run_task(
            Session(session_id="s"), "go"
        )
    )

    assert executor.result_limits == [builder.run_policy.effective_result_limit] * 2
    assert events[-1].payload["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_quote_heavy_result_is_bounded_by_the_per_call_limit():
    class _Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

    async def quote_heavy(_: _Args) -> object:
        return {"payload": '\\"' * 1000}

    registry = ToolRegistry()
    registry.register(
        make_tool(name="quoted", description="quoted", arguments_model=_Args, handler=quote_heavy)
    )
    builder = make_context_builder(max_tool_result_chars=600)
    executor = ToolExecutor(registry.snapshot(), builder.run_policy)
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(FunctionToolCall(id="c1", name="quoted", arguments="{}"),)
            ),
            AssistantMessage(content="done"),
        ]
    )
    session = Session(session_id="s")

    events = await _collect(
        AgentLoop(provider, MODEL, builder, tool_executor=executor).run_task(session, "go")
    )

    result = next(message for message in session.messages if isinstance(message, ToolMessage))
    assert len(result.content) <= builder.run_policy.effective_result_limit
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
async def test_tool_timeout_uses_the_policy_value(monkeypatch):
    recorded_timeouts = []
    original_wait_for = asyncio.wait_for

    async def recording_wait_for(awaitable, timeout):
        recorded_timeouts.append(timeout)
        return await original_wait_for(awaitable, timeout)

    monkeypatch.setattr("morrow.runtime.tool_cycle.asyncio.wait_for", recording_wait_for)
    builder = make_context_builder(tool_timeout_seconds=5.0)
    provider = ScriptedModelProvider(
        [AssistantMessage(tool_calls=(_call("c1"),)), AssistantMessage(content="done")]
    )

    events = await _collect(
        AgentLoop(
            provider,
            MODEL,
            builder,
            tool_executor=_executor(builder.run_policy),
        ).run_task(Session(session_id="s"), "go")
    )

    assert recorded_timeouts == [5.0]
    assert events[-1].payload["finish_reason"] == "stop"
