from __future__ import annotations

import asyncio

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.application.context import ContextBuilder
from morrow.bootstrap import build_application, build_session_application
from morrow.core.events import lifecycle_is_valid
from morrow.core.models import (
    FinishReason,
    ModelEvent,
    ModelFinishReason,
    ModelRef,
    Preferences,
    Profile,
    UserMessage,
)
from morrow.interfaces.spike import consume_stream, eof_to_action
from morrow.runtime.agent import AgentRuntime
from morrow.runtime.session import Session
from morrow.testing import (
    FixedClock,
    FixedIdSource,
    ScriptedModelProvider,
    make_context_builder,
    seed_user_turn,
)


class RaisingStreamProvider:
    def __init__(self, *, partial: bool) -> None:
        self.partial = partial

    async def stream(self, model, messages):
        del model, messages
        if self.partial:
            yield ModelEvent(kind="text_delta", text="partial")
        raise RuntimeError("credential-sentinel provider exploded")

    async def complete(self, model, messages):
        raise AssertionError("complete should not be called")


class EventStreamProvider:
    def __init__(self, events) -> None:
        self.events = events

    async def stream(self, model, messages):
        del model, messages
        for event in self.events:
            yield event

    async def complete(self, model, messages):
        raise AssertionError("complete should not be called")


def test_context_contains_only_supported_profile_and_preferences_state():
    session = Session(session_id="s", profile=Profile(name="demo"))
    builder = make_context_builder()
    context = builder.build(session, purpose="structured")
    state = "\n".join(message.content for message in context.messages)
    assert "demo" in state
    assert '"handoff"' not in state
    assert "current_goal" not in state


def test_preferences_have_global_workspace_session_precedence():
    value = ContextBuilder.merge_preferences(
        Preferences(language="中文", instructions=["global", "same"]),
        Preferences(instructions=["workspace", "same"]),
        Preferences(language="English", instructions=["session", "same"]),
    )
    assert value.language == "English"
    assert value.instructions == ["global", "workspace", "session", "same"]


def test_context_never_keeps_assistant_without_its_paired_user():
    session = Session(session_id="s")
    seed_user_turn(session, "u" * 20, assistant="a")
    session.log.begin_turn(UserMessage(content="now"))
    probe = make_context_builder()
    mandatory = (*probe._system_messages(session), UserMessage(content="now"))
    builder = make_context_builder(probe.estimate_request_chars(mandatory, ()))

    context = builder.build(session)

    assert [
        (message.role, message.content) for message in context.messages if message.role != "system"
    ] == [("user", "now")]


def test_context_does_not_skip_newest_oversized_turn_to_admit_older_turn():
    session = Session(session_id="s")
    seed_user_turn(session, "old", assistant="ok")
    seed_user_turn(session, "n" * 10, assistant="a" * 10)
    session.log.begin_turn(UserMessage(content="now"))
    probe = make_context_builder()
    mandatory = (*probe._system_messages(session), UserMessage(content="now"))
    builder = make_context_builder(probe.estimate_request_chars(mandatory, ()))

    context = builder.build(session)

    assert [
        (message.role, message.content) for message in context.messages if message.role != "system"
    ] == [("user", "now")]


def test_context_retains_unmatched_cancelled_user_as_its_own_history_unit():
    session = Session(session_id="s")
    seed_user_turn(session, "cancelled request", finish=FinishReason.CANCELLED)
    seed_user_turn(session, "completed request", assistant="completed answer")

    session.log.begin_turn(UserMessage(content="now"))
    context = make_context_builder().build(session)

    assert [message.content for message in context.messages if message.role != "system"] == [
        "cancelled request",
        "completed request",
        "completed answer",
        "now",
    ]


def test_context_rejects_fixed_mandatory_content_over_budget():
    session = Session(session_id="s")
    probe = make_context_builder()
    fixed_size = probe.estimate_request_chars(probe._system_messages(session), ())

    with pytest.raises(ValueError):
        make_context_builder(fixed_size - 1).build(session, purpose="structured")


def test_context_builder_requires_explicit_limit_and_estimator():
    with pytest.raises(TypeError):
        ContextBuilder()


@pytest.mark.asyncio
async def test_runtime_emits_one_ordered_lifecycle_and_admits_complete_history():
    provider = ScriptedModelProvider([["hello", " world"]])
    ids = FixedIdSource()
    session = Session(session_id="session")
    runtime = AgentRuntime(
        provider,
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
        id_source=ids,
        clock=FixedClock(),
    )
    events = [event async for event in runtime.run_turn(session, "hi")]
    assert lifecycle_is_valid(events)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert [message.content for message in session.messages] == ["hi", "hello world"]


@pytest.mark.asyncio
async def test_runtime_default_ids_are_unique_within_and_across_turns():
    provider = ScriptedModelProvider(["first", "second"])
    session = Session(session_id="session")
    runtime = AgentRuntime(
        provider, ModelRef(provider_id="p", model_id="m"), make_context_builder()
    )

    first = [event async for event in runtime.run_turn(session, "one")]
    second = [event async for event in runtime.run_turn(session, "two")]
    events = [*first, *second]

    assert len({event.event_id for event in events}) == len(events)
    assert first[0].turn_id != second[0].turn_id


