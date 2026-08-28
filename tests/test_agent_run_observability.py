"""Focused S7P-01 tests for safe AgentRun request and terminal observations."""

from __future__ import annotations

import asyncio
import json
import random
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.application.backup import OperationalBackupService
from morrow.application.doctor import OperationalDoctor
from morrow.application.local_tools import BASH_PROVIDER_SCHEMA, BashArguments
from morrow.application.tool_persistence import _envelope_from_outcome
from morrow.application.turns import SessionPersistence
from morrow.core.capabilities import ProcessIsolation
from morrow.core.domain import DurableSession
from morrow.core.execution import ToolExecutionDisposition, ToolExecutionState, tool_declaration
from morrow.core.models import (
    AgentStopCode,
    AssistantMessage,
    FinishReason,
    FunctionToolCall,
    ModelCost,
    ModelErrorCode,
    ModelEvent,
    ModelFinishReason,
    ModelRef,
    ModelUsage,
    UsageAvailability,
)
from morrow.core.store import StorageError, StorageErrorCode, StoreOpenMode
from morrow.runtime.agent import AgentLoop
from morrow.runtime.ids import RandomIdSource
from morrow.runtime.session import Session
from morrow.runtime.tools import (
    ToolErrorCode,
    ToolExecutionOutcome,
    ToolExecutor,
    ToolRegistry,
    make_tool,
)
from morrow.testing import FixedClock, FixedIdSource, ScriptedModelProvider, make_context_builder


def _retry() -> BusyRetryPolicy:
    return BusyRetryPolicy(busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0))


def _open(tmp_path: Path, *, skill_usage=None):
    clock = FixedClock()
    store = OperationalStore(
        tmp_path / "state",
        retry_policy=_retry(),
        clock=clock,
        maintenance_timeout=0,
    )
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    session = Session(session_id="ses_1")
    persistence = SessionPersistence(
        workspace_id="ws_1",
        journal=journal,
        store_session=handle,
        id_source=FixedIdSource(),
        model=ModelRef(provider_id="p", model_id="m"),
        run_policy=make_context_builder().run_policy,
        runtime_instance_id="host-1",
        clock=clock,
        skill_usage=skill_usage,
    )
    journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
    persistence.attach(session)
    return handle, journal, session, persistence


def test_terminal_agent_run_records_selected_skill_usage_best_effort(tmp_path):
    class RecordingSkillUsage:
        def __init__(self) -> None:
            self.terminals = []

        def record_terminal(self, terminal) -> None:
            self.terminals.append(terminal)

    skill_usage = RecordingSkillUsage()
    handle, _journal, session, persistence = _open(tmp_path, skill_usage=skill_usage)
    try:
        persistence.submit_user(
            session,
            "use the selected skill",
            "cmsg_1",
            turn_id="turn_1",
            agent_run_id="arun_1",
        )

        terminal = persistence.finalize_agent_run(
            finish_reason=FinishReason.STOP,
            model_attempts=1,
        )

        assert skill_usage.terminals == [terminal]
    finally:
        handle.close()


def test_fresh_v17_schema_contains_observation_tables(tmp_path):
    handle, _journal, _session, _persistence = _open(tmp_path)
    try:
        objects = handle.run_read(
            lambda executor: executor.execute(
                "SELECT type, name FROM sqlite_master WHERE name IN ("
                "'agent_run_model_requests', 'agent_run_terminal_metrics') ORDER BY name"
            )
        )
        assert objects == (
            ("table", "agent_run_model_requests"),
            ("table", "agent_run_terminal_metrics"),
        )
    finally:
        handle.close()


def test_agent_run_observation_admission_settlement_and_safe_terminal_projection(tmp_path):
    handle, journal, session, persistence = _open(tmp_path)
    try:
        accepted = persistence.submit_user(
            session,
            "hello",
            "cmsg_1",
            turn_id="turn_1",
            agent_run_id="arun_1",
        )
        assert accepted.kind == "accepted"
        assert persistence.current_agent_run_id == "arun_1"

        admitted = persistence.admit_model_request(
            agent_run_id="arun_1",
            attempt_ordinal=1,
            estimated_request_chars=321,
            request_char_budget=1000,
            cleared_cycle_count=2,
            dropped_turn_count=1,
            dropped_cycle_count=3,
            dropped_record_count=8,
            tool_rounds=1,
            tool_calls=2,
        )
        assert admitted.state.value == "admitted"
        assert admitted.attempt_ordinal == 1
        assert admitted.settled_at is None

        settled = persistence.settle_model_request(
            admitted.model_request_id,
            state="completed",
            finish_reason=ModelFinishReason.STOP,
            usage=ModelUsage(
                availability=UsageAvailability.AVAILABLE,
                input_tokens=7,
                output_tokens=5,
                total_tokens=12,
            ),
        )
        assert settled.state.value == "completed"
        assert settled.usage.total_tokens == 12

        terminal = persistence.finalize_agent_run(
            agent_run_id="arun_1",
            finish_reason=FinishReason.STOP,
            stop_code=None,
            model_attempts=1,
            retry_count=0,
            tool_rounds=1,
            tool_calls=2,
            cleared_cycle_count=2,
            dropped_turn_count=1,
            dropped_cycle_count=3,
            dropped_record_count=8,
        )
        assert terminal.finish_reason is FinishReason.STOP
        assert terminal.usage.total_tokens == 12

        inspection = persistence.get_agent_run_observation("arun_1")
        assert inspection.agent_run_id == "arun_1"
        assert [row.attempt_ordinal for row in inspection.requests] == [1]
        encoded = json.dumps(inspection.model_dump(mode="json"), ensure_ascii=False)
        assert "hello" not in encoded
        assert inspection.requests[0].prompt_evidence is None
        assert "project_instructions" not in encoded
        assert "argument" not in encoded
        assert "result" not in encoded
    finally:
        handle.close()


