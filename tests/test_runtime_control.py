"""Durable runtime-control queue and safe delivery tests."""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.models.openai_compatible import estimate_request_chars
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.application.context import ContextBuilder
from morrow.application.runtime_control import RuntimeControlService
from morrow.bootstrap import build_application, build_session_application
from morrow.core.domain import DurableSession
from morrow.core.models import (
    AssistantMessage,
    FinishReason,
    FunctionToolCall,
    ModelErrorCode,
    ModelEvent,
    ModelFinishReason,
    ModelProviderError,
    ModelRef,
    ToolMessage,
)
from morrow.core.runtime_control import (
    RUNTIME_CONTROL_MAX_PENDING,
    RuntimeControlEntry,
    RuntimeControlError,
    RuntimeControlErrorCode,
    RuntimeControlKind,
    RuntimeControlStatus,
)
from morrow.runtime.agent import AgentLoop
from morrow.runtime.durable_log import restore_conversation_log
from morrow.runtime.policy import LongHorizonPolicySettings, load_agent_policy
from morrow.runtime.session import Session
from morrow.runtime.tools import ToolExecutor, ToolRegistry, make_tool
from morrow.testing import (
    FixedClock,
    FixedIdSource,
    ScriptedModelProvider,
    make_context_builder,
    make_run_policy,
)

MODEL = ModelRef(provider_id="p", model_id="m")


class _EchoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


def _echo_executor() -> ToolExecutor:
    async def handler(arguments: _EchoArguments) -> object:
        return {"echo": arguments.value}

    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="echo",
            description="echo",
            arguments_model=_EchoArguments,
            handler=handler,
        )
    )
    return ToolExecutor(registry.snapshot(), make_run_policy())


class _SteeringAfterPolls:
    def __init__(self, threshold: int) -> None:
        self.threshold = threshold
        self.polls = 0

    def peek_steering(self, _session_id: str):
        self.polls += 1
        return object() if self.polls >= self.threshold else None


class _MutableSteering:
    def __init__(self) -> None:
        self.pending = False

    def peek_steering(self, _session_id: str):
        return object() if self.pending else None


def _long_horizon_context() -> ContextBuilder:
    policy = load_agent_policy().resolve(
        MODEL,
        tool_protocol="openai_function",
        multiple_tool_calls=True,
        context_window_tokens=100_000,
        settings=LongHorizonPolicySettings(compaction_enabled=False),
    )
    return ContextBuilder(run_policy=policy, estimate_request_chars=estimate_request_chars)


class _TransientThenStopProvider:
    def __init__(self) -> None:
        self.stream_calls = 0

    async def stream(self, model, messages, tools=()):
        del model, messages, tools
        self.stream_calls += 1
        if self.stream_calls == 1:
            raise ModelProviderError(ModelErrorCode.RATE_LIMIT, "transient")
        yield ModelEvent(
            kind="completed",
            finish_reason=ModelFinishReason.STOP,
            message=AssistantMessage(content="done"),
        )


class _TerminalErrorProvider:
    async def stream(self, model, messages, tools=()):
        del model, messages, tools
        yield ModelEvent(
            kind="error",
            error_code=ModelErrorCode.AUTH,
            error_message="authentication failed",
        )


class _BlockingProvider:
    def __init__(self, responses: tuple[str, ...]) -> None:
        self.responses = responses
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.stream_calls: list[list] = []

    async def stream(self, model, messages, tools=()):
        del model, tools
        self.stream_calls.append(list(messages))
        index = len(self.stream_calls) - 1
        if index == 0:
            self.started.set()
            await self.release.wait()
        text = self.responses[min(index, len(self.responses) - 1)]
        yield ModelEvent(kind="text_delta", text=text)
        yield ModelEvent(
            kind="completed",
            finish_reason=ModelFinishReason.STOP,
            message=AssistantMessage(content=text),
        )


def _session_products(tmp_path: Path, provider):
    project = tmp_path / "project"
    project.mkdir()
    app = build_application(
        state_root=tmp_path / "app-state",
        credentials=MemoryCredentialStore(),
        id_source=FixedIdSource(),
    )
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    products = build_session_application(app, identity, provider=provider, model=MODEL)
    return identity, products


