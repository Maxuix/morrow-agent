"""Unified Stage 4 Command/Query/Event boundary coverage."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.application.api import OperationalApplicationService
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import (
    DurableConversationRecord,
    DurableSession,
    SessionHealth,
    SessionLifecycle,
    TaskRunStatus,
)
from morrow.testing import FixedClock, FixedIdSource


def _api(tmp_path: Path, workspace_id: str = "ws_1"):
    store = OperationalStore(
        tmp_path / "state",
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    journal.create_session(DurableSession(session_id="ses_1", workspace_id=workspace_id))
    return handle, OperationalApplicationService(
        journal=journal,
        workspace_id=workspace_id,
        id_source=FixedIdSource(),
    )


def test_session_command_receipt_replay_conflict_and_same_transaction_event(tmp_path):
    handle, api = _api(tmp_path)
    try:
        first = api.create_session(session_id="ses_2", command_id="cmd_create")
        replay = api.create_session(session_id="ses_2", command_id="cmd_create")
        assert first.value.session_id == replay.value.session_id == "ses_2"
        assert replay.receipt is not None
        assert replay.receipt.disposition.value == "replay"
        events = api.list_events().items
        assert [event.cursor for event in events] == [1]
        assert events[0].event_type == "session.created"
        with pytest.raises(ApplicationError) as error:
            api.create_session(session_id="ses_3", command_id="cmd_create")
        assert error.value.code is ApplicationErrorCode.CONFLICT
        assert api.get_session("ses_3") is None
    finally:
        handle.close()


def test_session_archive_is_version_checked_and_event_cursor_is_independent(tmp_path):
    handle, api = _api(tmp_path)
    try:
        archived = api.archive_session("ses_1", command_id="cmd_archive")
        assert archived.value.lifecycle is SessionLifecycle.ARCHIVED
        assert archived.receipt is not None
        assert archived.receipt.event_cursor == 1
        assert api.list_events(after_cursor=0).items[0].cursor == 1
        with pytest.raises(ApplicationError) as error:
            api.archive_session("ses_missing", command_id="cmd_missing")
        assert error.value.code is ApplicationErrorCode.NOT_FOUND
    finally:
        handle.close()


def test_archive_requires_no_current_task_and_archived_session_cannot_start_one(tmp_path):
    handle, api = _api(tmp_path)
    try:
        task = api.task_new("ses_1", command_id="cmd_task").value
        with pytest.raises(ApplicationError) as active_error:
            api.archive_session("ses_1", command_id="cmd_archive_active")
        assert active_error.value.code is ApplicationErrorCode.INVALID
        assert "current TaskRun" in active_error.value.message
        assert api.get_session("ses_1").lifecycle is SessionLifecycle.ACTIVE

        api.task_cancel(
            task.task_run_id,
            command_id="cmd_cancel",
            expected_row_version=task.row_version,
        )
        archived = api.archive_session("ses_1", command_id="cmd_archive")
        assert archived.value.lifecycle is SessionLifecycle.ARCHIVED
        with pytest.raises(ApplicationError) as archived_error:
            api.task_new("ses_1", command_id="cmd_archived_task")
        assert archived_error.value.code is ApplicationErrorCode.INVALID
        assert "active Session" in archived_error.value.message
        assert len(api.list_tasks("ses_1").items) == 1
    finally:
        handle.close()


@pytest.mark.parametrize(
    ("health", "expected_code"),
    (
        (SessionHealth.NEEDS_RECOVERY, ApplicationErrorCode.NEEDS_RECOVERY),
        (SessionHealth.QUARANTINED, ApplicationErrorCode.QUARANTINED),
        (SessionHealth.READ_ONLY, ApplicationErrorCode.READ_ONLY),
    ),
)
def test_task_new_and_resume_require_active_healthy_session(tmp_path, health, expected_code):
    handle, api = _api(tmp_path)
    try:
        api.create_session(session_id="ses_2", command_id="cmd_create_second")
        resumable = api.task_new("ses_2", command_id="cmd_seed_task").value
        failed = api.tasks.fail(
            resumable.task_run_id,
            command_id="cmd_seed_fail",
            expected_row_version=resumable.row_version,
        ).task
        for session_id in ("ses_1", "ses_2"):
            row = api.get_session(session_id)
            api.journal.save_session(
                "ws_1",
                row.model_copy(update={"health": health}),
            )
        event_count = len(api.list_events().items)

        with pytest.raises(ApplicationError) as new_error:
            api.task_new("ses_1", command_id="cmd_blocked_new")
        with pytest.raises(ApplicationError) as resume_error:
            api.task_resume(failed.task_run_id, command_id="cmd_blocked_resume")

        assert new_error.value.code is expected_code
        assert resume_error.value.code is expected_code
        assert api.list_tasks("ses_1").items == ()
        assert api.get_task(failed.task_run_id).status is TaskRunStatus.FAILED
        assert len(api.list_events().items) == event_count
        assert api.journal.get_application_command_receipt("ws_1", "cmd_blocked_new") is None
        assert api.journal.get_application_command_receipt("ws_1", "cmd_blocked_resume") is None
    finally:
        handle.close()


def test_session_updated_at_is_monotonic_and_invalidates_old_archive_tokens(tmp_path):
    handle, api = _api(tmp_path)
    try:
        initial = api.get_session("ses_1")
        task = api.task_new("ses_1", command_id="cmd_task").value
        after_task = api.get_session("ses_1")
        assert after_task.updated_at > initial.updated_at
        with pytest.raises(ApplicationError) as stale:
            api.archive_session(
                "ses_1",
                command_id="cmd_stale_archive",
                expected_updated_at=initial.updated_at,
            )
        assert stale.value.code is ApplicationErrorCode.STALE

        ready = api.tasks._transition(
            task,
            TaskRunStatus.READY_FOR_ACCEPTANCE,
            reason="answer_presented",
            turn_id=None,
            command_id=None,
        )
        after_transition = api.get_session("ses_1")
        assert ready.status is TaskRunStatus.READY_FOR_ACCEPTANCE
        assert after_transition.updated_at > after_task.updated_at

        api.journal.append_records(
            "ws_1",
            (
                DurableConversationRecord(
                    record_id="rec_1",
                    session_id="ses_1",
                    conversation_position=1,
                    kind="message",
                    payload={"role": "user", "content": "hello"},
                ),
            ),
        )
        after_conversation = api.get_session("ses_1")
        assert after_conversation.updated_at > after_transition.updated_at

        recovered = api.journal.save_session(
            "ws_1",
            after_conversation.model_copy(update={"health": SessionHealth.NEEDS_RECOVERY}),
        )
        assert recovered.updated_at > after_conversation.updated_at
    finally:
        handle.close()


def test_archived_session_rejects_explicit_task_resume(tmp_path):
    handle, api = _api(tmp_path)
    try:
        task = api.task_new("ses_1", command_id="cmd_task").value
        failed = api.tasks.fail(task.task_run_id, command_id="cmd_fail").task
        assert failed.status is TaskRunStatus.FAILED
        handle.run_write(
            lambda executor: executor.execute(
                "UPDATE sessions SET lifecycle = 'archived' WHERE session_id = 'ses_1'"
            )
        )

        with pytest.raises(ApplicationError) as error:
            api.task_resume(
                failed.task_run_id,
                command_id="cmd_resume",
                expected_row_version=failed.row_version,
            )
        assert error.value.code is ApplicationErrorCode.INVALID
        assert "active Session" in error.value.message
        assert api.get_task(failed.task_run_id).status is TaskRunStatus.FAILED
    finally:
        handle.close()


def test_application_event_payload_rejects_secret_material(tmp_path):
    handle, api = _api(tmp_path)
    try:
        with pytest.raises(ValueError):
            from morrow.core.application import ApplicationEvent

            ApplicationEvent(
                event_id="evt_secret",
                workspace_id="ws_1",
                event_type="test.secret",
                aggregate_kind="test",
                aggregate_id="ses_1",
                payload={"api_key": "should-not-persist"},
            )
    finally:
        handle.close()


def test_application_boundary_rejects_invalid_workspace_and_event_pages(tmp_path):
    handle, api = _api(tmp_path)
    try:
        with pytest.raises(ApplicationError) as workspace_error:
            OperationalApplicationService(
                journal=api.journal,
                workspace_id="not-a-workspace",
            )
        assert workspace_error.value.code is ApplicationErrorCode.INVALID
        with pytest.raises(ApplicationError) as cursor_error:
            api.list_events(after_cursor=-1)
        assert cursor_error.value.code is ApplicationErrorCode.INVALID
        with pytest.raises(ApplicationError) as limit_error:
            api.list_events(limit=101)
        assert limit_error.value.code is ApplicationErrorCode.INVALID
    finally:
        handle.close()


def test_application_event_failure_rolls_back_business_state(tmp_path, monkeypatch):
    handle, api = _api(tmp_path)
    try:

        def fail_event(*args, **kwargs):
            raise ValueError("event failure")

        monkeypatch.setattr(api, "_event", fail_event)
        with pytest.raises(ApplicationError) as error:
            api.create_session(session_id="ses_3", command_id="cmd_rollback")
        assert error.value.code is ApplicationErrorCode.INVALID
        assert api.get_session("ses_3") is None
        assert api.list_events().items == ()
    finally:
        handle.close()