def test_agent_run_observation_settlement_and_terminal_finalization_are_idempotent(tmp_path):
    handle, _journal, session, persistence = _open(tmp_path)
    try:
        persistence.submit_user(
            session,
            "hello",
            "cmsg_1",
            turn_id="turn_1",
            agent_run_id="arun_1",
        )
        admitted = persistence.admit_model_request(
            agent_run_id="arun_1",
            attempt_ordinal=1,
            estimated_request_chars=10,
            request_char_budget=100,
        )
        first = persistence.settle_model_request(
            admitted.model_request_id,
            state="failed",
            error_code=ModelErrorCode.INVALID_RESPONSE,
        )
        second = persistence.settle_model_request(
            admitted.model_request_id,
            state="failed",
            error_code=ModelErrorCode.INVALID_RESPONSE,
        )
        assert second == first

        first_terminal = persistence.finalize_agent_run(
            agent_run_id="arun_1",
            finish_reason=FinishReason.ERROR,
            stop_code=AgentStopCode.INVALID_RESPONSE,
            model_attempts=1,
            retry_count=0,
            tool_rounds=0,
            tool_calls=0,
        )
        second_terminal = persistence.finalize_agent_run(
            agent_run_id="arun_1",
            finish_reason=FinishReason.ERROR,
            stop_code=AgentStopCode.INVALID_RESPONSE,
            model_attempts=1,
            retry_count=0,
            tool_rounds=0,
            tool_calls=0,
        )
        assert second_terminal == first_terminal
    finally:
        handle.close()


def test_agent_run_observation_idempotency_ignores_retry_timestamps(tmp_path):
    handle, _journal, session, persistence = _open(tmp_path)
    try:
        persistence.submit_user(
            session,
            "hello",
            "cmsg_1",
            turn_id="turn_1",
            agent_run_id="arun_1",
        )
        admitted = persistence.admit_model_request(
            agent_run_id="arun_1",
            attempt_ordinal=1,
            estimated_request_chars=10,
            request_char_budget=100,
        )
        persistence.clock.value = persistence.clock.value + timedelta(seconds=1)
        settled = persistence.settle_model_request(
            admitted.model_request_id,
            state="completed",
            finish_reason=ModelFinishReason.STOP,
        )
        persistence.clock.value = persistence.clock.value + timedelta(seconds=1)
        repeated_settlement = persistence.settle_model_request(
            admitted.model_request_id,
            state="completed",
            finish_reason=ModelFinishReason.STOP,
        )
        assert repeated_settlement == settled

        first_terminal = persistence.finalize_agent_run(
            agent_run_id="arun_1",
            finish_reason=FinishReason.STOP,
            model_attempts=1,
        )
        persistence.clock.value = persistence.clock.value + timedelta(seconds=1)
        repeated_terminal = persistence.finalize_agent_run(
            agent_run_id="arun_1",
            finish_reason=FinishReason.STOP,
            model_attempts=1,
        )
        assert repeated_terminal == first_terminal
    finally:
        handle.close()


def test_agent_run_usage_aggregation_marks_incompatible_partial_usage_unavailable(tmp_path):
    handle, _journal, session, persistence = _open(tmp_path)
    try:
        persistence.submit_user(
            session,
            "hello",
            "cmsg_1",
            turn_id="turn_1",
            agent_run_id="arun_1",
        )
        first = persistence.admit_model_request(
            agent_run_id="arun_1",
            attempt_ordinal=1,
            estimated_request_chars=10,
            request_char_budget=100,
        )
        second = persistence.admit_model_request(
            agent_run_id="arun_1",
            attempt_ordinal=2,
            estimated_request_chars=10,
            request_char_budget=100,
        )
        persistence.settle_model_request(
            first.model_request_id,
            state="completed",
            finish_reason=ModelFinishReason.STOP,
            usage=ModelUsage(
                availability=UsageAvailability.AVAILABLE,
                input_tokens=3,
            ),
        )
        persistence.settle_model_request(
            second.model_request_id,
            state="completed",
            finish_reason=ModelFinishReason.STOP,
            usage=ModelUsage(
                availability=UsageAvailability.AVAILABLE,
                output_tokens=4,
            ),
        )

        terminal = persistence.finalize_agent_run(
            agent_run_id="arun_1", finish_reason=FinishReason.STOP, model_attempts=2
        )

        assert terminal.usage.availability is UsageAvailability.UNAVAILABLE
    finally:
        handle.close()