def _open(tmp_path: Path):
    store = OperationalStore(
        tmp_path / "state",
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0,
            sleep=lambda _delay: None,
            rng=random.Random(0),
        ),
        clock=FixedClock(),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle, id_source=FixedIdSource())
    journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
    return store, handle, journal


def test_runtime_control_model_rejects_blank_and_oversized_text() -> None:
    values = {
        "workspace_id": "ws_1",
        "session_id": "ses_1",
        "position": 1,
        "kind": RuntimeControlKind.STEER,
        "client_message_id": "cmsg_1",
    }
    with pytest.raises(ValidationError):
        RuntimeControlEntry(**values, text="   ")
    with pytest.raises(ValidationError):
        RuntimeControlEntry(**values, text="x" * 4097)


def test_runtime_control_queue_is_fifo_one_at_a_time_and_survives_reopen(tmp_path: Path) -> None:
    store, handle, journal = _open(tmp_path)
    clock = FixedClock()
    ids = FixedIdSource()
    service = RuntimeControlService(journal, workspace_id="ws_1", id_source=ids, clock=clock.now)
    first = service.enqueue_steering("ses_1", "correct course")
    follow = service.enqueue_follow_up("ses_1", "then explain")
    second = service.enqueue_steering("ses_1", "also run tests")

    assert service.peek_steering("ses_1") == first
    assert service.peek_follow_up("ses_1") == follow
    assert [entry.position for entry in journal.list_runtime_controls("ws_1", "ses_1")] == [
        1,
        2,
        3,
    ]
    journal.consume_runtime_control(
        "ws_1", "ses_1", first.client_message_id, consumed_at=clock.now()
    )
    assert service.peek_steering("ses_1") == second
    handle.close()

    with store.open("read_write") as reopened_handle:
        reopened = SqliteOperationalJournal(reopened_handle)
        pending = reopened.list_runtime_controls(
            "ws_1", "ses_1", status=RuntimeControlStatus.PENDING
        )
        assert [entry.text for entry in pending] == ["then explain", "also run tests"]


def test_runtime_control_queue_rejects_newest_after_32_pending(tmp_path: Path) -> None:
    _store, handle, journal = _open(tmp_path)
    try:
        service = RuntimeControlService(
            journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=FixedClock().now,
        )
        for index in range(RUNTIME_CONTROL_MAX_PENDING):
            service.enqueue_steering("ses_1", f"steer-{index}")
        with pytest.raises(RuntimeControlError) as error:
            service.enqueue_follow_up("ses_1", "rejected-newest")
        assert error.value.code is RuntimeControlErrorCode.QUEUE_FULL
        assert len(journal.list_runtime_controls("ws_1", "ses_1")) == 32
        assert service.peek_steering("ses_1").text == "steer-0"
    finally:
        handle.close()


def test_runtime_control_service_rejects_blank_and_oversized_before_write(
    tmp_path: Path,
) -> None:
    _store, handle, journal = _open(tmp_path)
    try:
        service = RuntimeControlService(
            journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=FixedClock().now,
        )
        for text in ("   ", "x" * 4097):
            with pytest.raises(RuntimeControlError) as error:
                service.enqueue_steering("ses_1", text)
            assert error.value.code is RuntimeControlErrorCode.INVALID
        assert journal.list_runtime_controls("ws_1", "ses_1") == ()
    finally:
        handle.close()


def test_runtime_control_enqueue_is_idempotent_and_conflicts_on_changed_payload(
    tmp_path: Path,
) -> None:
    _store, handle, journal = _open(tmp_path)
    try:
        stamp = FixedClock().now()
        values = {
            "session_id": "ses_1",
            "kind": RuntimeControlKind.STEER,
            "client_message_id": "cmsg_fixed",
            "text": "same",
            "created_at": stamp,
        }
        first = journal.enqueue_runtime_control("ws_1", **values)
        assert journal.enqueue_runtime_control("ws_1", **values) == first
        with pytest.raises(RuntimeControlError) as error:
            journal.enqueue_runtime_control("ws_1", **{**values, "text": "different"})
        assert error.value.code is RuntimeControlErrorCode.CONFLICT
    finally:
        handle.close()


