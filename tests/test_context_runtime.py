from __future__ import annotations

import asyncio

import pytest

from morrow.application.context import ContextBuilder
from morrow.core.events import lifecycle_is_valid
from morrow.core.models import FinishReason, Handoff, ModelRef, Preferences, Profile
from morrow.interfaces.spike import consume_stream, eof_to_action
from morrow.runtime.agent import AgentRuntime
from morrow.runtime.session import Session
from morrow.testing import FixedClock, FixedIdSource, ScriptedModelProvider


def test_context_excludes_handoff_until_explicit_load():
    session = Session(session_id="s", profile=Profile(name="demo"))
    builder = ContextBuilder()
    independent = builder.build(session)
    assert "current_goal" not in "\n".join(message.content for message in independent.messages)
    session.loaded_handoff = Handoff(current_goal="continue this")
    session.handoff_source_revision = 1
    attached = builder.build(session)
    assert "continue this" in "\n".join(message.content for message in attached.messages)


def test_preferences_have_global_workspace_session_precedence():
    value = ContextBuilder.merge_preferences(
        Preferences(language="中文", instructions=["global", "same"]),
        Preferences(instructions=["workspace", "same"]),
        Preferences(language="English", instructions=["session", "same"]),
    )
    assert value.language == "English"
    assert value.instructions == ["global", "workspace", "session", "same"]


@pytest.mark.asyncio
async def test_runtime_emits_one_ordered_lifecycle_and_admits_complete_history():
    provider = ScriptedModelProvider([["hello", " world"]])
    ids = FixedIdSource()
    session = Session(session_id="session")
    runtime = AgentRuntime(
        provider,
        ModelRef(provider_id="p", model_id="m"),
        ContextBuilder(),
        id_source=ids,
        clock=FixedClock(),
    )
    events = [event async for event in runtime.run_turn(session, "hi")]
    assert lifecycle_is_valid(events)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert [message.content for message in session.messages] == ["hi", "hello world"]


@pytest.mark.asyncio
async def test_cancel_preserves_user_but_not_partial_assistant():
    provider = ScriptedModelProvider(["cancel"])
    session = Session(session_id="session")
    runtime = AgentRuntime(provider, ModelRef(provider_id="p", model_id="m"), ContextBuilder())
    task = asyncio.create_task(_collect(runtime.run_turn(session, "stop me")))
    await asyncio.sleep(0.01)
    task.cancel()
    events = await task
    assert events[-1].payload["finish_reason"] == FinishReason.CANCELLED.value
    assert [message.role for message in session.messages] == ["user"]


@pytest.mark.asyncio
async def test_network_error_retries_once_before_visible_text():
    provider = ScriptedModelProvider([RuntimeError("network"), "recovered"])
    session = Session(session_id="session")
    runtime = AgentRuntime(
        provider,
        ModelRef(provider_id="p", model_id="m"),
        ContextBuilder(),
        max_retries=1,
    )
    events = [event async for event in runtime.run_turn(session, "retry")]
    assert [event.type for event in events].count("status.changed") == 1
    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert len(provider.stream_calls) == 2


@pytest.mark.asyncio
async def test_empty_completion_is_error_without_empty_assistant_message():
    provider = ScriptedModelProvider([[]])
    session = Session(session_id="session")
    runtime = AgentRuntime(provider, ModelRef(provider_id="p", model_id="m"), ContextBuilder())

    events = [event async for event in runtime.run_turn(session, "empty")]

    assert events[-1].payload["finish_reason"] == FinishReason.ERROR.value
    assert [message.role for message in session.messages] == ["user"]


def test_oversized_current_input_is_rejected_before_model_call():
    provider = ScriptedModelProvider(["should not run"])
    session = Session(session_id="session")
    runtime = AgentRuntime(
        provider,
        ModelRef(provider_id="p", model_id="m"),
        ContextBuilder(max_chars=100),
    )
    import asyncio

    events = asyncio.run(_collect(runtime.run_turn(session, "x" * 1000)))
    assert events[-1].payload["finish_reason"] == FinishReason.ERROR.value
    assert provider.stream_calls == []


async def _collect(iterator):
    return [event async for event in iterator]


@pytest.mark.asyncio
async def test_terminal_spike_first_cancel_closes_producer_and_eof_has_one_exit_path():
    cancelled = False

    async def producer():
        nonlocal cancelled
        try:
            await asyncio.sleep(10)
            return "late"
        except asyncio.CancelledError:
            cancelled = True
            raise

    event = asyncio.Event()
    task = asyncio.create_task(consume_stream(producer(), lambda text: None, cancel_event=event))
    event.set()
    result = await task
    assert result.cancelled is True
    assert cancelled is True
    assert eof_to_action(None) == "exit"
