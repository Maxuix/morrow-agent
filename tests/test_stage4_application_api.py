"""Unified Stage 4 Command/Query/Event boundary coverage."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.application.api import OperationalApplicationService
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import DurableSession, SessionLifecycle
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