def test_runtime_control_consumption_rolls_back_with_outer_transaction(tmp_path: Path) -> None:
    _store, handle, journal = _open(tmp_path)
    try:
        service = RuntimeControlService(
            journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=FixedClock().now,
        )
        entry = service.enqueue_steering("ses_1", "persist me")

        def work(txn) -> None:
            txn.consume_runtime_control(
                "ws_1", "ses_1", entry.client_message_id, consumed_at=FixedClock().now()
            )
            raise RuntimeError("rollback")

        with pytest.raises(RuntimeError, match="rollback"):
            journal.transact(work)
        assert service.peek_steering("ses_1") == entry
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_loop_start_steering_closes_before_any_model_request() -> None:
    provider = ScriptedModelProvider(["unused"])
    session = Session(session_id="s")
    events = [
        event
        async for event in AgentLoop(
            provider,
            MODEL,
            make_context_builder(),
            runtime_control=_SteeringAfterPolls(1),
        ).run_task(session, "start")
    ]

    assert provider.stream_calls == []
    assert events[-1].payload["finish_reason"] == FinishReason.STEERED.value
    turn = session.log.snapshot().public_turns(require_closed=True)[0]
    assert turn.final_assistant is None
    assert turn.terminal.finish_reason is FinishReason.STEERED


@pytest.mark.asyncio
async def test_pre_stop_steering_rejects_candidate_without_publishing_it() -> None:
    provider = ScriptedModelProvider(["obsolete answer"])
    session = Session(session_id="s")
    events = [
        event
        async for event in AgentLoop(
            provider,
            MODEL,
            make_context_builder(),
            runtime_control=_SteeringAfterPolls(2),
        ).run_task(session, "start")
    ]

    assert len(provider.stream_calls) == 1
    assert events[-1].payload["finish_reason"] == FinishReason.STEERED.value
    assert not any(
        event.type == "text.delta" and event.payload.get("text") == "obsolete answer"
        for event in events
    )
    assert [message.role for message in session.messages] == ["user"]


@pytest.mark.asyncio
async def test_post_batch_steering_waits_until_all_admitted_calls_are_closed() -> None:
    calls = (
        FunctionToolCall(id="call_1", name="echo", arguments=json.dumps({"value": "one"})),
        FunctionToolCall(id="call_2", name="echo", arguments=json.dumps({"value": "two"})),
    )
    provider = ScriptedModelProvider([AssistantMessage(tool_calls=calls), "unused"])
    session = Session(session_id="s")
    events = [
        event
        async for event in AgentLoop(
            provider,
            MODEL,
            make_context_builder(),
            tool_executor=_echo_executor(),
            runtime_control=_SteeringAfterPolls(2),
        ).run_task(session, "start")
    ]

    assert len(provider.stream_calls) == 1
    assert events[-1].payload["finish_reason"] == FinishReason.STEERED.value
    assert [
        message.tool_call_id for message in session.messages if isinstance(message, ToolMessage)
    ] == ["call_1", "call_2"]
    turn = session.log.snapshot().public_turns(require_closed=True)[0]
    assert turn.unresolved_call_ids == ()
    assert turn.terminal.interrupted_call_ids == ()


@pytest.mark.asyncio
async def test_orchestrator_steer_is_rejected_while_idle(tmp_path: Path) -> None:
    _identity, products = _session_products(tmp_path, ScriptedModelProvider(["done"]))
    try:
        with pytest.raises(RuntimeControlError) as error:
            await products.orchestrator.steer("not running")
        assert error.value.code is RuntimeControlErrorCode.IDLE
    finally:
        products.persistence.store_session.close()


@pytest.mark.asyncio
async def test_orchestrator_follow_up_while_idle_routes_to_ordinary_submission(
    tmp_path: Path,
) -> None:
    provider = ScriptedModelProvider(["ordinary"])
    _identity, products = _session_products(tmp_path, provider)
    try:
        result = await products.orchestrator.follow_up("idle follow-up")
        assert result.events[-1].payload["finish_reason"] == FinishReason.STOP.value
        assert provider.stream_calls[0][-1].content == "idle follow-up"
        assert (
            products.persistence.journal.list_runtime_controls(
                products.persistence.workspace_id, products.session.session_id
            )
            == ()
        )
    finally:
        products.persistence.store_session.close()