def test_agent_run_observation_rejects_illegal_transition_and_cross_workspace_access(tmp_path):
    handle, journal, session, persistence = _open(tmp_path)
    try:
        persistence.submit_user(
            session,
            "hello",
            "cmsg_1",
            turn_id="turn_1",
            agent_run_id="arun_1",
        )
        admitted = persistence.admit_model_request(
            agent_run_id="arun_1",
            attempt_ordinal=1,
            estimated_request_chars=10,
            request_char_budget=100,
        )
        with pytest.raises(StorageError) as error:
            persistence.finalize_agent_run(
                agent_run_id="arun_1", finish_reason=FinishReason.STOP, model_attempts=1
            )
        assert error.value.code is StorageErrorCode.UNAVAILABLE
        with pytest.raises(StorageError) as error:
            journal.admit_model_request(
                "ws_2",
                agent_run_id="arun_1",
                attempt_ordinal=1,
                estimated_request_chars=10,
                request_char_budget=100,
            )
        assert error.value.code is StorageErrorCode.NOT_FOUND
        persistence.settle_model_request(admitted.model_request_id, state="cancelled")
    finally:
        handle.close()


def test_agent_run_observation_rejects_incomplete_terminal_facts(tmp_path):
    handle, _journal, session, persistence = _open(tmp_path)
    try:
        persistence.submit_user(
            session,
            "hello",
            "cmsg_1",
            turn_id="turn_1",
            agent_run_id="arun_1",
        )
        admitted = persistence.admit_model_request(
            agent_run_id="arun_1",
            attempt_ordinal=1,
            estimated_request_chars=10,
            request_char_budget=100,
        )
        with pytest.raises(StorageError) as error:
            persistence.settle_model_request(admitted.model_request_id, state="completed")
        assert error.value.code is StorageErrorCode.UNAVAILABLE

        with pytest.raises(StorageError) as error:
            persistence.settle_model_request(admitted.model_request_id, state="failed")
        assert error.value.code is StorageErrorCode.UNAVAILABLE

        persistence.settle_model_request(admitted.model_request_id, state="cancelled")
        with pytest.raises(StorageError) as error:
            persistence.finalize_agent_run(
                agent_run_id="arun_1",
                finish_reason=FinishReason.STOP,
                stop_code=AgentStopCode.INTERNAL,
                model_attempts=1,
            )
        assert error.value.code is StorageErrorCode.UNAVAILABLE
    finally:
        handle.close()


def test_model_cost_source_rejects_credential_like_values():
    with pytest.raises(ValueError):
        ModelCost(
            availability=UsageAvailability.AVAILABLE,
            amount_minor=0,
            currency="USD",
            source="token=sk-" + "a" * 20,
        )


def test_agent_run_observation_tampered_typed_json_fails_closed(tmp_path):
    handle, _journal, session, persistence = _open(tmp_path)
    try:
        persistence.submit_user(
            session,
            "hello",
            "cmsg_1",
            turn_id="turn_1",
            agent_run_id="arun_1",
        )
        persistence.finalize_agent_run(
            agent_run_id="arun_1", finish_reason=FinishReason.STOP, model_attempts=0
        )
        handle.run_write(
            lambda executor: executor.execute(
                "UPDATE agent_run_terminal_metrics SET tool_terminal_counts_json = ? "
                "WHERE agent_run_id = ?",
                ('{"succeeded":0}', "arun_1"),
            )
        )
        with pytest.raises(StorageError) as error:
            persistence.get_agent_run_observation("arun_1")
        assert error.value.code is StorageErrorCode.NEEDS_REPAIR
    finally:
        handle.close()


def test_doctor_reports_agent_run_observation_counts_without_writing(tmp_path):
    handle, _journal, session, persistence = _open(tmp_path)
    try:
        persistence.submit_user(
            session,
            "hello",
            "cmsg_1",
            turn_id="turn_1",
            agent_run_id="arun_1",
        )
        persistence.finalize_agent_run(
            agent_run_id="arun_1", finish_reason=FinishReason.STOP, model_attempts=0
        )
    finally:
        handle.close()

    report = OperationalDoctor(OperationalStore(tmp_path / "state")).inspect("ws_1")

    assert "agent_run_observations" in report.checks
    assert report.counts["agent_run_observations"] == 1
    assert report.counts["agent_run_terminal_metrics"] == 1
    assert report.counts["agent_run_open_model_requests"] == 0


