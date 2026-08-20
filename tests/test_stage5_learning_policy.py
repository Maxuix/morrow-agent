"""Application boundary tests for the workspace LearningPolicy foundation."""

from datetime import UTC, datetime

import pytest

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.api import OperationalApplicationService
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.learning import LearningMode
from morrow.testing import FixedClock, FixedIdSource

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _api(tmp_path):
    store = OperationalStore(
        tmp_path / "state",
        clock=FixedClock(NOW),
        maintenance_timeout=0,
    )
    session = store.initialize()
    journal = SqliteOperationalJournal(session)
    api = OperationalApplicationService(
        journal=journal,
        workspace_id="ws_1",
        id_source=FixedIdSource(),
        clock=lambda: NOW,
    )
    return session, journal, api


def test_policy_defaults_without_a_write_and_exposes_only_safe_modes(tmp_path):
    session, journal, api = _api(tmp_path)
    try:
        status = api.learning_policy_status()
        assert status.persisted is False
        assert status.policy.mode is LearningMode.REVIEW_ONLY
        assert journal.get_learning_policy("ws_1") is None

        created = api.set_learning_mode(
            LearningMode.REVIEW_ONLY,
            command_id="cmd_policy_1",
            expected_row_version=status.policy.row_version,
        )
        assert created.value.persisted is True
        assert created.value.policy.row_version == 1
        assert created.receipt is not None
        assert created.receipt.event_cursor == 1

        changed = api.set_learning_mode(
            "off",
            command_id="cmd_policy_2",
            expected_row_version=1,
        )
        assert changed.value.policy.mode is LearningMode.OFF
        assert changed.value.policy.row_version == 2
        events = journal.list_application_events("ws_1")
        assert [event.event_type for event in events] == [
            "learning.policy_changed",
            "learning.policy_changed",
        ]
        assert all(
            set(event.payload)
            == {
                "mode",
                "candidate_ttl_days",
                "max_candidates_per_review",
                "max_evidence_per_review",
                "row_version",
            }
            for event in events
        )
    finally:
        session.close()


def test_policy_replay_conflict_stale_and_explicit_auto_are_typed(tmp_path):
    session, _journal, api = _api(tmp_path)
    try:
        first = api.set_learning_mode("off", command_id="cmd_policy", expected_row_version=None)
        replay = api.set_learning_mode("off", command_id="cmd_policy", expected_row_version=None)
        assert replay.receipt is not None
        assert replay.receipt.disposition.value == "replay"
        assert replay.value == first.value

        with pytest.raises(ApplicationError) as conflict:
            api.set_learning_mode("review_only", command_id="cmd_policy", expected_row_version=None)
        assert conflict.value.code is ApplicationErrorCode.CONFLICT

        with pytest.raises(ApplicationError) as stale:
            api.set_learning_mode("review_only", command_id="cmd_stale", expected_row_version=0)
        assert stale.value.code is ApplicationErrorCode.STALE

        with pytest.raises(ApplicationError) as reserved:
            api.set_learning_mode("explicit_auto", command_id="cmd_auto")
        assert reserved.value.code is ApplicationErrorCode.INVALID
    finally:
        session.close()


def test_policy_command_receipt_and_event_are_atomic(tmp_path, monkeypatch):
    session, journal, api = _api(tmp_path)
    try:

        def fail_event(*_args, **_kwargs):
            raise RuntimeError("injected event failure")

        monkeypatch.setattr(journal, "put_application_event_in_txn", fail_event)
        with pytest.raises(ApplicationError) as failure:
            api.set_learning_mode("review_only", command_id="cmd_atomic")
        assert failure.value.code is ApplicationErrorCode.UNAVAILABLE
        assert journal.get_learning_policy("ws_1") is None
        assert journal.get_application_command_receipt("ws_1", "cmd_atomic") is None
        assert journal.list_application_events("ws_1") == ()
    finally:
        session.close()