@pytest.mark.asyncio
async def test_midstream_steering_resubmits_as_durable_turn_and_consumes_atomically(
    tmp_path: Path,
) -> None:
    provider = _BlockingProvider(("obsolete", "corrected"))
    identity, products = _session_products(tmp_path, provider)
    try:
        dispatch = asyncio.create_task(products.orchestrator.dispatch("initial"))
        await provider.started.wait()
        queued = await products.orchestrator.steer("new direction")
        provider.release.set()
        result = await dispatch

        assert [
            event.payload["finish_reason"]
            for event in result.events
            if event.type == "turn.completed"
        ] == [FinishReason.STEERED.value, FinishReason.STOP.value]
        assert len(provider.stream_calls) == 2
        assert provider.stream_calls[1][-1].content == "new direction"
        stored = products.persistence.journal.get_runtime_control(
            identity.workspace_id,
            products.session.session_id,
            queued.client_message_id,
        )
        assert stored is not None
        assert stored.status is RuntimeControlStatus.CONSUMED
        assert stored.consumed_at is not None
        task_ids = {
            turn.task_run_id
            for turn in products.persistence.journal.list_session_turns(
                identity.workspace_id, products.session.session_id
            )
        }
        assert len(task_ids) == 1
        runs = products.persistence.journal.list_session_agent_runs(
            identity.workspace_id, products.session.session_id
        )
        first_observation = products.persistence.journal.get_agent_run_observation(
            identity.workspace_id, runs[0].agent_run_id
        )
        assert first_observation.terminal_metrics.finish_reason is FinishReason.STEERED
        restored = restore_conversation_log(
            products.persistence.journal,
            identity.workspace_id,
            products.session.session_id,
        )
        assert [
            turn.terminal.finish_reason
            for turn in restored.snapshot().public_turns(require_closed=True)
        ] == [FinishReason.STEERED, FinishReason.STOP]
        doctor = products.doctor.inspect(identity.workspace_id)
        assert doctor.health.value in {"ok", "needs_recovery"}
        backup = products.backup.create("runtime-control")
        assert backup.integrity_ok
        bundle = products.backup.store.layout.backups_dir / backup.bundle_name
        assert products.backup.verify(bundle).ok
    finally:
        products.persistence.store_session.close()


@pytest.mark.asyncio
async def test_consecutive_steering_is_polled_at_each_new_turn_loop_top(tmp_path: Path) -> None:
    provider = _BlockingProvider(("obsolete", "latest answer"))
    _identity, products = _session_products(tmp_path, provider)
    try:
        dispatch = asyncio.create_task(products.orchestrator.dispatch("initial"))
        await provider.started.wait()
        first = await products.orchestrator.steer("direction one")
        await products.orchestrator.steer("direction two")
        provider.release.set()
        result = await dispatch

        assert [
            event.payload["finish_reason"]
            for event in result.events
            if event.type == "turn.completed"
        ] == [
            FinishReason.STEERED.value,
            FinishReason.STEERED.value,
            FinishReason.STOP.value,
        ]
        assert len(provider.stream_calls) == 2
        assert [
            message.content for message in provider.stream_calls[-1] if message.role == "user"
        ] == [
            "initial",
            "direction one",
            "direction two",
        ]

        replay = [
            event
            async for event in products.orchestrator.runtime.run_turn(
                products.session,
                "direction one",
                client_message_id=first.client_message_id,
            )
        ]
        assert replay[-1].payload["finish_reason"] == FinishReason.STEERED.value
        assert any(
            event.type == "status.changed" and event.payload.get("status") == "steered"
            for event in replay
        )
        assert not any(event.type == "text.delta" for event in replay)
    finally:
        products.persistence.store_session.close()


@pytest.mark.asyncio
async def test_error_replay_uses_receipt_terminal_when_metrics_are_missing(tmp_path: Path) -> None:
    _identity, products = _session_products(tmp_path, _TerminalErrorProvider())
    client_message_id = "replay-error-crash"
    try:
        first = [
            event
            async for event in products.orchestrator.runtime.run_turn(
                products.session,
                "request",
                client_message_id=client_message_id,
            )
        ]
        assert first[-1].payload["stop_code"] == "provider_auth"
        products.persistence.store_session.run_write(
            lambda executor: executor.execute("DELETE FROM agent_run_terminal_metrics")
        )

        replay = [
            event
            async for event in products.orchestrator.runtime.run_turn(
                products.session,
                "request",
                client_message_id=client_message_id,
            )
        ]
        assert replay[-1].payload["finish_reason"] == FinishReason.ERROR.value
        assert replay[-1].payload["stop_code"] == "provider_auth"
    finally:
        products.persistence.store_session.close()