def test_backup_carries_agent_run_observation_rows(tmp_path):
    handle, journal, session, persistence = _open(tmp_path)
    try:
        persistence.submit_user(
            session,
            "backup me",
            "cmsg_1",
            turn_id="turn_1",
            agent_run_id="arun_1",
        )
        admitted = persistence.admit_model_request(
            agent_run_id="arun_1",
            attempt_ordinal=1,
            estimated_request_chars=10,
            request_char_budget=100,
        )
        persistence.settle_model_request(
            admitted.model_request_id,
            state="completed",
            finish_reason=ModelFinishReason.STOP,
        )
        persistence.finalize_agent_run(
            agent_run_id="arun_1", finish_reason=FinishReason.STOP, model_attempts=1
        )

        store = OperationalStore(tmp_path / "state", clock=FixedClock())
        backup = OperationalBackupService(store, journal=journal)
        report = backup.create("observability")
        bundle = store.layout.backups_dir / report.bundle_name

        assert backup.verify(bundle).ok
        with sqlite3.connect(bundle / "database.sqlite") as connection:
            rows = connection.execute(
                "SELECT COUNT(*), (SELECT COUNT(*) FROM agent_run_terminal_metrics) "
                "FROM agent_run_model_requests"
            ).fetchone()
        assert rows == (1, 1)
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_agent_loop_observation_records_retry_attempts(tmp_path):
    handle, _journal, session, persistence = _open(tmp_path)
    try:
        provider = ScriptedModelProvider([RuntimeError("transient"), "done"])
        loop = AgentLoop(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            id_source=FixedIdSource(),
            clock=FixedClock(),
        )

        events = [event async for event in loop.run_task(session, "retry once")]

        assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
        observation = persistence.get_agent_run_observation()
        assert observation is not None
        assert [item.attempt_ordinal for item in observation.requests] == [1, 2]
        assert [item.state.value for item in observation.requests] == ["failed", "completed"]
        assert observation.terminal_metrics is not None
        assert observation.terminal_metrics.model_attempts == 2
        assert observation.terminal_metrics.retry_count == 1
        assert observation.retry_progress is not None
        assert observation.retry_progress.total_retry_count == 1
        assert observation.retry_progress.consecutive_model_retries == 0
    finally:
        handle.close()


def test_retry_progress_is_bounded_and_monotonic(tmp_path):
    handle, _journal, session, persistence = _open(tmp_path)
    try:
        persistence.submit_user(
            session,
            "hello",
            "cmsg_1",
            turn_id="turn_1",
            agent_run_id="arun_1",
        )
        saved = persistence.record_retry_progress(
            agent_run_id="arun_1",
            consecutive_model_retries=1,
            total_retry_count=2,
            summary_retry_count=1,
        )
        assert saved.total_retry_count == 2
        assert persistence.get_agent_run_observation("arun_1").retry_progress == saved

        with pytest.raises(StorageError) as error:
            persistence.record_retry_progress(
                agent_run_id="arun_1",
                consecutive_model_retries=0,
                total_retry_count=1,
                summary_retry_count=1,
            )
        assert error.value.code is StorageErrorCode.UNAVAILABLE
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_agent_loop_cancellation_settles_the_admitted_request(tmp_path):
    class BlockingProvider:
        def __init__(self) -> None:
            self.entered = asyncio.Event()

        async def stream(self, _model, _messages, _tools=()):
            self.entered.set()
            await asyncio.Event().wait()
            yield  # pragma: no cover

    handle, _journal, session, persistence = _open(tmp_path)
    try:
        provider = BlockingProvider()
        loop = AgentLoop(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            id_source=FixedIdSource(),
            clock=FixedClock(),
        )
        task = asyncio.create_task(_collect_events(loop.run_task(session, "cancel this")))
        await provider.entered.wait()
        task.cancel()
        events = await task

        assert events[-1].payload["finish_reason"] == FinishReason.CANCELLED.value
        observation = persistence.get_agent_run_observation()
        assert observation is not None
        assert observation.requests[0].state.value == "cancelled"
        assert observation.terminal_metrics is not None
        assert observation.terminal_metrics.finish_reason is FinishReason.CANCELLED
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_agent_loop_context_budget_error_still_finalizes_the_agent_run(tmp_path):
    handle, _journal, session, persistence = _open(tmp_path)
    try:
        loop = AgentLoop(
            ScriptedModelProvider(["unreachable"]),
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(request_char_limit=64),
            id_source=FixedIdSource(),
            clock=FixedClock(),
        )

        events = [event async for event in loop.run_task(session, "too long")]

        assert events[-1].payload["finish_reason"] == FinishReason.ERROR.value
        assert events[-1].payload["stop_code"] == AgentStopCode.CONTEXT_BUDGET.value
        observation = persistence.get_agent_run_observation()
        assert observation is not None
        assert observation.requests == ()
        assert observation.terminal_metrics is not None
        assert observation.terminal_metrics.finish_reason is FinishReason.ERROR
        assert observation.terminal_metrics.stop_code is AgentStopCode.CONTEXT_BUDGET
        assert observation.terminal_metrics.model_attempts == 0
    finally:
        handle.close()


