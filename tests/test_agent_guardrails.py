"""Progress-aware retry, repeated-Cycle stops, and public event contracts."""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel, ConfigDict

from morrow.core.events import lifecycle_is_valid
from morrow.core.models import (
    AssistantMessage,
    FunctionToolCall,
    ModelErrorCode,
    ModelEvent,
    ModelFinishReason,
    ModelRef,
)
from morrow.runtime.agent import AgentLoop
from morrow.runtime.conversation import ConversationLog
from morrow.runtime.session import Session
from morrow.runtime.tools import ToolExecutor, ToolRegistry, make_tool
from morrow.testing import ScriptedModelProvider, make_context_builder

MODEL = ModelRef(provider_id="p", model_id="m")


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


def _call(call_id: str, value: str) -> FunctionToolCall:
    return FunctionToolCall(
        id=call_id,
        name="echo",
        arguments=json.dumps({"value": value}),
    )


def _executor(handler=None, *, builder=None):
    async def echo(arguments: _Args) -> object:
        return {"echo": arguments.value}

    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="echo",
            description="echo",
            arguments_model=_Args,
            handler=handler or echo,
        )
    )
    context_builder = builder or make_context_builder()
    return ToolExecutor(registry.snapshot(), context_builder.run_policy)


async def _collect(iterator):
    return [event async for event in iterator]


class _EventProvider:
    def __init__(self, attempts):
        self.attempts = list(attempts)
        self.stream_calls = []

    async def stream(self, model, messages, tools=()):
        del model, tools
        self.stream_calls.append(list(messages))
        for event in self.attempts.pop(0):
            yield event


@pytest.mark.asyncio
async def test_tool_fragment_progress_prevents_transient_retry():
    provider = _EventProvider(
        [[ModelEvent(kind="error", error_code=ModelErrorCode.NETWORK, made_progress=True)]]
    )
    events = await _collect(
        AgentLoop(provider, MODEL, make_context_builder()).run_task(Session(session_id="s"), "go")
    )
    assert len(provider.stream_calls) == 1
    assert not any(event.type == "status.changed" for event in events)
    assert events[-2].payload["stop_code"] == "provider_network"


@pytest.mark.asyncio
async def test_zero_progress_transient_retries_but_auth_never_retries():
    transient = _EventProvider(
        [
            [ModelEvent(kind="error", error_code=ModelErrorCode.TIMEOUT)],
            [ModelEvent(kind="error", error_code=ModelErrorCode.TIMEOUT)],
        ]
    )
    transient_events = await _collect(
        AgentLoop(transient, MODEL, make_context_builder()).run_task(
            Session(session_id="transient"), "go"
        )
    )
    assert len(transient.stream_calls) == 2
    assert [event.type for event in transient_events].count("status.changed") == 1
    assert transient_events[-2].payload["stop_code"] == "provider_timeout"

    auth = _EventProvider([[ModelEvent(kind="error", error_code=ModelErrorCode.AUTH)]])
    auth_events = await _collect(
        AgentLoop(auth, MODEL, make_context_builder()).run_task(Session(session_id="auth"), "go")
    )
    assert len(auth.stream_calls) == 1
    assert auth_events[-2].payload["stop_code"] == "provider_auth"


@pytest.mark.parametrize(
    ("finish_reason", "message"),
    [
        (
            ModelFinishReason.STOP,
            AssistantMessage(content="text", tool_calls=(_call("c1", "value"),)),
        ),
        (ModelFinishReason.TOOL_CALLS, AssistantMessage(content="text")),
    ],
)
@pytest.mark.asyncio
async def test_finish_reason_must_match_the_assistant_message_shape(finish_reason, message):
    provider = _EventProvider(
        [[ModelEvent(kind="completed", finish_reason=finish_reason, message=message)]]
    )
    builder = make_context_builder()
    events = await _collect(
        AgentLoop(
            provider,
            MODEL,
            builder,
            tool_executor=_executor(builder=builder),
        ).run_task(Session(session_id="s"), "go")
    )

    assert events[-2].payload["stop_code"] == "invalid_response"
    assert events[-1].payload["stop_code"] == "invalid_response"


@pytest.mark.asyncio
async def test_repeated_single_cycle_stops_at_configured_repeat_limit():
    provider = ScriptedModelProvider(
        [AssistantMessage(tool_calls=(_call(f"c{index}", "same"),)) for index in range(1, 5)]
    )
    builder = make_context_builder(
        max_tool_rounds=6,
        loop_repeat_limit=3,
        loop_max_pattern_cycles=2,
    )
    session = Session(session_id="s")
    events = await _collect(
        AgentLoop(
            provider,
            MODEL,
            builder,
            tool_executor=_executor(builder=builder),
        ).run_task(session, "go")
    )
    assert len(provider.stream_calls) == 3
    assert events[-2].payload["stop_code"] == "loop_detected"
    assert session.log.unresolved_call_ids == ()


