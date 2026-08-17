"""AgentLoop tool rounds and minimum cancelled/internal unresolved-call closure."""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel, ConfigDict

from morrow.core.events import lifecycle_is_valid
from morrow.core.models import (
    AssistantMessage,
    FinishReason,
    FunctionToolCall,
    ModelRef,
    ToolMessage,
)
from morrow.runtime.agent import AgentLoop
from morrow.runtime.session import Session
from morrow.runtime.tools import (
    ToolExecutor,
    ToolRegistry,
    ToolSet,
    make_tool,
)
from morrow.testing import make_context_builder, make_run_policy

MODEL = ModelRef(provider_id="p", model_id="m")


class EchoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


def _echo_executor(cancel_on: str | None = None, explode_on_id: str | None = None):
    async def handler(arguments: EchoArguments) -> object:
        if cancel_on is not None and arguments.value == cancel_on:
            raise asyncio.CancelledError
        return {"echo": arguments.value}

    registry = ToolRegistry()
    registry.register(
        make_tool(name="echo", description="echo", arguments_model=EchoArguments, handler=handler)
    )
    executor = ToolExecutor(registry.snapshot(), make_run_policy())
    if explode_on_id is not None:
        return _ExplodingExecutor(executor.tool_set, explode_on_id)
    return executor


class _ExplodingExecutor(ToolExecutor):
    def __init__(self, tool_set: ToolSet, explode_on_id: str) -> None:
        super().__init__(tool_set, make_run_policy())
        self._explode_on_id = explode_on_id

    async def execute(self, call, **kwargs):
        if call.id == self._explode_on_id:
            raise RuntimeError("executor exploded unexpectedly")
        return await super().execute(call, **kwargs)


def _call(call_id: str, value: str) -> FunctionToolCall:
    return FunctionToolCall(id=call_id, name="echo", arguments=json.dumps({"value": value}))


async def _collect(aiter):
    return [item async for item in aiter]


def _roles(session: Session) -> list[str]:
    return [message.role for message in session.messages]


def _tool_messages(session: Session) -> list[ToolMessage]:
    return [message for message in session.messages if isinstance(message, ToolMessage)]


def _envelope_code(message: ToolMessage) -> str:
    return json.loads(message.content)["error"]["code"]


@pytest.mark.asyncio
async def test_second_cycle_may_reuse_vendor_call_ids():
    provider = _provider(
        AssistantMessage(tool_calls=(_call("call_0", "first"),)),
        AssistantMessage(tool_calls=(_call("call_0", "second"),)),
        AssistantMessage(content="done"),
    )
    session = Session(session_id="s")
    events = await _collect(
        AgentLoop(provider, MODEL, make_context_builder(), tool_executor=_echo_executor()).run_task(
            session, "question"
        )
    )

    assert lifecycle_is_valid(events)
    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert [message.tool_call_id for message in _tool_messages(session)] == ["call_0", "call_0"]
    assert [
        json.loads(message.content)["result"]["echo"] for message in _tool_messages(session)
    ] == [
        "first",
        "second",
    ]


@pytest.mark.asyncio
async def test_tool_round_then_final_text_keeps_legal_history():
    provider = _provider(
        AssistantMessage(tool_calls=(_call("c1", "alpha"),)),
        AssistantMessage(content="done"),
    )
    session = Session(session_id="s")
    loop = AgentLoop(provider, MODEL, make_context_builder(), tool_executor=_echo_executor())

    events = await _collect(loop.run_task(session, "question"))

    assert lifecycle_is_valid(events)
    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert _roles(session) == ["user", "assistant", "tool", "assistant"]
    tool_message = _tool_messages(session)[0]
    assert tool_message.tool_call_id == "c1"
    assert json.loads(tool_message.content) == {"ok": True, "result": {"echo": "alpha"}}
    # tools were sent to the provider and the second call saw the tool result
    assert provider.stream_tools[0]
    assert isinstance(provider.stream_calls[1][-1], ToolMessage)
    assert provider.stream_calls[1][-1].tool_call_id == "c1"