@pytest.mark.asyncio
async def test_old_cancelled_replay_does_not_borrow_a_newer_stop_turn(tmp_path: Path) -> None:
    provider = _BlockingProvider(("unused", "new answer"))
    _identity, products = _session_products(tmp_path, provider)
    try:
        cancelled_task = asyncio.create_task(
            _collect_events(
                products.orchestrator.runtime.run_turn(
                    products.session,
                    "cancel me",
                    client_message_id="replay-old-cancelled",
                )
            )
        )
        await provider.started.wait()
        cancelled_task.cancel()
        cancelled = await cancelled_task
        assert cancelled[-1].payload["finish_reason"] == FinishReason.CANCELLED.value
        products.persistence.store_session.run_write(
            lambda executor: executor.execute("DELETE FROM agent_run_terminal_metrics")
        )

        provider.release.set()
        newer = [
            event
            async for event in products.orchestrator.runtime.run_turn(
                products.session,
                "new request",
                client_message_id="newer-stop",
            )
        ]
        assert newer[-1].payload["finish_reason"] == FinishReason.STOP.value

        replay = [
            event
            async for event in products.orchestrator.runtime.run_turn(
                products.session,
                "cancel me",
                client_message_id="replay-old-cancelled",
            )
        ]
        assert replay[-1].payload["finish_reason"] == FinishReason.CANCELLED.value
        assert not any(event.type == "text.delta" for event in replay)
    finally:
        products.persistence.store_session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", (FinishReason.ERROR, FinishReason.CANCELLED))
async def test_closed_replay_preserves_non_stop_terminal_reason(
    tmp_path: Path,
    terminal: FinishReason,
) -> None:
    provider = (
        _TerminalErrorProvider()
        if terminal is FinishReason.ERROR
        else _BlockingProvider(("unused",))
    )
    _identity, products = _session_products(tmp_path, provider)
    client_message_id = f"replay-{terminal.value}"
    try:
        if terminal is FinishReason.ERROR:
            first = [
                event
                async for event in products.orchestrator.runtime.run_turn(
                    products.session,
                    "request",
                    client_message_id=client_message_id,
                )
            ]
        else:
            task = asyncio.create_task(
                _collect_events(
                    products.orchestrator.runtime.run_turn(
                        products.session,
                        "request",
                        client_message_id=client_message_id,
                    )
                )
            )
            await provider.started.wait()
            task.cancel()
            first = await task
        assert first[-1].payload["finish_reason"] == terminal.value

        replay = [
            event
            async for event in products.orchestrator.runtime.run_turn(
                products.session,
                "request",
                client_message_id=client_message_id,
            )
        ]
        assert replay[-1].payload["finish_reason"] == terminal.value
    finally:
        products.persistence.store_session.close()


@pytest.mark.asyncio
async def test_active_follow_up_drains_after_normal_stop_in_fifo_order(tmp_path: Path) -> None:
    provider = _BlockingProvider(("first answer", "second answer", "third answer"))
    identity, products = _session_products(tmp_path, provider)
    try:
        dispatch = asyncio.create_task(products.orchestrator.dispatch("initial"))
        await provider.started.wait()
        first = await products.orchestrator.follow_up("follow one")
        second = await products.orchestrator.follow_up("follow two")
        provider.release.set()
        result = await dispatch

        assert [
            event.payload["finish_reason"]
            for event in result.events
            if event.type == "turn.completed"
        ] == [FinishReason.STOP.value] * 3
        assert [messages[-1].content for messages in provider.stream_calls] == [
            "initial",
            "follow one",
            "follow two",
        ]
        stored = products.persistence.journal.list_runtime_controls(
            identity.workspace_id, products.session.session_id
        )
        assert [entry.client_message_id for entry in stored] == [
            first.client_message_id,
            second.client_message_id,
        ]
        assert all(entry.status is RuntimeControlStatus.CONSUMED for entry in stored)
    finally:
        products.persistence.store_session.close()


