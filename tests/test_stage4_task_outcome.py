"""Subplan 40 TaskRun lifecycle and immutable TaskOutcome coverage."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

import morrow.bootstrap as bootstrap
from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.application.tasks import TaskCommandConflict, TaskCommandError, TaskService
from morrow.bootstrap import build_application, build_session_application
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import (
    DurableSession,
    DurableTurn,
    SessionHealth,
    SessionLifecycle,
    TaskOutcomeTrigger,
    TaskRunStatus,
)
from morrow.core.models import ModelRef
from morrow.core.store import StorageError, StorageErrorCode
from morrow.testing import FixedClock, FixedIdSource, ScriptedModelProvider


def _journal(tmp_path: Path):
    store = OperationalStore(
        tmp_path / "state",
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    session = store.initialize()
    journal = SqliteOperationalJournal(session)
    journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
    return session, journal


def test_task_state_machine_current_pointer_and_attempts(tmp_path):
    session, journal = _journal(tmp_path)
    try:
        tasks = TaskService(journal=journal, workspace_id="ws_1", id_source=FixedIdSource())
        created = tasks.new_task("ses_1", command_id="cmd_new")
        assert created.task is not None
        task = created.task
        assert task.status is TaskRunStatus.OPEN
        assert journal.get_session("ws_1", "ses_1").current_task_run_id == task.task_run_id

        ready = tasks._transition(
            task,
            TaskRunStatus.READY_FOR_ACCEPTANCE,
            reason="assistant_answer_presented",
            turn_id=None,
            command_id=None,
        )
        continued = tasks.continue_after_answer(ready)
        assert continued.status is TaskRunStatus.OPEN
        assert continued.row_version == 3

        failed = tasks.fail(continued.task_run_id, command_id="cmd_fail")
        assert failed.task.status is TaskRunStatus.FAILED
        resumed = tasks.resume(failed.task.task_run_id, command_id="cmd_resume")
        assert resumed.task.status is TaskRunStatus.OPEN
        assert resumed.task.attempt == 2
        assert [
            item.to_status for item in journal.list_task_transitions("ws_1", task.task_run_id)
        ] == [
            TaskRunStatus.READY_FOR_ACCEPTANCE,
            TaskRunStatus.OPEN,
            TaskRunStatus.FAILED,
            TaskRunStatus.OPEN,
        ]
    finally:
        session.close()


def test_accept_is_idempotent_and_outcome_is_immutable(tmp_path):
    session, journal = _journal(tmp_path)
    try:
        tasks = TaskService(journal=journal, workspace_id="ws_1", id_source=FixedIdSource())
        task = tasks.new_task("ses_1").task
        ready = tasks._transition(
            task,
            TaskRunStatus.READY_FOR_ACCEPTANCE,
            reason="answer",
            turn_id=None,
            command_id=None,
        )
        accepted = tasks.accept(
            ready.task_run_id,
            command_id="cmd_accept",
            summary="完成并由用户确认",
            feedback=("清晰",),
        )
        assert accepted.task.status is TaskRunStatus.ACCEPTED
        assert journal._read_one(
            "SELECT status FROM task_runs WHERE task_run_id = ?", (ready.task_run_id,)
        ) == (TaskRunStatus.ACCEPTED.value,)
        assert accepted.outcome is not None
        assert accepted.outcome.version == 1
        assert accepted.outcome.trigger is TaskOutcomeTrigger.ACCEPTANCE
        replay = tasks.accept(
            ready.task_run_id,
            command_id="cmd_accept",
            summary="完成并由用户确认",
            feedback=("清晰",),
        )
        assert replay.kind == "replay"
        assert replay.outcome == accepted.outcome
        assert journal.list_task_outcomes("ws_1", ready.task_run_id) == (accepted.outcome,)
        with pytest.raises(TaskCommandConflict):
            tasks.accept(ready.task_run_id, command_id="cmd_accept", summary="不同请求")
        with pytest.raises(StorageError) as error:
            journal.put_task_outcome("ws_1", accepted.outcome)
        assert error.value.code is StorageErrorCode.UNAVAILABLE
        assert journal.get_session("ws_1", "ses_1").current_task_run_id is None
    finally:
        session.close()


def test_terminal_task_cannot_be_reopened_and_new_task_is_explicit(tmp_path):
    session, journal = _journal(tmp_path)
    try:
        tasks = TaskService(journal=journal, workspace_id="ws_1", id_source=FixedIdSource())
        first = tasks.new_task("ses_1").task
        ready = tasks._transition(
            first,
            TaskRunStatus.READY_FOR_ACCEPTANCE,
            reason="answer",
            turn_id=None,
            command_id=None,
        )
        accepted = tasks.accept(ready.task_run_id).task
        with pytest.raises(TaskCommandError):
            tasks.resume(accepted.task_run_id)
        second = tasks.new_task("ses_1", command_id="cmd_second").task
        assert second.task_run_id != first.task_run_id
        assert journal.get_session("ws_1", "ses_1").current_task_run_id == second.task_run_id
    finally:
        session.close()


@pytest.mark.asyncio
async def test_archived_session_rejects_new_turn_at_application_and_orchestrator_boundaries(
    tmp_path,
):
    project = tmp_path / "project"
    project.mkdir()
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
        id_source=FixedIdSource(),
    )
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(["must not run"])
    products = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
    )
    try:
        session_id = products.session.session_id
        archived = products.api.archive_session(session_id, command_id="cmd_archive").value
        assert products.session.lifecycle is SessionLifecycle.ACTIVE

        stale_dispatch = await products.orchestrator.dispatch("stale archived projection")
        assert stale_dispatch.events == []
        assert stale_dispatch.lines == ["当前 Session 已归档，无法开始新的 Turn。"]
        assert products.session.lifecycle is SessionLifecycle.ARCHIVED
        assert provider.stream_calls == []
        assert products.tasks.list(session_id) == ()
        assert products.persistence.journal.load_records(identity.workspace_id, session_id) == ()

        with pytest.raises(ApplicationError) as error:
            products.api.submit_turn(
                products.session,
                user_input="start after archive",
                client_message_id="client-archived",
                turn_id="turn_archived",
                command_id="cmd_turn_archived",
            )
        assert error.value.code is ApplicationErrorCode.INVALID
        assert "active Session" in error.value.message
        assert products.tasks.list(session_id) == ()
        assert products.persistence.journal.load_records(identity.workspace_id, session_id) == ()

        assert products.session.lifecycle is archived.lifecycle
        synchronized_dispatch = await products.orchestrator.dispatch("still archived")
        assert synchronized_dispatch.events == []
        assert synchronized_dispatch.lines == ["当前 Session 已归档，无法开始新的 Turn。"]
        assert products.session.lifecycle is SessionLifecycle.ARCHIVED

        with pytest.raises(ValueError, match="active Session"):
            build_session_application(
                app,
                identity,
                provider=ScriptedModelProvider(["must not resume"]),
                model=ModelRef(provider_id="p", model_id="m"),
                resume_session_id=session_id,
            )
    finally:
        products.persistence.close()


@pytest.mark.parametrize(
    ("health", "expected_code"),
    (
        (SessionHealth.NEEDS_RECOVERY, ApplicationErrorCode.NEEDS_RECOVERY),
        (SessionHealth.QUARANTINED, ApplicationErrorCode.QUARANTINED),
        (SessionHealth.READ_ONLY, ApplicationErrorCode.READ_ONLY),
    ),
)
def test_direct_api_submit_turn_rechecks_stale_durable_health(tmp_path, health, expected_code):
    project = tmp_path / "project"
    project.mkdir()
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
        id_source=FixedIdSource(),
    )
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    products = build_session_application(
        app,
        identity,
        provider=ScriptedModelProvider(["must not run"]),
        model=ModelRef(provider_id="p", model_id="m"),
    )
    try:
        session_id = products.session.session_id
        journal = products.persistence.journal
        row = products.api.get_session(session_id)
        journal.save_session(
            identity.workspace_id,
            row.model_copy(update={"health": health}),
        )
        assert products.session.health is SessionHealth.OK

        with pytest.raises(ApplicationError) as error:
            products.api.submit_turn(
                products.session,
                user_input="must not persist",
                client_message_id="client-health",
                turn_id="turn_health",
                agent_run_id="arun_health",
                command_id="cmd_turn_health",
            )

        assert error.value.code is expected_code
        assert products.session.health is health
        assert products.tasks.list(session_id) == ()
        assert journal.load_records(identity.workspace_id, session_id) == ()
        assert journal.list_session_turns(identity.workspace_id, session_id) == ()
        assert journal.list_session_agent_runs(identity.workspace_id, session_id) == ()
        assert (
            journal.get_application_command_receipt(identity.workspace_id, "cmd_turn_health")
            is None
        )
    finally:
        products.persistence.close()


@pytest.mark.parametrize(
    ("health", "expected_message"),
    (
        (SessionHealth.NEEDS_RECOVERY, "当前回合需要恢复后才能继续"),
        (SessionHealth.QUARANTINED, "Session health quarantined cannot start a Turn"),
        (SessionHealth.READ_ONLY, "Session health read_only cannot start a Turn"),
    ),
)
@pytest.mark.asyncio
async def test_orchestrator_refreshes_stale_durable_session_health_before_submit(
    tmp_path, health, expected_message
):
    project = tmp_path / "project"
    project.mkdir()
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
        id_source=FixedIdSource(),
    )
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(["must not run"])
    products = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
    )
    try:
        session_id = products.session.session_id
        row = products.api.get_session(session_id)
        products.persistence.journal.save_session(
            identity.workspace_id,
            row.model_copy(update={"health": health}),
        )
        assert products.session.health is SessionHealth.OK

        dispatch = await products.orchestrator.dispatch("must recover first")

        assert products.session.health is health
        assert [event.type for event in dispatch.events] == [
            "turn.started",
            "error",
            "turn.completed",
        ]
        assert dispatch.events[1].payload["message"] == expected_message
        assert provider.stream_calls == []
        assert products.tasks.list(session_id) == ()
        assert products.persistence.journal.load_records(identity.workspace_id, session_id) == ()
    finally:
        products.persistence.close()


def test_session_application_closes_store_when_post_open_composition_fails(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
        id_source=FixedIdSource(),
    )
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    opened = []
    open_store = bootstrap._open_operational_store

    def track_open(application):
        handle = open_store(application)
        opened.append(handle)
        return handle

    def fail_layout(_filesystem):
        raise RuntimeError("layout failure")

    monkeypatch.setattr(bootstrap, "_open_operational_store", track_open)
    monkeypatch.setattr(bootstrap.FilesystemArtifactStore, "ensure_layout", fail_layout)

    with pytest.raises(RuntimeError, match="layout failure"):
        build_session_application(
            app,
            identity,
            provider=ScriptedModelProvider(["must not run"]),
            model=ModelRef(provider_id="p", model_id="m"),
        )

    assert len(opened) == 1
    assert opened[0]._closed is True


@pytest.mark.asyncio
async def test_multi_turn_correction_and_acceptance_survive_restart(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
        id_source=FixedIdSource(),
    )
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    products = build_session_application(
        app,
        identity,
        provider=ScriptedModelProvider(["初版答案", "修正后的答案"]),
        model=ModelRef(provider_id="p", model_id="m"),
    )
    session_id = products.session.session_id
    await products.orchestrator.dispatch("先完成这个目标")
    first_task = products.tasks.get(products.persistence.current_task_run_id)
    assert first_task.status is TaskRunStatus.READY_FOR_ACCEPTANCE

    await products.orchestrator.dispatch("请修正并补充结果")
    second_task = products.tasks.get(first_task.task_run_id)
    assert second_task.status is TaskRunStatus.READY_FOR_ACCEPTANCE
    assert second_task.task_run_id == first_task.task_run_id
    assert len(products.tasks.list(session_id)) == 1

    accepted = await products.orchestrator.dispatch("/accept")
    assert accepted.value.task.status is TaskRunStatus.ACCEPTED
    outcomes = products.persistence.journal.list_task_outcomes(
        identity.workspace_id, first_task.task_run_id
    )
    assert len(outcomes) == 1
    assert outcomes[0].version == 1
    assert (
        products.persistence.journal.get_session(
            identity.workspace_id, session_id
        ).current_task_run_id
        is None
    )

    resumed = build_session_application(
        app,
        identity,
        provider=ScriptedModelProvider(["ignored"]),
        model=ModelRef(provider_id="p", model_id="m"),
        resume_session_id=session_id,
    )
    assert (
        resumed.persistence.journal.list_task_outcomes(
            identity.workspace_id, first_task.task_run_id
        )
        == outcomes
    )
    assert len(resumed.tasks.list(session_id)) == 1
    assert resumed.tasks.get(first_task.task_run_id).status is TaskRunStatus.ACCEPTED

    await resumed.orchestrator.dispatch("开始一个新的独立目标")
    new_task = resumed.tasks.get(resumed.persistence.current_task_run_id)
    assert new_task.task_run_id != first_task.task_run_id
    assert (
        resumed.persistence.journal.get_session(
            identity.workspace_id, session_id
        ).current_task_run_id
        == new_task.task_run_id
    )


def test_snapshot_digest_covers_feedback_and_expected_version(tmp_path):
    session, journal = _journal(tmp_path)
    try:
        tasks = TaskService(journal=journal, workspace_id="ws_1", id_source=FixedIdSource())
        task = tasks.new_task("ses_1").task
        first = tasks.snapshot(
            task.task_run_id,
            command_id="cmd_snapshot",
            expected_row_version=task.row_version,
            summary="阶段快照",
            feedback=("明确",),
        )
        assert first.outcome is not None
        with pytest.raises(TaskCommandConflict):
            tasks.snapshot(
                task.task_run_id,
                command_id="cmd_snapshot",
                expected_row_version=task.row_version,
                summary="阶段快照",
                feedback=("不同反馈",),
            )
        with pytest.raises(TaskCommandConflict):
            tasks.snapshot(
                task.task_run_id,
                command_id="cmd_snapshot",
                expected_row_version=task.row_version + 1,
                summary="阶段快照",
                feedback=("明确",),
            )
    finally:
        session.close()


def test_outcome_exposes_first_turn_as_goal_reference(tmp_path):
    session, journal = _journal(tmp_path)
    try:
        tasks = TaskService(journal=journal, workspace_id="ws_1", id_source=FixedIdSource())
        task = tasks.new_task("ses_1").task
        journal.create_turn(
            "ws_1",
            DurableTurn(
                turn_id="turn_1",
                session_id="ses_1",
                task_run_id=task.task_run_id,
                client_message_id="client-1",
            ),
        )
        ready = tasks._transition(
            task,
            TaskRunStatus.READY_FOR_ACCEPTANCE,
            reason="answer",
            turn_id="turn_1",
            command_id=None,
        )
        accepted = tasks.accept(ready.task_run_id)
        assert accepted.outcome is not None
        assert accepted.outcome.goal_reference is not None
        assert accepted.outcome.goal_reference.reference_id == "turn_1"
        assert accepted.outcome.goal_reference.role == "user_goal"
    finally:
        session.close()