async def _collect_events(aiter):
    return [item async for item in aiter]


def test_unsettled_model_request_is_visible_after_reopen(tmp_path):
    handle, _journal, session, persistence = _open(tmp_path)
    persistence.submit_user(
        session,
        "hello",
        "cmsg_1",
        turn_id="turn_1",
        agent_run_id="arun_1",
    )
    admitted = persistence.admit_model_request(
        agent_run_id="arun_1",
        attempt_ordinal=1,
        estimated_request_chars=10,
        request_char_budget=100,
    )
    assert admitted.state.value == "admitted"
    handle.close()

    with OperationalStore(
        tmp_path / "state", retry_policy=_retry(), clock=FixedClock(), maintenance_timeout=0
    ).open(StoreOpenMode.READ_ONLY) as reopened:
        inspection = SqliteOperationalJournal(reopened).get_agent_run_observation("ws_1", "arun_1")
        assert inspection.requests[0].state.value == "admitted"
        assert inspection.terminal_metrics is None


def test_invalid_argument_diagnostics_retain_only_bounded_path_and_type():
    outcome = ToolExecutionOutcome(
        call_id="call_1",
        name="validate",
        ok=False,
        envelope=json.dumps(
            {
                "ok": False,
                "error": {
                    "code": "invalid_arguments",
                    "message": "bad value",
                    "details": [
                        {"path": "first", "type": "int_parsing", "value": "secret"},
                        {"path": "api_key", "type": "string_type"},
                        {"path": "second", "type": "missing"},
                    ],
                },
            }
        ),
        error_code=ToolErrorCode.INVALID_ARGUMENTS,
    )

    envelope = _envelope_from_outcome(outcome)
    assert [(item.path, item.type) for item in envelope.validation_diagnostics] == [
        ("first", "int_parsing"),
        ("second", "missing"),
    ]
    encoded = json.dumps(envelope.model_dump(mode="json"), ensure_ascii=False)
    assert "secret" not in encoded
    assert "value" not in encoded


@pytest.mark.asyncio
async def test_scripted_agent_repairs_command_shape_without_durable_raw_sentinel(tmp_path):
    async def handler(arguments: BashArguments):
        return {"status": "executed", "command": arguments.command}

    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="bash",
            description="run command",
            arguments_model=BashArguments,
            provider_schema=BASH_PROVIDER_SCHEMA,
            handler=handler,
            recovery_declaration=tool_declaration("bash", process_isolation=ProcessIsolation.HOST),
        )
    )
    executor = ToolExecutor(registry.snapshot(), make_context_builder().run_policy)
    handle, journal, session, persistence = _open(tmp_path)
    sentinel = "raw-command-sentinel"
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="bad",
                        name="bash",
                        arguments=json.dumps({"command": {"value": sentinel}}),
                    ),
                )
            ),
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="good",
                        name="bash",
                        arguments='{"command":"echo"}',
                    ),
                )
            ),
            AssistantMessage(content="done"),
        ]
    )
    try:
        loop = AgentLoop(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            id_source=FixedIdSource(),
            clock=FixedClock(),
            tool_executor=executor,
        )
        events = [event async for event in loop.run_task(session, "run it")]

        assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
        first_tool_message = provider.stream_calls[1][-1]
        first_error = json.loads(first_tool_message.content)
        assert first_error["error"]["code"] == ToolErrorCode.INVALID_ARGUMENTS.value
        assert sentinel not in first_tool_message.content
        assert sentinel not in json.dumps([event.payload for event in events], ensure_ascii=False)

        observation = persistence.get_agent_run_observation()
        assert observation is not None
        executions = journal.list_executions("ws_1", agent_run_id=observation.agent_run_id)
        assert len(executions) == 2
        invalid_envelope = executions[0].result_envelope
        assert invalid_envelope is not None
        assert invalid_envelope.validation_diagnostics
        assert sentinel not in json.dumps(invalid_envelope.model_dump(mode="json"))
        assert json.loads(provider.stream_calls[2][-1].content)["result"] == {
            "command": "echo",
            "status": "executed",
        }
    finally:
        handle.close()


