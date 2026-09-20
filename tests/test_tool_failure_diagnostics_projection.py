"""Failure-diagnostic regressions for the activity projection seams.

The GUI must be able to show error code, validation reason and field path
for a failed tool call — live and after a refresh — without ever seeing raw
arguments or output content.
"""

from __future__ import annotations

import json

from morrow.adapters.state.chat_timeline_journal import ChatTimelineJournal
from morrow.application.chat_timeline import TimelineService
from morrow.core.activity import ActivityItem
from morrow.server.activity_projection import SessionActivityProjector


def _terminal_fact(**overrides):
    fact = {
        "call_id": "call_1",
        "tool_name": "submit_node_result",
        "ordinal": 1,
        "total": 1,
        "phase": "terminal",
        "timestamp": "2026-09-08T00:00:00+00:00",
        "disposition": "failed",
        "error_code": "invalid_arguments",
        "validation_reason": "empty_submission",
        "validation_path": "$",
    }
    fact.update(overrides)
    return fact


def test_terminal_tool_fact_projects_failure_diagnostics():
    item = SessionActivityProjector().observe_tool_fact(
        workspace_id="ws",
        session_id="ses_1",
        agent_run_id="arun_1",
        **_terminal_fact(),
    )

    assert item is not None
    payload = ActivityItem.model_validate(item).payload
    assert payload.error_code == "invalid_arguments"
    assert payload.validation_reason == "empty_submission"
    assert payload.validation_path == "$"


def test_running_tool_fact_stays_diagnostics_free():
    item = SessionActivityProjector().observe_tool_fact(
        workspace_id="ws",
        session_id="ses_1",
        agent_run_id="arun_1",
        **_terminal_fact(
            phase="executing",
            disposition=None,
            error_code=None,
            validation_reason=None,
            validation_path=None,
        ),
    )

    assert item is not None
    payload = ActivityItem.model_validate(item).payload
    assert payload.error_code is None
    assert payload.validation_reason is None


def test_recovery_projects_validation_diagnostics_from_the_stored_envelope():
    envelope = json.dumps(
        {
            "ok": False,
            "summary": {"chars": 120},
            "error_code": "invalid_arguments",
            "error_reason": "empty_submission",
            "validation_diagnostics": [{"path": "$", "type": "empty_submission"}],
        }
    )

    class _Backend:
        def read_all(self, _sql, _params):
            return [
                (
                    "tex_1",
                    "call_1",
                    "submit_node_result",
                    "closed",
                    "failed",
                    1,
                    "arun_1",
                    "turn_1",
                    None,
                    1000,
                    1010,
                    "invalid_arguments",
                    envelope,
                )
            ]

    facts = ChatTimelineJournal(_Backend(), None).tool_execution_facts("ws", "ses_1")
    assert facts[0]["validation"] == {
        "error_code": "invalid_arguments",
        "validation_reason": "empty_submission",
        "validation_path": "$",
        "validation_type": "empty_submission",
    }

    class _Manager:
        @staticmethod
        def require_session(session_id):
            return type("S", (), {"conversation_position": 1})()

    service = TimelineService(_Manager(), type("J", (), {"chat_timeline": None})(), "ws")
    service.repository = type(
        "R",
        (),
        {
            "tool_execution_facts": staticmethod(lambda *a, **k: facts),
            "page": staticmethod(lambda *a, **k: []),
        },
    )()
    recovered = service.tool_activities("ses_1")

    assert recovered["truncated"] is False
    payload = recovered["items"][0]["payload"]
    ActivityItem.model_validate(recovered["items"][0])
    assert payload["error_code"] == "invalid_arguments"
    assert payload["validation_reason"] == "empty_submission"
    assert payload["validation_path"] == "$"
