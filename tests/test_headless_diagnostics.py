"""The benchmark diagnostic export contains durable facts, never raw values."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from types import SimpleNamespace

import pytest

from morrow.interfaces import headless_diagnostics
from morrow.interfaces.headless_diagnostics import HeadlessDiagnostics


class Value(StrEnum):
    ADMITTED = "admitted"
    COMPLETED = "completed"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    CLOSED = "closed"
    SUCCEEDED = "succeeded"
    AGENT = "agent"


def test_diagnostics_tracks_settlement_without_exporting_raw_result(tmp_path) -> None:
    raw_secret = "top-secret-command-argument"
    request = SimpleNamespace(
        model_request_id="mreq_test",
        attempt_ordinal=1,
        purpose=Value.AGENT,
        state=Value.ADMITTED,
        admitted_at=datetime.now(UTC),
        settled_at=None,
        error_code=None,
        usage=SimpleNamespace(
            availability=Value.UNAVAILABLE, input_tokens=None, output_tokens=None
        ),
        cost=SimpleNamespace(availability=Value.UNAVAILABLE, amount_minor=None, currency=None),
        compaction_required=False,
        dropped_record_count=0,
    )
    execution = SimpleNamespace(
        tool_execution_id="tex_test",
        call_id="call_1",
        tool_name="bash",
        state=Value.CLOSED,
        disposition=Value.SUCCEEDED,
        created_at=datetime.now(UTC),
        executing_at=datetime.now(UTC),
        closed_at=datetime.now(UTC),
        intent=SimpleNamespace(arguments_digest=raw_secret),
        result_envelope=SimpleNamespace(model_dump=lambda **_: {"text": raw_secret}),
        error_code=None,
        facts=SimpleNamespace(
            commands=(SimpleNamespace(command_class="shell", cwd=".", exit_code=0, duration_ms=4),)
        ),
        artifact_refs=(),
    )
    api = SimpleNamespace(
        get_agent_run_observation=lambda _: SimpleNamespace(requests=(request,)),
        get_agent_run_fingerprint=lambda _: {
            "tool_schema_digest": "a" * 64,
            "unexpected": raw_secret,
        },
        journal=SimpleNamespace(list_executions=lambda *_args, **_kwargs: (execution,)),
        workspace_id="ws_test",
    )
    app = SimpleNamespace(api=api, persistence=SimpleNamespace(current_agent_run_id="arun_test"))
    path = tmp_path / "diagnostics.jsonl"
    diagnostics = HeadlessDiagnostics(path, app)
    diagnostics.record_event(SimpleNamespace(type="turn.started", payload={}))
    request.state = Value.COMPLETED
    request.settled_at = datetime.now(UTC)
    request.usage = SimpleNamespace(availability=Value.AVAILABLE, input_tokens=12, output_tokens=3)
    diagnostics.record_event(
        SimpleNamespace(type="turn.completed", payload={"finish_reason": "stop"})
    )
    diagnostics.record_error(RuntimeError(raw_secret), "headless_stream")
    diagnostics.close()
    exported = path.read_text()
    assert raw_secret not in exported
    assert "call_1" not in exported
    rows = [json.loads(line) for line in exported.splitlines()]
    requests = [row for row in rows if row["kind"] == "model.request"]
    assert [row for row in rows if row["kind"] == "run.fingerprint"][0]["digests"] == {
        "tool_schema_digest": "a" * 64
    }
    assert [row["state"] for row in requests] == ["admitted", "completed"]
    assert requests[-1]["usage"]["input_tokens"] == 12
    tools = [row for row in rows if row["kind"] == "tool.execution"]
    assert tools[0]["cwd"] == "."
    assert tools[0]["argument_fingerprint"] != raw_secret
    assert tools[0]["result_fingerprint"] != raw_secret
    error = rows[-1]
    assert error["error_class"] == "RuntimeError"
    assert "traceback" not in exported


@pytest.mark.parametrize(
    ("status", "payload"),
    [
        ("awaiting_model", {"status": "awaiting_model", "attempt_ordinal": 2}),
        ("running_tool", {"status": "running", "call_id": "call_2", "name": "bash"}),
    ],
)
def test_cancel_during_model_or_tool_keeps_last_journal_snapshot(tmp_path, status, payload) -> None:
    request = SimpleNamespace(
        model_request_id="mreq_prior",
        attempt_ordinal=1,
        purpose=Value.AGENT,
        state=Value.COMPLETED,
        admitted_at=datetime.now(UTC),
        settled_at=datetime.now(UTC),
        error_code=None,
        usage=SimpleNamespace(availability=Value.AVAILABLE, input_tokens=8, output_tokens=2),
        cost=SimpleNamespace(availability=Value.UNAVAILABLE, amount_minor=None, currency=None),
        compaction_required=False,
        dropped_record_count=0,
    )
    api = SimpleNamespace(
        get_agent_run_observation=lambda _: SimpleNamespace(requests=(request,)),
        journal=SimpleNamespace(list_executions=lambda *_args, **_kwargs: ()),
        workspace_id="ws_test",
    )
    app = SimpleNamespace(api=api, persistence=SimpleNamespace(current_agent_run_id="arun_test"))
    path = tmp_path / "diagnostics.jsonl"
    diagnostics = HeadlessDiagnostics(path, app)
    diagnostics.record_event(SimpleNamespace(type="turn.started", payload={}))
    event_type = "tool.status" if status == "running_tool" else "status.changed"
    diagnostics.record_event(SimpleNamespace(type=event_type, payload=payload))
    diagnostics.snapshot()
    diagnostics.close()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert any(row["kind"] == "model.request" and row["usage"]["input_tokens"] == 8 for row in rows)
    if status == "running_tool":
        assert any(row["kind"] == "tool.status" and row["status"] == "running" for row in rows)


def test_diagnostics_has_a_bounded_export(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(headless_diagnostics, "_MAX_BYTES", 64)
    path = tmp_path / "diagnostics.jsonl"
    diagnostics = HeadlessDiagnostics(path, SimpleNamespace())
    diagnostics._write("oversized", value="secret-value" * 20)
    diagnostics.close()
    assert "secret-value" not in path.read_text()
    assert json.loads(path.read_text())["kind"] == "diagnostics.truncated"


def test_model_timeout_export_distinguishes_active_stream_from_idle(tmp_path) -> None:
    path = tmp_path / "diagnostics.jsonl"
    diagnostics = HeadlessDiagnostics(path, SimpleNamespace())
    diagnostics.record_event(
        SimpleNamespace(
            type="status.changed",
            payload={
                "status": "model_attempt_timeout",
                "cause_phase": "task_deadline",
                "cause_code": "active_stream",
                "last_activity": "reasoning",
                "internal_remaining_seconds": 0.0,
            },
        )
    )
    diagnostics.close()
    assert json.loads(path.read_text()) == {
        "schema_version": 1,
        "kind": "model.timeout",
        "cause_phase": "task_deadline",
        "cause_code": "active_stream",
        "last_activity": "reasoning",
        "internal_remaining_seconds": 0.0,
    }


def test_compaction_failure_export_has_no_response_body(tmp_path) -> None:
    path = tmp_path / "diagnostics.jsonl"
    diagnostics = HeadlessDiagnostics(path, SimpleNamespace())
    diagnostics.record_event(
        SimpleNamespace(
            type="status.changed",
            payload={
                "status": "compaction_failure",
                "cause_phase": "completion_request",
                "cause_code": "invalid_response",
                "http_status_class": "4xx",
                "request_output_tokens": 4_096,
            },
        )
    )
    diagnostics.close()
    row = json.loads(path.read_text())
    assert row["kind"] == "context.compaction_failure"
    assert row["http_status_class"] == "4xx"
    assert row["request_output_tokens"] == 4_096
    assert "response" not in row


def test_terminal_cause_exports_only_fixed_structural_fields(tmp_path) -> None:
    path = tmp_path / "diagnostics.jsonl"
    diagnostics = HeadlessDiagnostics(path, SimpleNamespace())
    diagnostics.record_event(
        SimpleNamespace(
            type="status.changed",
            payload={
                "status": "terminal_cause",
                "cause_phase": "tool_intent_prepare",
                "cause_code": "commit_or_visibility_rejected",
                "untrusted_body": "secret",
            },
        )
    )
    diagnostics.close()
    assert json.loads(path.read_text()) == {
        "schema_version": 1,
        "kind": "run.terminal_cause",
        "cause_phase": "tool_intent_prepare",
        "cause_code": "commit_or_visibility_rejected",
    }