@pytest.mark.asyncio
async def test_mismatched_queue_delivery_rolls_back_turn_and_keeps_entry_pending(
    tmp_path: Path,
) -> None:
    identity, products = _session_products(tmp_path, ScriptedModelProvider(["unused"]))
    try:
        entry = products.orchestrator.runtime_control.enqueue_steering(
            products.session.session_id, "expected"
        )
        events = [
            event
            async for event in products.orchestrator.runtime.run_turn(
                products.session,
                "different",
                client_message_id=entry.client_message_id,
            )
        ]
        assert events[-1].payload["finish_reason"] == FinishReason.ERROR.value
        assert (
            products.persistence.journal.get_receipt(
                identity.workspace_id,
                products.session.session_id,
                entry.client_message_id,
            )
            is None
        )
        assert (
            products.orchestrator.runtime_control.peek_steering(products.session.session_id)
            == entry
        )
    finally:
        products.persistence.store_session.close()


@pytest.mark.asyncio
async def test_steering_during_retry_backoff_waits_for_the_next_safe_point() -> None:
    provider = _TransientThenStopProvider()
    control = _MutableSteering()
    sleep_started = asyncio.Event()
    release_sleep = asyncio.Event()

    async def retry_sleep(_delay: float) -> None:
        sleep_started.set()
        await release_sleep.wait()

    loop = AgentLoop(
        provider,
        MODEL,
        _long_horizon_context(),
        runtime_control=control,
        retry_sleep=retry_sleep,
    )
    task = asyncio.create_task(_collect_events(loop.run_task(Session(session_id="s"), "start")))
    await sleep_started.wait()
    control.pending = True
    release_sleep.set()
    events = await task

    assert provider.stream_calls == 1
    assert events[-1].payload["finish_reason"] == FinishReason.STEERED.value


@pytest.mark.asyncio
async def test_cancel_during_retry_backoff_remains_immediate() -> None:
    provider = _TransientThenStopProvider()
    sleep_started = asyncio.Event()

    async def retry_sleep(_delay: float) -> None:
        sleep_started.set()
        await asyncio.Future()

    session = Session(session_id="s")
    loop = AgentLoop(
        provider,
        MODEL,
        _long_horizon_context(),
        retry_sleep=retry_sleep,
    )
    task = asyncio.create_task(_collect_events(loop.run_task(session, "start")))
    await sleep_started.wait()
    task.cancel()
    events = await task

    assert provider.stream_calls == 1
    assert events[-1].payload["finish_reason"] == FinishReason.CANCELLED.value
    assert session.log.snapshot().public_turns(require_closed=True)[0].unresolved_call_ids == ()


@pytest.mark.asyncio
async def test_cancel_preserves_pending_controls_for_the_next_user_run(tmp_path: Path) -> None:
    provider = _BlockingProvider(("cancelled", "steered answer", "follow-up answer"))
    identity, products = _session_products(tmp_path, provider)
    try:
        active = asyncio.create_task(products.orchestrator.dispatch("initial"))
        await provider.started.wait()
        steering = await products.orchestrator.steer("survive cancel")
        follow_up = await products.orchestrator.follow_up("after recovery")
        active.cancel()
        cancelled = await active
        assert cancelled.events[-1].payload["finish_reason"] == FinishReason.CANCELLED.value
        pending = products.persistence.journal.list_runtime_controls(
            identity.workspace_id,
            products.session.session_id,
            status=RuntimeControlStatus.PENDING,
        )
        assert [entry.client_message_id for entry in pending] == [
            steering.client_message_id,
            follow_up.client_message_id,
        ]

        resumed = await products.orchestrator.dispatch("resume")
        assert [
            event.payload["finish_reason"]
            for event in resumed.events
            if event.type == "turn.completed"
        ] == [
            FinishReason.STEERED.value,
            FinishReason.STOP.value,
            FinishReason.STOP.value,
        ]
        entries = products.persistence.journal.list_runtime_controls(
            identity.workspace_id, products.session.session_id
        )
        assert all(entry.status is RuntimeControlStatus.CONSUMED for entry in entries)
    finally:
        products.persistence.store_session.close()


async def _collect_events(iterator):
    return [event async for event in iterator]