@pytest.mark.parametrize(
    "envelope",
    [
        {
            "ok": True,
            "error": {
                "code": "invalid_arguments",
                "details": [{"path": "count", "type": "int_type"}],
            },
        },
        {
            "ok": False,
            "error": {
                "code": "internal",
                "details": [{"path": "count", "type": "int_type"}],
            },
        },
        {"ok": False, "error": {"code": "invalid_arguments", "details": "not-a-list"}},
    ],
)
def test_invalid_argument_diagnostics_require_a_matching_error_envelope(envelope):
    outcome = ToolExecutionOutcome(
        call_id="call_1",
        name="validate",
        ok=bool(envelope["ok"]),
        envelope=json.dumps(envelope),
        error_code=ToolErrorCode.INVALID_ARGUMENTS,
    )

    assert _envelope_from_outcome(outcome).validation_diagnostics == ()


@pytest.mark.asyncio
async def test_agent_loop_persists_invalid_argument_diagnostics_without_values(tmp_path):
    class StrictArguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

        count: int

    async def handler(arguments: StrictArguments):
        return {"count": arguments.count}

    builder = make_context_builder()
    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="strict_count",
            description="strict count",
            arguments_model=StrictArguments,
            handler=handler,
        )
    )
    executor = ToolExecutor(registry.snapshot(), builder.run_policy)
    handle, journal, session, persistence = _open(tmp_path)
    try:
        provider = ScriptedModelProvider(
            [
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(
                            id="call_1",
                            name="strict_count",
                            arguments='{"count":"secret"}',
                        ),
                    )
                ),
                AssistantMessage(content="done"),
            ]
        )
        loop = AgentLoop(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            builder,
            id_source=FixedIdSource(),
            clock=FixedClock(),
            tool_executor=executor,
        )

        events = [event async for event in loop.run_task(session, "validate")]

        assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
        observation = persistence.get_agent_run_observation()
        assert observation is not None
        executions = journal.list_executions("ws_1", agent_run_id=observation.agent_run_id)
        assert len(executions) == 1
        envelope = executions[0].result_envelope
        assert envelope is not None
        assert envelope.validation_diagnostics
        assert envelope.validation_diagnostics[0].path == "count"
        assert envelope.validation_diagnostics[0].type == "int_type"
        encoded = json.dumps(executions[0].model_dump(mode="json"), ensure_ascii=False)
        assert "secret" not in encoded
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_agent_loop_records_one_durable_request_and_terminal_metrics(tmp_path):
    handle, _journal, session, persistence = _open(tmp_path)
    try:
        provider = ScriptedModelProvider(["done"])
        loop = AgentLoop(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            id_source=FixedIdSource(),
            clock=FixedClock(),
        )

        events = [event async for event in loop.run_task(session, "question")]

        assert events[-1].type == "turn.completed"
        assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
        observation = persistence.get_agent_run_observation()
        assert observation is not None
        assert observation.agent_run_id != observation.turn_id
        assert len(observation.requests) == 1
        assert observation.requests[0].state.value == "completed"
        assert observation.requests[0].usage.availability is UsageAvailability.UNAVAILABLE
        assert observation.terminal_metrics is not None
        assert observation.terminal_metrics.finish_reason is FinishReason.STOP
        assert observation.terminal_metrics.model_attempts == 1
        assert observation.terminal_metrics.usage.availability is UsageAvailability.UNAVAILABLE
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_agent_loop_marks_an_empty_provider_stream_as_invalid_response(tmp_path):
    class EmptyProvider:
        async def stream(self, _model, _messages, _tools=()):
            if False:
                yield ModelEvent(kind="text_delta", text="unreachable")

    handle, _journal, session, persistence = _open(tmp_path)
    try:
        loop = AgentLoop(
            EmptyProvider(),
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            id_source=FixedIdSource(),
            clock=FixedClock(),
        )

        events = [event async for event in loop.run_task(session, "empty")]

        assert events[-1].payload["stop_code"] == AgentStopCode.INVALID_RESPONSE.value
        observation = persistence.get_agent_run_observation()
        assert observation is not None and observation.terminal_metrics is not None
        assert observation.requests[0].state.value == "failed"
        assert observation.requests[0].error_code is ModelErrorCode.INVALID_RESPONSE
        assert observation.terminal_metrics.stop_code is AgentStopCode.INVALID_RESPONSE
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_agent_loop_persists_normalized_usage_on_request_and_terminal_metrics(tmp_path):
    class UsageProvider:
        async def stream(self, _model, _messages, _tools=()):
            yield ModelEvent(
                kind="completed",
                finish_reason=ModelFinishReason.STOP,
                message=AssistantMessage(content="done"),
                usage=ModelUsage(
                    availability=UsageAvailability.AVAILABLE,
                    input_tokens=4,
                    output_tokens=2,
                    total_tokens=6,
                ),
            )

    handle, _journal, session, persistence = _open(tmp_path)
    try:
        loop = AgentLoop(
            UsageProvider(),
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            id_source=FixedIdSource(),
            clock=FixedClock(),
        )

        await _collect_events(loop.run_task(session, "question"))

        observation = persistence.get_agent_run_observation()
        assert observation is not None and observation.terminal_metrics is not None
        assert observation.requests[0].usage.total_tokens == 6
        assert observation.terminal_metrics.usage.input_tokens == 4
        assert observation.terminal_metrics.usage.output_tokens == 2
        assert observation.terminal_metrics.usage.total_tokens == 6
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_agent_loop_settles_request_when_deadline_expires_after_text(tmp_path):
    class TextThenCompleted:
        async def stream(self, _model, _messages, _tools=()):
            yield ModelEvent(kind="text_delta", text="partial")
            yield ModelEvent(
                kind="completed",
                finish_reason=ModelFinishReason.STOP,
                message=AssistantMessage(content="partial"),
            )

    handle, _journal, session, persistence = _open(tmp_path)
    try:
        steps = iter((0.0, 0.0, 0.0, 61.0))

        def monotonic():
            return next(steps, 61.0)

        loop = AgentLoop(
            TextThenCompleted(),
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(max_run_seconds=60.0, tool_timeout_seconds=1.0),
            id_source=FixedIdSource(),
            clock=FixedClock(),
            monotonic=monotonic,
        )

        events = [event async for event in loop.run_task(session, "deadline")]

        assert events[-1].payload["stop_code"] == AgentStopCode.RUN_TIMEOUT.value
        observation = persistence.get_agent_run_observation()
        assert observation is not None and observation.terminal_metrics is not None
        assert observation.requests[0].state.value == "failed"
        assert observation.requests[0].error_code is ModelErrorCode.TIMEOUT
        assert observation.terminal_metrics.stop_code is AgentStopCode.RUN_TIMEOUT
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_agent_loop_settles_request_when_deadline_expires_before_provider(tmp_path):
    handle, _journal, session, persistence = _open(tmp_path)
    try:
        steps = iter((0.0, 0.0, 61.0))

        def monotonic():
            return next(steps, 61.0)

        loop = AgentLoop(
            ScriptedModelProvider(["unreachable"]),
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(max_run_seconds=60.0, tool_timeout_seconds=1.0),
            id_source=FixedIdSource(),
            clock=FixedClock(),
            monotonic=monotonic,
        )

        events = [event async for event in loop.run_task(session, "deadline")]

        assert events[-1].payload["stop_code"] == AgentStopCode.RUN_TIMEOUT.value
        observation = persistence.get_agent_run_observation()
        assert observation is not None and observation.terminal_metrics is not None
        assert observation.requests[0].state.value == "failed"
        assert observation.requests[0].error_code is ModelErrorCode.TIMEOUT
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_agent_loop_uses_a_new_agent_run_for_each_new_turn(tmp_path):
    handle, _journal, session, persistence = _open(tmp_path)
    try:
        loop = AgentLoop(
            ScriptedModelProvider(["first", "second"]),
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            id_source=FixedIdSource(),
            clock=FixedClock(),
        )

        await _collect_events(loop.run_task(session, "one"))
        first = persistence.get_agent_run_observation()
        await _collect_events(loop.run_task(session, "two"))
        second = persistence.get_agent_run_observation()

        assert first is not None and second is not None
        assert first.agent_run_id != second.agent_run_id
        assert first.turn_id != second.turn_id
        assert first.terminal_metrics is not None
        assert second.terminal_metrics is not None
        assert first.terminal_metrics.finish_reason is FinishReason.STOP
        assert second.terminal_metrics.finish_reason is FinishReason.STOP
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_agent_loop_resume_continues_the_same_agent_run_after_settled_request(tmp_path):
    handle, _journal, session, persistence = _open(tmp_path)
    try:
        persistence.submit_user(
            session,
            "resume me",
            "cmsg_1",
            turn_id="turn_1",
            agent_run_id="arun_1",
        )
        admitted = persistence.admit_model_request(
            agent_run_id="arun_1",
            attempt_ordinal=1,
            estimated_request_chars=10,
            request_char_budget=100,
            cleared_cycle_count=2,
            dropped_turn_count=1,
            dropped_cycle_count=3,
            dropped_record_count=4,
        )
        persistence.settle_model_request(
            admitted.model_request_id,
            state="failed",
            error_code=ModelErrorCode.NETWORK,
        )
    finally:
        handle.close()

    store = OperationalStore(
        tmp_path / "state",
        retry_policy=_retry(),
        clock=FixedClock(),
        maintenance_timeout=0,
    )
    reopened = store.open(StoreOpenMode.READ_WRITE)
    try:
        journal = SqliteOperationalJournal(reopened)
        resumed_session = Session(session_id="ses_1")
        resumed_persistence = SessionPersistence(
            workspace_id="ws_1",
            journal=journal,
            store_session=reopened,
            id_source=RandomIdSource(),
            model=ModelRef(provider_id="p", model_id="m"),
            run_policy=make_context_builder().run_policy,
            runtime_instance_id="host-2",
            clock=FixedClock(),
        )
        resumed_persistence.restore_into(resumed_session)
        loop = AgentLoop(
            ScriptedModelProvider(["resumed"]),
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            id_source=RandomIdSource(),
            clock=FixedClock(),
        )

        events = [
            event async for event in loop.run_task(resumed_session, "", resume_current_turn=True)
        ]

        assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
        observation = resumed_persistence.get_agent_run_observation("arun_1")
        assert observation is not None and observation.terminal_metrics is not None
        assert observation.agent_run_id == "arun_1"
        assert observation.turn_id == "turn_1"
        assert [item.attempt_ordinal for item in observation.requests] == [1, 2]
        assert [item.state.value for item in observation.requests] == ["failed", "completed"]
        assert observation.terminal_metrics.model_attempts == 2
        assert observation.terminal_metrics.cleared_cycle_count == 2
        assert observation.terminal_metrics.dropped_turn_count == 1
        assert observation.terminal_metrics.dropped_cycle_count == 3
        assert observation.terminal_metrics.dropped_record_count == 4
        assert observation.terminal_metrics.retry_count == 1
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_agent_loop_terminal_metrics_count_durable_tool_dispositions(tmp_path):
    class EchoArguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

        value: str

    async def echo(arguments: EchoArguments):
        return {"echo": arguments.value}

    registry = ToolRegistry()
    registry.register(
        make_tool(name="echo", description="echo", arguments_model=EchoArguments, handler=echo)
    )
    executor = ToolExecutor(registry.snapshot(), make_context_builder().run_policy)
    handle, _journal, session, persistence = _open(tmp_path)
    try:
        provider = ScriptedModelProvider(
            [
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(
                            id="call_1", name="echo", arguments=json.dumps({"value": "ok"})
                        ),
                    )
                ),
                AssistantMessage(content="done"),
            ]
        )
        loop = AgentLoop(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            id_source=FixedIdSource(),
            clock=FixedClock(),
            tool_executor=executor,
        )

        events = [event async for event in loop.run_task(session, "use echo")]

        assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
        observation = persistence.get_agent_run_observation()
        assert observation is not None
        assert len(observation.requests) == 2
        assert all(item.state.value == "completed" for item in observation.requests)
        assert observation.terminal_metrics is not None
        metrics = observation.terminal_metrics
        assert metrics.tool_rounds == 1
        assert metrics.tool_calls == 1
        assert metrics.tool_terminal_counts.succeeded == 1
        assert metrics.tool_terminal_counts.terminal_total == 1
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_agent_loop_budget_closes_durable_tool_executions_before_finalization(tmp_path):
    class EchoArguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

        value: str

    async def echo(arguments: EchoArguments):
        return {"echo": arguments.value}

    builder = make_context_builder(max_tool_calls=1, max_tool_calls_per_cycle=1)
    registry = ToolRegistry()
    registry.register(
        make_tool(name="echo", description="echo", arguments_model=EchoArguments, handler=echo)
    )
    executor = ToolExecutor(registry.snapshot(), builder.run_policy)
    handle, journal, session, persistence = _open(tmp_path)
    try:
        provider = ScriptedModelProvider(
            [
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(id="call_1", name="echo", arguments='{"value":"one"}'),
                    )
                ),
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(id="call_2", name="echo", arguments='{"value":"two"}'),
                    )
                ),
            ]
        )
        loop = AgentLoop(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            builder,
            id_source=FixedIdSource(),
            clock=FixedClock(),
            tool_executor=executor,
        )

        events = [event async for event in loop.run_task(session, "use echo")]

        assert events[-1].payload["finish_reason"] == FinishReason.ERROR.value
        assert events[-1].payload["stop_code"] == AgentStopCode.TOOL_CALL_LIMIT.value
        observation = persistence.get_agent_run_observation()
        assert observation is not None and observation.terminal_metrics is not None
        executions = journal.list_executions("ws_1", agent_run_id=observation.agent_run_id)
        assert len(executions) == 2
        assert all(item.state is ToolExecutionState.CLOSED for item in executions)
        dispositions = [item.disposition for item in executions]
        assert dispositions.count(ToolExecutionDisposition.SUCCEEDED) == 1
        assert dispositions.count(ToolExecutionDisposition.CANCELLED) == 1
        assert observation.terminal_metrics.tool_terminal_counts.succeeded == 1
        assert observation.terminal_metrics.tool_terminal_counts.cancelled == 1
        assert observation.terminal_metrics.tool_terminal_counts.terminal_total == 2
    finally:
        handle.close()
