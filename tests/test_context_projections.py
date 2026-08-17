"""Purpose-safe context projections, canonical sizing, and legal reduction."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from morrow.adapters.models.openai_compatible import estimate_request_chars
from morrow.application.context import OMITTED_TOOL_RESULT, ContextBudgetError
from morrow.core.models import (
    AssistantMessage,
    FinishReason,
    FunctionToolCall,
    ToolDefinition,
    ToolFunction,
    ToolMessage,
    UserMessage,
)
from morrow.runtime.session import Session
from morrow.testing import make_context_builder, seed_user_turn


def _call(call_id: str, *, arguments: str = "{}") -> FunctionToolCall:
    return FunctionToolCall(id=call_id, name="lookup_record", arguments=arguments)


def _append_cycle(session: Session, calls, results) -> None:
    session.log.append_assistant(AssistantMessage(tool_calls=tuple(calls)))
    for call, result in zip(calls, results, strict=True):
        session.log.append_tool_result(call.id, result)


def _seed_tool_turn(session: Session, *, result: str = "secret-tool-data") -> None:
    session.log.begin_turn(UserMessage(content="completed user"))
    _append_cycle(session, [_call("c1")], [result])
    session.log.append_assistant(AssistantMessage(content="completed final"))
    session.log.finish_turn(FinishReason.STOP)


def test_explicit_projections_keep_tool_data_out_of_structured_and_fallback_views():
    session = Session(session_id="s")
    _seed_tool_turn(session)
    seed_user_turn(session, "failed latest user", finish=FinishReason.ERROR)
    builder = make_context_builder()

    chat_session = Session(session_id="chat", log=session.log)
    chat_session.log.begin_turn(UserMessage(content="current chat"))
    chat = builder.build(chat_session)
    structured = builder.build(chat_session, purpose="structured")
    fallback = builder.build(chat_session, purpose="handoff_fallback")

    assert len([message for message in chat.messages if message.role == "system"]) == 2
    assert any(isinstance(message, ToolMessage) for message in chat.messages)
    assert all(not isinstance(message, ToolMessage) for message in structured.messages)
    assert all(not getattr(message, "tool_calls", ()) for message in structured.messages)
    assert "secret-tool-data" not in "\n".join(
        message.content or "" for message in structured.messages
    )
    assert [message.content for message in fallback.messages] == [
        "current chat",
        "completed final",
    ]
    assert structured.tools == fallback.tools == ()


def test_context_request_pack_and_source_snapshot_are_immutable_and_build_is_pure():
    session = Session(session_id="s")
    _seed_tool_turn(session)
    session.log.begin_turn(UserMessage(content="current"))
    builder = make_context_builder()
    before = session.log.snapshot()
    request = builder._request(session, "chat", ())

    first = builder.build(session)
    second = builder.build(session)

    assert first == second
    assert session.log.snapshot() == before
    with pytest.raises(ValidationError):
        request.request_char_limit = 1
    with pytest.raises(ValidationError):
        first.messages = ()


def test_multi_result_cycle_is_cleared_atomically_without_touching_log():
    session = Session(session_id="s")
    session.log.begin_turn(UserMessage(content="old tool request"))
    calls = (_call("c1"), _call("c2"))
    _append_cycle(session, calls, ["x" * 1000, "y" * 1000])
    session.log.append_assistant(AssistantMessage(content="old final"))
    session.log.finish_turn(FinishReason.STOP)
    session.log.begin_turn(UserMessage(content="current"))
    source = session.log.snapshot()

    probe = make_context_builder(100_000).build(session)
    cleared_messages = tuple(
        ToolMessage(tool_call_id=message.tool_call_id, content=OMITTED_TOOL_RESULT)
        if isinstance(message, ToolMessage)
        else message
        for message in probe.messages
    )
    limit = estimate_request_chars(cleared_messages, ())
    pack = make_context_builder(limit).build(session)

    results = [message for message in pack.messages if isinstance(message, ToolMessage)]
    assert [message.content for message in results] == [OMITTED_TOOL_RESULT] * 2
    assert pack.cleared_cycle_count == 1
    assert session.log.snapshot() == source
    assert [record.message.content for record in source.records if hasattr(record, "message")][
        2:4
    ] == [
        "x" * 1000,
        "y" * 1000,
    ]


def test_hard_trim_drops_oldest_whole_turn_and_counts_source_records():
    session = Session(session_id="s")
    seed_user_turn(session, "old user" * 100, assistant="old answer" * 100)
    session.log.begin_turn(UserMessage(content="current"))
    probe = make_context_builder()
    mandatory = (*probe._system_messages(session), UserMessage(content="current"))
    limit = estimate_request_chars(mandatory, ())

    pack = make_context_builder(limit).build(session)

    assert [message.content for message in pack.messages if message.role != "system"] == ["current"]
    assert pack.dropped_record_count == 3


def test_hard_trim_drops_oldest_closed_cycle_but_preserves_current_user():
    session = Session(session_id="s")
    session.log.begin_turn(UserMessage(content="current"))
    _append_cycle(session, [_call("large", arguments="x" * 2000)], ["small"])
    _append_cycle(session, [_call("keep")], ["kept-result"])
    probe = make_context_builder(100_000)
    systems = probe._system_messages(session)
    retained = (
        *systems,
        UserMessage(content="current"),
        AssistantMessage(tool_calls=(_call("keep"),)),
        ToolMessage(tool_call_id="keep", content=OMITTED_TOOL_RESULT),
    )
    limit = estimate_request_chars(retained, ())

    pack = make_context_builder(limit).build(session)

    assert "large" not in [
        call.id for message in pack.messages for call in getattr(message, "tool_calls", ())
    ]
    assert pack.messages[2].content == "current"
    assert pack.dropped_record_count == 2


def test_protected_context_overflow_is_typed_context_budget_failure():
    session = Session(session_id="s")
    session.log.begin_turn(UserMessage(content="x" * 1000))
    with pytest.raises(ContextBudgetError) as exc_info:
        make_context_builder(10).build(session)
    assert exc_info.value.code == "context_budget"


def test_canonical_estimator_counts_tool_schema_and_rejects_wire_oversize():
    session = Session(session_id="s")
    session.log.begin_turn(UserMessage(content="short"))
    tool = ToolDefinition(
        function=ToolFunction(
            name="lookup_record",
            description="d" * 500,
            parameters={"type": "object", "properties": {"key": {"type": "string"}}},
        )
    )
    probe = make_context_builder()
    no_tools = (*probe._system_messages(session), UserMessage(content="short"))
    content_only_limit = estimate_request_chars(no_tools, ())

    with pytest.raises(ContextBudgetError):
        make_context_builder(content_only_limit).build(session, tools=(tool,))


def test_estimator_is_exact_compact_canonical_json_length():
    messages = (UserMessage(content="你好"),)
    expected = json.dumps(
        {"messages": [{"role": "user", "content": "你好"}]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert estimate_request_chars(messages, ()) == len(expected)