def _provider(*responses):
    from morrow.testing import ScriptedModelProvider

    return ScriptedModelProvider(list(responses))


@pytest.mark.asyncio
async def test_multi_call_batch_results_arrive_in_original_order():
    provider = _provider(
        AssistantMessage(tool_calls=(_call("c1", "one"), _call("c2", "two"))),
        AssistantMessage(content="finished"),
    )
    session = Session(session_id="s")
    loop = AgentLoop(provider, MODEL, make_context_builder(), tool_executor=_echo_executor())

    events = await _collect(loop.run_task(session, "question"))

    assert lifecycle_is_valid(events)
    assert [m.tool_call_id for m in _tool_messages(session)] == ["c1", "c2"]
    assert [json.loads(m.content)["result"]["echo"] for m in _tool_messages(session)] == [
        "one",
        "two",
    ]


@pytest.mark.asyncio
async def test_cancellation_before_first_result_closes_all_calls():
    provider = _provider(AssistantMessage(tool_calls=(_call("c1", "boom"), _call("c2", "later"))))
    session = Session(session_id="s")
    loop = AgentLoop(
        provider, MODEL, make_context_builder(), tool_executor=_echo_executor(cancel_on="boom")
    )

    events = await _collect(loop.run_task(session, "question"))

    assert lifecycle_is_valid(events)
    assert events[-1].payload["finish_reason"] == FinishReason.CANCELLED.value
    codes = [_envelope_code(m) for m in _tool_messages(session)]
    assert codes == ["cancelled", "cancelled"]
    statuses = [event.payload for event in events if event.type == "tool.status"]
    assert [(status["status"], status.get("error_code")) for status in statuses] == [
        ("running", None),
        ("cancelled", "cancelled"),
        ("skipped", "cancelled"),
    ]
    records = session.log.snapshot().records
    assert records[-1].finish_reason == FinishReason.CANCELLED
    assert session.log.unresolved_call_ids == ()

    provider.responses = [AssistantMessage(content="recovered")]
    recovered = await _collect(loop.run_task(session, "next question"))
    assert recovered[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert _roles(session) == ["user", "assistant", "tool", "tool", "user", "assistant"]


@pytest.mark.asyncio
async def test_cancellation_after_partial_results_preserves_completed_ones():
    provider = _provider(AssistantMessage(tool_calls=(_call("c1", "safe"), _call("c2", "boom"))))
    session = Session(session_id="s")
    loop = AgentLoop(
        provider, MODEL, make_context_builder(), tool_executor=_echo_executor(cancel_on="boom")
    )

    events = await _collect(loop.run_task(session, "question"))

    assert events[-1].payload["finish_reason"] == FinishReason.CANCELLED.value
    results = _tool_messages(session)
    assert json.loads(results[0].content)["result"] == {"echo": "safe"}
    assert _envelope_code(results[1]) == "cancelled"
    statuses = [event.payload["status"] for event in events if event.type == "tool.status"]
    assert statuses == ["running", "succeeded", "running", "cancelled"]


@pytest.mark.asyncio
async def test_unexpected_post_admission_exception_closes_with_internal():
    provider = _provider(AssistantMessage(tool_calls=(_call("c1", "safe"), _call("c2", "doomed"))))
    session = Session(session_id="s")
    loop = AgentLoop(
        provider, MODEL, make_context_builder(), tool_executor=_echo_executor(explode_on_id="c2")
    )

    events = await _collect(loop.run_task(session, "question"))

    assert lifecycle_is_valid(events)
    assert events[-1].payload["finish_reason"] == FinishReason.ERROR.value
    errors = [event for event in events if event.type == "error"]
    assert len(errors) == 1
    assert errors[0].payload["stop_code"] == "internal"
    assert "exploded" not in errors[0].payload["message"]
    results = _tool_messages(session)
    assert json.loads(results[0].content)["result"] == {"echo": "safe"}
    assert _envelope_code(results[1]) == "internal"
    statuses = [event.payload["status"] for event in events if event.type == "tool.status"]
    assert statuses == ["running", "succeeded", "running", "failed"]
    assert session.log.snapshot().records[-1].finish_reason == FinishReason.ERROR

    provider.responses = [AssistantMessage(content="after failure")]
    recovered = await _collect(loop.run_task(session, "try again"))
    assert recovered[-1].payload["finish_reason"] == FinishReason.STOP.value


@pytest.mark.asyncio
async def test_no_tools_loop_rejects_tool_call_completion_before_admission():
    provider = _provider(AssistantMessage(tool_calls=(_call("c1", "alpha"),)))
    session = Session(session_id="s")
    loop = AgentLoop(provider, MODEL, make_context_builder())

    events = await _collect(loop.run_task(session, "question"))

    assert lifecycle_is_valid(events)
    assert events[-1].payload["finish_reason"] == FinishReason.ERROR.value
    assert _roles(session) == ["user"]
    assert _tool_messages(session) == []
    records = session.log.snapshot().records
    assert [type(record).__name__ for record in records] == [
        "MessageRecord",
        "TurnTerminalRecord",
    ]


@pytest.mark.asyncio
async def test_tool_failure_envelope_continues_loop_without_retry():
    provider = _provider(
        AssistantMessage(tool_calls=(_call("c1", "value"),)),
        AssistantMessage(content="handled the failure"),
    )
    session = Session(session_id="s")

    class _FailingExecutor(ToolExecutor):
        async def execute(self, call, **kwargs):
            outcome = await super().execute(call, **kwargs)
            self.attempts = getattr(self, "attempts", 0) + 1
            return outcome

    executor = _FailingExecutor(_executor_with_failing_tool(), make_run_policy())
    loop = AgentLoop(provider, MODEL, make_context_builder(), tool_executor=executor)

    events = await _collect(loop.run_task(session, "question"))

    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert executor.attempts == 1
    assert _envelope_code(_tool_messages(session)[0]) == "not_found"


@pytest.mark.asyncio
async def test_each_continuation_rebuilds_context_from_the_latest_snapshot():
    provider = _provider(
        AssistantMessage(tool_calls=(_call("c1", "value"),)),
        AssistantMessage(content="done"),
    )
    session = Session(session_id="s")
    builder = make_context_builder()
    original_build = builder.build
    build_snapshots = []

    def recording_build(*args, **kwargs):
        pack = original_build(*args, **kwargs)
        build_snapshots.append(args[0].log.snapshot())
        return pack

    builder.build = recording_build
    loop = AgentLoop(
        provider,
        MODEL,
        builder,
        tool_executor=_echo_executor(),
    )

    events = await _collect(loop.run_task(session, "question"))

    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert len(provider.stream_calls) == 2
    assert len(build_snapshots) == 2
    assert not any(isinstance(message, ToolMessage) for message in build_snapshots[0].messages())
    assert any(isinstance(message, ToolMessage) for message in build_snapshots[1].messages())


@pytest.mark.asyncio
async def test_cancelled_synthetic_result_respects_the_assigned_result_limit():
    provider = _provider(AssistantMessage(tool_calls=(_call("c1", "cancel"),)))
    builder = make_context_builder(max_tool_result_chars=62)
    session = Session(session_id="s")
    events = await _collect(
        AgentLoop(
            provider,
            MODEL,
            builder,
            tool_executor=_echo_executor(cancel_on="cancel"),
        ).run_task(session, "question")
    )

    result = _tool_messages(session)[0]
    assert len(result.content) <= builder.run_policy.effective_result_limit
    assert _envelope_code(result) == "cancelled"
    assert events[-1].payload["finish_reason"] == FinishReason.CANCELLED.value


def _executor_with_failing_tool() -> ToolSet:
    async def handler(_: EchoArguments) -> object:
        from morrow.runtime.tools import ToolErrorCode, ToolExecutionError

        raise ToolExecutionError(ToolErrorCode.NOT_FOUND, "没有找到")

    registry = ToolRegistry()
    registry.register(
        make_tool(name="echo", description="echo", arguments_model=EchoArguments, handler=handler)
    )
    return registry.snapshot()