def test_one_application_id_source_drives_workspace_and_distinct_sessions(tmp_path):
    ids = FixedIdSource()
    project = tmp_path / "project"
    project.mkdir()
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
        id_source=ids,
    )

    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    first_app = build_session_application(
        app,
        identity,
        provider=ScriptedModelProvider(["first"]),
        model=ModelRef(provider_id="p", model_id="m"),
    )
    second_app = build_session_application(
        app,
        identity,
        provider=ScriptedModelProvider(["second"]),
        model=ModelRef(provider_id="p", model_id="m"),
    )

    assert identity.workspace_id == "ws_1"
    assert first_app.session.session_id == "ses_1"
    assert second_app.session.session_id == "ses_2"


@pytest.mark.asyncio
async def test_cancel_preserves_user_but_not_partial_assistant():
    provider = ScriptedModelProvider(["cancel"])
    session = Session(session_id="session")
    runtime = AgentRuntime(
        provider, ModelRef(provider_id="p", model_id="m"), make_context_builder()
    )
    task = asyncio.create_task(_collect(runtime.run_turn(session, "stop me")))
    await asyncio.sleep(0.01)
    task.cancel()
    events = await task
    assert events[-1].payload["finish_reason"] == FinishReason.CANCELLED.value
    assert [message.role for message in session.messages] == ["user"]


@pytest.mark.asyncio
async def test_conversation_succeeds_after_cancelled_turn():
    provider = ScriptedModelProvider(["cancel", "recovered"])
    session = Session(session_id="session")
    runtime = AgentRuntime(
        provider, ModelRef(provider_id="p", model_id="m"), make_context_builder()
    )
    task = asyncio.create_task(_collect(runtime.run_turn(session, "cancel this")))
    await asyncio.sleep(0.01)
    task.cancel()

    cancelled = await task
    recovered = await _collect(runtime.run_turn(session, "try again"))

    assert cancelled[-1].payload["finish_reason"] == FinishReason.CANCELLED.value
    assert recovered[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert [message.role for message in session.messages] == ["user", "user", "assistant"]


@pytest.mark.asyncio
async def test_ten_turns_preserve_ordered_full_history_and_stream_deltas():
    responses = [[f"answer-{index}-", "done"] for index in range(10)]
    provider = ScriptedModelProvider(responses)
    session = Session(session_id="session")
    runtime = AgentRuntime(
        provider,
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(100_000),
    )

    for index in range(10):
        events = await _collect(runtime.run_turn(session, f"request-{index}"))
        deltas = [event.payload["text"] for event in events if event.type == "text.delta"]
        assert deltas == [f"answer-{index}-", "done"]

    expected = [
        item for index in range(10) for item in (f"request-{index}", f"answer-{index}-done")
    ]
    assert [message.content for message in session.messages] == expected
    last_call = [
        message.content for message in provider.stream_calls[-1] if message.role != "system"
    ]
    assert last_call == expected[:-1]


@pytest.mark.asyncio
async def test_network_error_retries_once_before_visible_text():
    provider = ScriptedModelProvider([RuntimeError("network"), "recovered"])
    session = Session(session_id="session")
    runtime = AgentRuntime(
        provider,
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
    )
    events = [event async for event in runtime.run_turn(session, "retry")]
    assert [event.type for event in events].count("status.changed") == 1
    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert len(provider.stream_calls) == 2


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.asyncio
async def test_provider_exception_always_completes_error_without_assistant_history(partial):
    session = Session(session_id="session")
    runtime = AgentRuntime(
        RaisingStreamProvider(partial=partial),
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
    )

    events = [event async for event in runtime.run_turn(session, "raise")]

    assert lifecycle_is_valid(events)
    assert [event.type for event in events].count("turn.started") == 1
    assert [event.type for event in events].count("turn.completed") == 1
    assert events[-1].payload["finish_reason"] == FinishReason.ERROR.value
    assert [message.role for message in session.messages] == ["user"]
    assert "credential-sentinel" not in str([event.payload for event in events])


@pytest.mark.parametrize(
    "terminal_event",
    [
        ModelEvent(kind="completed", finish_reason=ModelFinishReason.LENGTH),
        ModelEvent(kind="completed", finish_reason=ModelFinishReason.CONTENT_FILTER),
        ModelEvent(kind="completed"),
        None,
    ],
)
@pytest.mark.asyncio
async def test_only_explicit_stop_finish_admits_assistant_history(terminal_event):
    model_events = [ModelEvent(kind="text_delta", text="partial")]
    if terminal_event is not None:
        model_events.append(terminal_event)
    session = Session(session_id="session")
    runtime = AgentRuntime(
        EventStreamProvider(model_events),
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
    )

    events = [event async for event in runtime.run_turn(session, "finish")]

    assert lifecycle_is_valid(events)
    assert events[-1].payload["finish_reason"] == FinishReason.ERROR.value
    assert [message.role for message in session.messages] == ["user"]


@pytest.mark.asyncio
async def test_empty_completion_is_error_without_empty_assistant_message():
    provider = ScriptedModelProvider([[]])
    session = Session(session_id="session")
    runtime = AgentRuntime(
        provider, ModelRef(provider_id="p", model_id="m"), make_context_builder()
    )

    events = [event async for event in runtime.run_turn(session, "empty")]

    assert events[-1].payload["finish_reason"] == FinishReason.ERROR.value
    assert [message.role for message in session.messages] == ["user"]


def test_oversized_current_input_is_rejected_before_model_call():
    provider = ScriptedModelProvider(["should not run"])
    session = Session(session_id="session")
    runtime = AgentRuntime(
        provider,
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(100),
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