@pytest.mark.asyncio
async def test_repeated_two_cycle_pattern_detects_but_near_match_does_not():
    repeated = ["A", "B"] * 3
    provider = ScriptedModelProvider(
        [
            AssistantMessage(tool_calls=(_call(f"r{index}", value),))
            for index, value in enumerate(repeated)
        ]
    )
    builder = make_context_builder(
        max_tool_rounds=6,
        loop_repeat_limit=3,
        loop_max_pattern_cycles=2,
    )
    repeated_events = await _collect(
        AgentLoop(
            provider,
            MODEL,
            builder,
            tool_executor=_executor(builder=builder),
        ).run_task(Session(session_id="repeat"), "go")
    )
    assert len(provider.stream_calls) == 6
    assert repeated_events[-2].payload["stop_code"] == "loop_detected"

    near_provider = ScriptedModelProvider(
        [
            AssistantMessage(tool_calls=(_call("n1", "A"),)),
            AssistantMessage(tool_calls=(_call("n2", "A"),)),
            AssistantMessage(tool_calls=(_call("n3", "changed"),)),
            AssistantMessage(content="done"),
        ]
    )
    near_session = Session(session_id="near")
    near_events = await _collect(
        AgentLoop(
            near_provider,
            MODEL,
            builder,
            tool_executor=_executor(builder=builder),
        ).run_task(near_session, "go")
    )
    assert near_events[-1].payload["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_changing_results_breaks_loop_equality_and_tool_events_are_secret_safe():
    counter = 0

    async def changing(arguments: _Args) -> object:
        nonlocal counter
        counter += 1
        return {"echo": arguments.value, "counter": counter, "secret": "result-secret"}

    provider = ScriptedModelProvider(
        [
            AssistantMessage(tool_calls=(_call("c1", "argument-secret"),)),
            AssistantMessage(tool_calls=(_call("c2", "argument-secret"),)),
            AssistantMessage(tool_calls=(_call("c3", "argument-secret"),)),
            AssistantMessage(content="done"),
        ]
    )
    builder = make_context_builder(loop_repeat_limit=3, loop_max_pattern_cycles=1)
    events = await _collect(
        AgentLoop(
            provider,
            MODEL,
            builder,
            tool_executor=_executor(changing, builder=builder),
        ).run_task(Session(session_id="s"), "go")
    )
    assert events[-1].payload["finish_reason"] == "stop"
    statuses = [event for event in events if event.type == "tool.status"]
    assert [event.payload["status"] for event in statuses] == [
        "running",
        "succeeded",
        "running",
        "succeeded",
        "running",
        "succeeded",
    ]
    assert "argument-secret" not in str([event.payload for event in statuses])
    assert "result-secret" not in str([event.payload for event in statuses])
    assert lifecycle_is_valid(events)


def test_fatal_public_event_contract_is_exact_and_matches_completion():
    provider = _EventProvider([[ModelEvent(kind="error", error_code=ModelErrorCode.AUTH)]])
    events = asyncio.run(
        _collect(
            AgentLoop(provider, MODEL, make_context_builder()).run_task(
                Session(session_id="s"), "go"
            )
        )
    )
    error, completed = events[-2:]
    assert set(error.payload) == {"message", "stop_code"}
    assert completed.payload == {
        "finish_reason": "error",
        "text": "",
        "text_length": 0,
        "stop_code": "provider_auth",
    }
    assert lifecycle_is_valid(events)


@pytest.mark.asyncio
async def test_cancellation_after_text_progress_discards_partial_assistant_and_recovers():
    progressed = asyncio.Event()

    class Provider:
        async def stream(self, model, messages, tools=()):
            del model, messages, tools
            yield ModelEvent(kind="text_delta", text="partial")
            progressed.set()
            await asyncio.Event().wait()

    session = Session(session_id="s")
    task = asyncio.create_task(
        _collect(AgentLoop(Provider(), MODEL, make_context_builder()).run_task(session, "go"))
    )
    await progressed.wait()
    task.cancel()
    events = await task

    assert [message.role for message in session.messages] == ["user"]
    assert events[-1].payload == {
        "finish_reason": "cancelled",
        "text": "partial",
        "text_length": 7,
    }
    assert lifecycle_is_valid(events)


@pytest.mark.asyncio
async def test_cancellation_before_user_commit_emits_completion_without_history_record():
    class CancellingBeforeBeginLog(ConversationLog):
        def begin_turn(self, user):
            del user
            raise asyncio.CancelledError

    session = Session(session_id="s", log=CancellingBeforeBeginLog())
    events = await _collect(
        AgentLoop(ScriptedModelProvider(["unused"]), MODEL, make_context_builder()).run_task(
            session, "go"
        )
    )
    assert session.log.snapshot().records == ()
    assert events[-1].payload["finish_reason"] == "cancelled"
    assert lifecycle_is_valid(events)


@pytest.mark.asyncio
async def test_cancellation_after_complete_before_acceptance_discards_assistant():
    class Provider:
        async def stream(self, model, messages, tools=()):
            del model, messages, tools
            asyncio.current_task().cancel()
            yield ModelEvent(
                kind="completed",
                finish_reason=ModelFinishReason.STOP,
                message=AssistantMessage(content="must not commit"),
            )

    session = Session(session_id="s")
    task = asyncio.create_task(
        _collect(AgentLoop(Provider(), MODEL, make_context_builder()).run_task(session, "go"))
    )
    events = await task
    assert [message.role for message in session.messages] == ["user"]
    assert events[-1].payload["finish_reason"] == "cancelled"


@pytest.mark.asyncio
async def test_cancellation_before_first_tool_marks_every_call_skipped():
    class CancellingLog(ConversationLog):
        def append_assistant(self, message):
            super().append_assistant(message)
            if message.tool_calls:
                asyncio.current_task().cancel()

    provider = ScriptedModelProvider(
        [AssistantMessage(tool_calls=(_call("c1", "one"), _call("c2", "two")))]
    )
    builder = make_context_builder()
    session = Session(session_id="s", log=CancellingLog())
    task = asyncio.create_task(
        _collect(
            AgentLoop(
                provider,
                MODEL,
                builder,
                tool_executor=_executor(builder=builder),
            ).run_task(session, "go")
        )
    )
    events = await task
    statuses = [event.payload["status"] for event in events if event.type == "tool.status"]
    assert statuses == ["skipped", "skipped"]
    assert session.log.unresolved_call_ids == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("call_count", [1, 2])
async def test_cancellation_after_a_tool_result_preserves_it_and_closes_remaining(call_count):
    builder = make_context_builder()
    executor = _executor(builder=builder)

    class CancellingLog(ConversationLog):
        def append_tool_result(self, tool_call_id, content):
            super().append_tool_result(tool_call_id, content)
            if tool_call_id == "c1":
                asyncio.current_task().cancel()

    calls = tuple(_call(f"c{index}", str(index)) for index in range(1, call_count + 1))
    provider = ScriptedModelProvider([AssistantMessage(tool_calls=calls)])
    session = Session(session_id="s", log=CancellingLog())
    task = asyncio.create_task(
        _collect(
            AgentLoop(provider, MODEL, builder, tool_executor=executor).run_task(session, "go")
        )
    )
    events = await task

    results = [message for message in session.messages if message.role == "tool"]
    assert json.loads(results[0].content)["ok"] is True
    if call_count == 2:
        assert json.loads(results[1].content)["error"]["code"] == "cancelled"
    assert events[-1].payload["finish_reason"] == "cancelled"
    assert session.log.unresolved_call_ids == ()


@pytest.mark.asyncio
async def test_aclose_while_yielded_closes_active_turn_for_the_next_begin():
    class StreamingThenHang:
        async def stream(self, model, messages, tools=()):
            del model, messages, tools
            yield ModelEvent(kind="text_delta", text="partial")
            await asyncio.Event().wait()
            yield

    session = Session(session_id="s")
    gen = AgentLoop(StreamingThenHang(), MODEL, make_context_builder()).run_task(session, "go")
    assert (await anext(gen)).type == "turn.started"
    assert (await anext(gen)).type == "text.delta"
    await gen.aclose()

    assert session.log.has_active_turn is False
    assert session.log.snapshot().records[-1].finish_reason.value == "cancelled"

    events = await _collect(
        AgentLoop(ScriptedModelProvider(["recovered"]), MODEL, make_context_builder()).run_task(
            session, "again"
        )
    )
    assert events[-1].payload["finish_reason"] == "stop"
    assert [message.content for message in session.messages] == ["go", "again", "recovered"]


@pytest.mark.asyncio
async def test_aclose_after_tool_running_closes_unresolved_cycle():
    provider = ScriptedModelProvider(
        [AssistantMessage(tool_calls=(_call("c1", "one"), _call("c2", "two")))]
    )
    builder = make_context_builder()
    session = Session(session_id="s")
    gen = AgentLoop(provider, MODEL, builder, tool_executor=_executor(builder=builder)).run_task(
        session, "go"
    )
    while True:
        event = await anext(gen)
        if event.type == "tool.status" and event.payload["status"] == "running":
            break
    await gen.aclose()

    assert session.log.has_active_turn is False
    assert session.log.unresolved_call_ids == ()
    results = [message for message in session.messages if message.role == "tool"]
    assert [json.loads(message.content)["error"]["code"] for message in results] == [
        "cancelled",
        "cancelled",
    ]


@pytest.mark.asyncio
async def test_cancellation_after_final_assistant_append_is_ignored_as_committed():
    class CancellingLog(ConversationLog):
        def append_assistant(self, message):
            super().append_assistant(message)
            if not message.tool_calls:
                asyncio.current_task().cancel()

    session = Session(session_id="s", log=CancellingLog())
    events = await _collect(
        AgentLoop(ScriptedModelProvider(["committed"]), MODEL, make_context_builder()).run_task(
            session, "go"
        )
    )
    assert events[-1].payload["finish_reason"] == "stop"
    assert session.log.snapshot().records[-1].finish_reason.value == "stop"
    assert [message.content for message in session.messages] == ["go", "committed"]
