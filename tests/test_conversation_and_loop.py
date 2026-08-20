"""ConversationLog authority, read-only projections, and the single AgentLoop path."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.models import (
    AssistantMessage,
    FinishReason,
    FunctionToolCall,
    ModelRef,
    ToolMessage,
    UserMessage,
)
from morrow.runtime.agent import AgentLoop, AgentRuntime
from morrow.runtime.conversation import (
    ConversationLog,
    ConversationLogError,
    ConversationSnapshot,
    MessageRecord,
    TurnTerminalRecord,
)
from morrow.runtime.session import Session
from morrow.testing import ScriptedModelProvider, make_context_builder, seed_user_turn


def _tool_call(call_id="call_1", name="lookup_record", arguments='{"k": 1}'):
    return FunctionToolCall(id=call_id, name=name, arguments=arguments)


def test_log_enforces_single_active_turn_and_single_opening_user():
    log = ConversationLog()
    log.begin_turn(UserMessage(content="first"))
    with pytest.raises(ConversationLogError):
        log.begin_turn(UserMessage(content="second"))
    log.append_assistant(AssistantMessage(content="done"))
    log.finish_turn(FinishReason.STOP)
    with pytest.raises(ConversationLogError):
        log.append_assistant(AssistantMessage(content="orphan"))
    with pytest.raises(ConversationLogError):
        log.append_tool_result("call_1", "{}")
    with pytest.raises(ConversationLogError):
        log.finish_turn(FinishReason.STOP)


def test_log_enforces_ordered_results_and_no_terminal_while_cycle_open():
    log = ConversationLog()
    log.begin_turn(UserMessage(content="go"))
    log.append_assistant(
        AssistantMessage(tool_calls=(_tool_call("call_1"), _tool_call("call_2", "calculate")))
    )
    assert log.unresolved_call_ids == ("call_1", "call_2")
    with pytest.raises(ConversationLogError):
        log.append_tool_result("call_2", "{}")
    with pytest.raises(ConversationLogError):
        log.finish_turn(FinishReason.STOP)
    log.append_tool_result("call_1", '{"ok": true}')
    log.append_tool_result("call_2", '{"value": 3}')
    assert log.unresolved_call_ids == ()
    with pytest.raises(ConversationLogError):
        log.append_tool_result("call_1", '{"dup": true}')
    log.append_assistant(AssistantMessage(content="done"))
    log.finish_turn(FinishReason.STOP)
    assert [type(record).__name__ for record in log.snapshot().records] == [
        "MessageRecord",
        "MessageRecord",
        "MessageRecord",
        "MessageRecord",
        "MessageRecord",
        "TurnTerminalRecord",
    ]


def test_public_turn_views_derive_multiple_closed_cycles_and_final_assistant():
    log = ConversationLog()
    log.begin_turn(UserMessage(content="go"))
    log.append_assistant(AssistantMessage(tool_calls=(_tool_call("c1"), _tool_call("c2"))))
    log.append_tool_result("c1", "one")
    log.append_tool_result("c2", "two")
    log.append_assistant(AssistantMessage(tool_calls=(_tool_call("c3"),)))
    log.append_tool_result("c3", "three")
    log.append_assistant(AssistantMessage(content="done"))
    log.finish_turn(FinishReason.STOP)

    turn = log.snapshot().public_turns(require_closed=True)[0]
    assert turn.is_closed is True
    assert [cycle.is_closed for cycle in turn.cycles] == [True, True]
    assert [record.message.tool_call_id for record in turn.cycles[0].results] == ["c1", "c2"]
    assert turn.final_assistant.message.content == "done"
    assert turn.unresolved_call_ids == ()


def test_open_cycle_view_is_immutable_and_reports_unresolved_ids():
    log = ConversationLog()
    log.begin_turn(UserMessage(content="go"))
    log.append_assistant(AssistantMessage(tool_calls=(_tool_call("c1"), _tool_call("c2"))))
    log.append_tool_result("c1", "one")

    snapshot = log.snapshot()
    cycle = snapshot.public_turns()[0].cycles[0]
    assert cycle.is_closed is False
    assert cycle.unresolved_call_ids == ("c2",)
    with pytest.raises(ValidationError):
        cycle.unresolved_call_ids = ()
    with pytest.raises(ConversationLogError):
        snapshot.public_turns(require_closed=True)


def test_log_rejects_assistant_crossing_open_cycle_and_missing_final():
    log = ConversationLog()
    log.begin_turn(UserMessage(content="go"))
    log.append_assistant(AssistantMessage(tool_calls=(_tool_call("c1"),)))
    with pytest.raises(ConversationLogError):
        log.append_assistant(AssistantMessage(content="too early"))
    log.append_tool_result("c1", "one")
    with pytest.raises(ConversationLogError):
        log.finish_turn(FinishReason.STOP)
    log.finish_turn(FinishReason.ERROR)


def test_log_allows_reused_call_ids_across_closed_cycles():
    log = ConversationLog()
    log.begin_turn(UserMessage(content="go"))
    log.append_assistant(AssistantMessage(tool_calls=(_tool_call("call_0"),)))
    log.append_tool_result("call_0", "one")
    log.append_assistant(AssistantMessage(tool_calls=(_tool_call("call_0"),)))
    log.append_tool_result("call_0", "two")
    log.append_assistant(AssistantMessage(content="done"))
    log.finish_turn(FinishReason.STOP)

    turn = log.snapshot().public_turns(require_closed=True)[0]
    assert [cycle.is_closed for cycle in turn.cycles] == [True, True]
    assert [record.message.tool_call_id for cycle in turn.cycles for record in cycle.results] == [
        "call_0",
        "call_0",
    ]


def test_cancelled_terminal_preserves_exact_runtime_interrupted_call_ids():
    log = ConversationLog()
    log.begin_turn(UserMessage(content="go"))
    log.append_assistant(AssistantMessage(tool_calls=(_tool_call("c1"), _tool_call("c2"))))
    log.append_tool_result("c1", "one")
    log.append_tool_result("c2", "cancelled")
    log.finish_turn(FinishReason.CANCELLED, interrupted_call_ids=("c2",))
    terminal = log.snapshot().public_turns(require_closed=True)[0].terminal
    assert terminal.interrupted_call_ids == ("c2",)


@pytest.mark.parametrize(
    "records",
    [
        (TurnTerminalRecord(sequence=1, finish_reason=FinishReason.ERROR),),
        (
            MessageRecord(sequence=1, message=UserMessage(content="u")),
            MessageRecord(sequence=2, message=ToolMessage(tool_call_id="unknown", content="x")),
        ),
        (
            MessageRecord(sequence=1, message=UserMessage(content="u")),
            TurnTerminalRecord(sequence=2, finish_reason=FinishReason.STOP),
        ),
        (
            MessageRecord(sequence=2, message=UserMessage(content="u")),
            MessageRecord(sequence=1, message=AssistantMessage(content="a")),
        ),
    ],
)
def test_snapshot_strict_validation_rejects_malformed_record_order(records):
    with pytest.raises(ConversationLogError):
        ConversationSnapshot(records=records).public_turns(require_closed=True)


@pytest.mark.asyncio
async def test_log_sequences_are_monotonic_and_independent_of_agent_events():
    session = Session(session_id="s")
    runtime = AgentRuntime(
        ScriptedModelProvider(["hello world"]),
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
    )
    events = [event async for event in runtime.run_turn(session, "hi")]
    event_sequences = [event.sequence for event in events]
    record_sequences = [record.sequence for record in session.log.snapshot().records]
    assert record_sequences == sorted(record_sequences) == [1, 2, 3]
    assert event_sequences == [1, 2, 3]


async def _collect(aiter):
    return [item async for item in aiter]


def test_log_snapshot_and_messages_view_are_deeply_read_only():
    session = Session(session_id="s")
    seed_user_turn(session, "question", assistant="answer")
    view = session.messages
    assert isinstance(view, tuple)
    snapshot = session.log.snapshot()
    assert isinstance(snapshot.records, tuple)
    with pytest.raises(AttributeError):
        view.append(UserMessage(content="extra"))
    frozen = snapshot.records[0]
    with pytest.raises(ValidationError):
        frozen.sequence = 99


def test_session_messages_is_read_only_projection_with_no_public_writer():
    session = Session(session_id="s")
    seed_user_turn(session, "question", assistant="answer")
    assert isinstance(session.messages, tuple)
    assert [message.role for message in session.messages] == ["user", "assistant"]
    assert not hasattr(session, "accept_user")
    assert not hasattr(session, "accept_assistant")
    with pytest.raises(AttributeError):
        session.messages = ()
    with pytest.raises(ValidationError):
        session.messages[0].content = "tampered"


def test_session_reset_clears_log_and_process_local_session_state():
    session = Session(session_id="s")
    seed_user_turn(session, "old", assistant="old answer")
    session.reset("s2")
    assert session.session_id == "s2"
    assert session.messages == ()
    assert session.dirty is False
    assert session.log.has_active_turn is False
    assert session.log.snapshot().records == ()


@pytest.mark.asyncio
async def test_plain_chat_uses_agent_loop_with_single_history_writer():
    provider = ScriptedModelProvider(["final answer"])
    session = Session(session_id="s")
    runtime = AgentRuntime(
        provider, ModelRef(provider_id="p", model_id="m"), make_context_builder()
    )
    events = [event async for event in runtime.run_turn(session, "question")]
    assert [event.type for event in events][0] == "turn.started"
    assert events[-1].type == "turn.completed"
    records = session.log.snapshot().records
    assert [type(record).__name__ for record in records] == [
        "MessageRecord",
        "MessageRecord",
        "TurnTerminalRecord",
    ]
    assert records[-1].finish_reason == FinishReason.STOP
    assert [message.content for message in session.messages] == ["question", "final answer"]
    assert session.dirty is True


@pytest.mark.asyncio
async def test_runtime_run_turn_is_thin_delegate_of_loop_run_task():
    provider = ScriptedModelProvider(["same path"])
    session = Session(session_id="s")
    runtime = AgentRuntime(
        provider, ModelRef(provider_id="p", model_id="m"), make_context_builder()
    )
    via_runtime = [event async for event in runtime.run_turn(session, "one")]
    via_loop = [event async for event in runtime.loop.run_task(session, "two")]
    assert via_runtime[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert via_loop[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert [message.content for message in session.messages] == [
        "one",
        "same path",
        "two",
        "same path",
    ]


@pytest.mark.asyncio
async def test_cancelled_turn_records_cancelled_terminal_and_next_turn_succeeds():
    provider = ScriptedModelProvider(["cancel"])
    session = Session(session_id="s")
    runtime = AgentRuntime(
        provider, ModelRef(provider_id="p", model_id="m"), make_context_builder()
    )
    task = asyncio.create_task(_collect(runtime.run_turn(session, "stop me")))
    await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    records = session.log.snapshot().records
    assert [type(record).__name__ for record in records] == [
        "MessageRecord",
        "TurnTerminalRecord",
    ]
    assert records[-1].finish_reason == FinishReason.CANCELLED
    assert [message.role for message in session.messages] == ["user"]

    provider.responses = ["recovered"]
    events = [event async for event in runtime.run_turn(session, "try again")]
    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert [message.content for message in session.messages] == [
        "stop me",
        "try again",
        "recovered",
    ]


@pytest.mark.asyncio
async def test_history_admission_failure_is_invalid_response_not_internal():
    class RejectingLog(ConversationLog):
        def plan_append_assistant(self, message):
            raise ConversationLogError("tool call IDs must be unique within one ToolCycle")

    session = Session(session_id="s", log=RejectingLog())
    events = [
        event
        async for event in AgentLoop(
            ScriptedModelProvider(["hello"]),
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
        ).run_task(session, "go")
    ]
    assert events[-2].payload["stop_code"] == "invalid_response"
    assert events[-1].payload["stop_code"] == "invalid_response"
    assert events[-2].payload["message"] != "模型服务发生未预期错误"


@pytest.mark.asyncio
async def test_pre_start_runtime_failure_still_emits_one_complete_event_pair():
    class FailingCommitter:
        def submit_user(self, *_args, **_kwargs):
            raise RuntimeError("pre-start failure")

    provider = ScriptedModelProvider(["must not run"])
    session = Session(session_id="s", committer=FailingCommitter())
    events = [
        event
        async for event in AgentLoop(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
        ).run_task(session, "go")
    ]

    assert [event.type for event in events] == ["turn.started", "error", "turn.completed"]
    assert events[1].payload["message"] == "任务执行发生未预期错误"
    assert provider.stream_calls == []


@pytest.mark.asyncio
async def test_pre_start_application_error_keeps_stable_non_provider_message():
    class RejectingCommitter:
        def submit_user(self, *_args, **_kwargs):
            raise ApplicationError(ApplicationErrorCode.INVALID, "durable Session state changed")

    provider = ScriptedModelProvider(["must not run"])
    session = Session(session_id="s", committer=RejectingCommitter())
    events = [
        event
        async for event in AgentLoop(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
        ).run_task(session, "go")
    ]

    assert [event.type for event in events] == ["turn.started", "error", "turn.completed"]
    assert events[1].payload["message"] == "durable Session state changed"
    assert "模型服务" not in events[1].payload["message"]
    assert provider.stream_calls == []


@pytest.mark.asyncio
async def test_context_overflow_records_terminal_and_keeps_only_user():
    session = Session(session_id="s")
    loop = AgentLoop(
        ScriptedModelProvider(["never called"]),
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(10),
    )
    events = [event async for event in loop.run_task(session, "way too long input")]
    assert events[-1].payload["finish_reason"] == FinishReason.ERROR.value
    records = session.log.snapshot().records
    assert [type(record).__name__ for record in records] == [
        "MessageRecord",
        "TurnTerminalRecord",
    ]
    assert [message.role for message in session.messages] == ["user"]
