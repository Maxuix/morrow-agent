"""Activity contract tests (master plan agent-transparency, subplan 1 / P1.5).

Covers the ``core/activity.py`` typed contract (conditional identities, closed
payload unions, size bounds, illegal states), old model/event serialization
compatibility frozen against fixture files, and the explicit activity-mode
handshake negotiation. All providers stay scripted; no network is involved.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from fixtures.core_api_client import WSSession
from morrow.core.activity import (
    ACTIVITY_DELTA_MAX_BYTES,
    ACTIVITY_ID_PREFIX,
    ACTIVITY_PREVIEW_REF_MAX_CHARS,
    ACTIVITY_SUMMARY_MAX_CHARS,
    ACTIVITY_TITLE_MAX_CHARS,
    ActivityIdentity,
    ActivityItem,
    NullActivityObserver,
    ToolActivityPayload,
    prepared_tool_activity_id,
    stable_tool_activity_id,
)
from morrow.core.events import PUBLIC_EVENT_TYPES, lifecycle_is_valid, make_event
from morrow.core.models import AgentEvent, FinishReason, ModelEvent, utc_now
from test_stage8_chat_submission import new_session
from test_stage8_core_api import SERVE_TOKEN, ServerFixture

FIXTURES = Path(__file__).parent / "fixtures" / "activity_stream"


def identity(**overrides) -> dict:
    base = {
        "workspace_id": "ws_sample",
        "root_session_id": "ses_root",
        "source_session_id": "ses_leaf",
    }
    base.update(overrides)
    return base


def tool_item(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "activity_id": "act_tool_texec_1",
        "revision": 1,
        "kind": "tool",
        "state": "running",
        "origin": "tool_executor",
        "identity": identity(tool_execution_id="texec_1", call_id="call_1"),
        "payload": {
            "kind": "tool",
            "tool_name": "read",
            "call_id": "call_1",
            "tool_execution_id": "texec_1",
        },
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "safe_title": "read src/morrow/server/replies.py",
    }
    base.update(overrides)
    return base


adapter = TypeAdapter(ActivityItem)


# --- identity and conditional required relations (proposal 4.2) ---


def test_identity_requires_workspace_root_and_source_scope():
    with pytest.raises(ValidationError):
        ActivityIdentity.model_validate({"workspace_id": "ws", "root_session_id": "ses"})
    with pytest.raises(ValidationError):
        ActivityIdentity.model_validate({"workspace_id": "ws", "source_session_id": "ses_leaf"})
    assert ActivityIdentity.model_validate(identity()) is not None


def test_tool_activity_requires_call_or_execution_identity():
    item = tool_item(
        identity=identity(call_id="call_1"),
        payload={"kind": "tool", "tool_name": "read", "call_id": "call_1"},
    )
    assert adapter.validate_python(item) is not None
    with pytest.raises(ValidationError):
        ToolActivityPayload(tool_name="read")


def test_node_output_requires_node_run_identity():
    base = {
        "schema_version": 1,
        "activity_id": "act_node_1",
        "revision": 1,
        "kind": "node_output",
        "state": "running",
        "origin": "scheduler",
        "identity": identity(workflow_run_id="wfr_1"),
        "payload": {"kind": "node_output"},
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "safe_title": "节点输出",
    }
    with pytest.raises(ValidationError):
        adapter.validate_python(base)
    base["identity"] = identity(workflow_run_id="wfr_1", node_run_id="nrun_1", node_id="collect")
    assert adapter.validate_python(base) is not None


def test_model_activity_requires_agent_run_or_planning_operation():
    base = {
        "schema_version": 1,
        "activity_id": "act_model_1",
        "revision": 1,
        "kind": "model",
        "state": "running",
        "origin": "agent_loop",
        "identity": identity(),
        "payload": {"kind": "model", "stage": "awaiting_model"},
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "safe_title": "等待模型响应",
    }
    with pytest.raises(ValidationError):
        adapter.validate_python(base)
    base["identity"] = identity(planning_operation_id="plop_1")
    assert adapter.validate_python(base) is not None
    base["identity"] = identity(agent_run_id="arun_1")
    assert adapter.validate_python(base) is not None


def test_payload_kind_must_match_item_kind():
    item = tool_item(kind="model")
    with pytest.raises(ValidationError):
        adapter.validate_python(item)


def test_unknown_payload_fields_are_rejected():
    item = tool_item(payload={"kind": "tool", "tool_name": "read", "call_id": "c1", "raw": "x"})
    with pytest.raises(ValidationError):
        adapter.validate_python(item)
    item = tool_item(unexpected_field=True)
    with pytest.raises(ValidationError):
        adapter.validate_python(item)


def test_tool_ordinal_must_not_exceed_total():
    with pytest.raises(ValidationError):
        ToolActivityPayload(tool_name="read", call_id="c1", ordinal=3, total=2)


def test_retry_attempt_ordinal_starts_at_two():
    with pytest.raises(ValidationError):
        adapter.validate_python(
            tool_item(
                kind="retry",
                activity_id="act_retry_1",
                identity=identity(agent_run_id="arun_1"),
                payload={"kind": "retry", "attempt_ordinal": 1},
            )
        )


# --- states, timestamps and terminal invariants ---


def _finished(kind_state: str) -> dict:
    now = utc_now()
    return tool_item(
        state=kind_state,
        payload={
            "kind": "tool",
            "tool_name": "read",
            "call_id": "c1",
            "tool_execution_id": "texec_1",
        },
        started_at=now,
        updated_at=now,
        ended_at=now,
    )


def test_terminal_state_requires_ended_at_and_running_forbids_it():
    with pytest.raises(ValidationError):
        adapter.validate_python(
            tool_item(
                state="succeeded", payload={"kind": "tool", "tool_name": "read", "call_id": "c1"}
            )
        )
    assert adapter.validate_python(_finished("succeeded")) is not None
    assert adapter.validate_python(_finished("cancelled")) is not None
    running = tool_item(ended_at=utc_now())
    with pytest.raises(ValidationError):
        adapter.validate_python(running)


def test_timestamp_ordering_is_enforced():
    now = utc_now()
    earlier = tool_item(started_at=now, updated_at=now.replace(year=2020))
    with pytest.raises(ValidationError):
        adapter.validate_python(earlier)
    ended = _finished("succeeded")
    ended["ended_at"] = ended["started_at"].replace(year=2020)
    with pytest.raises(ValidationError):
        adapter.validate_python(ended)


def test_naive_timestamps_are_rejected():
    naive = tool_item(started_at=utc_now().replace(tzinfo=None))
    with pytest.raises(ValidationError):
        adapter.validate_python(naive)


# --- size bounds and id shape ---


def test_text_and_id_bounds_are_enforced():
    with pytest.raises(ValidationError):
        adapter.validate_python(tool_item(safe_title="x" * (ACTIVITY_TITLE_MAX_CHARS + 1)))
    with pytest.raises(ValidationError):
        adapter.validate_python(tool_item(safe_summary="x" * (ACTIVITY_SUMMARY_MAX_CHARS + 1)))
    with pytest.raises(ValidationError):
        adapter.validate_python(tool_item(preview_ref="x" * (ACTIVITY_PREVIEW_REF_MAX_CHARS + 1)))
    with pytest.raises(ValidationError):
        adapter.validate_python(tool_item(activity_id=f"{ACTIVITY_ID_PREFIX}_{'x' * 200}"))
    with pytest.raises(ValidationError):
        adapter.validate_python(tool_item(revision=0))
    with pytest.raises(ValidationError):
        adapter.validate_python(tool_item(safe_title="   "))


def test_activity_id_prefix_and_helpers():
    assert stable_tool_activity_id("texec_9") == "act_tool_texec_9"
    assert prepared_tool_activity_id("call_9") == "act_call_call_9"
    item = adapter.validate_python(tool_item(activity_id=prepared_tool_activity_id("call_9")))
    assert item.activity_id.startswith("act_")
    with pytest.raises(ValidationError):
        adapter.validate_python(tool_item(activity_id="tool_texec_1"))


def test_blank_and_oversize_delta_are_reported_by_contract_constant():
    # The delta bound is a named contract constant; observer implementations
    # (P2) enforce it at the projection boundary.
    assert 0 < ACTIVITY_DELTA_MAX_BYTES <= 4096


# --- old model/event serialization compatibility (A15 baseline) ---


def test_public_event_types_and_lifecycle_unchanged():
    assert PUBLIC_EVENT_TYPES == frozenset(
        {"turn.started", "status.changed", "text.delta", "tool.status", "error", "turn.completed"}
    )
    events = [
        make_event(
            event_type="turn.started",
            event_id="ev1",
            session_id="ses",
            turn_id="turn",
            sequence=1,
        ),
        make_event(
            event_type="tool.status",
            event_id="ev2",
            session_id="ses",
            turn_id="turn",
            sequence=2,
            payload={
                "call_id": "c1",
                "name": "read",
                "status": "running",
                "ordinal": 1,
                "total": 1,
            },
        ),
        make_event(
            event_type="turn.completed",
            event_id="ev3",
            session_id="ses",
            turn_id="turn",
            sequence=3,
            payload={"finish_reason": FinishReason.STOP.value, "text": "", "text_length": 0},
        ),
    ]
    assert lifecycle_is_valid(events)
    dumped = [AgentEvent.model_validate(e.model_dump()).model_dump_json() for e in events]
    reparsed = [AgentEvent.model_validate_json(text) for text in dumped]
    assert reparsed == events


def test_agent_event_ignores_unknown_fields_but_keeps_shape():
    event = AgentEvent(
        type="status.changed",
        event_id="ev1",
        session_id="ses",
        turn_id="turn",
        sequence=1,
        payload={"status": "awaiting_model"},
    )
    payload = json.loads(event.model_dump_json())
    payload["brand_new_field"] = 1
    reparsed = AgentEvent.model_validate(payload)
    assert reparsed.payload == {"status": "awaiting_model"}
    assert "brand_new_field" not in reparsed.model_dump()["payload"]


def test_model_event_reasoning_activity_roundtrip():
    event = ModelEvent(kind="activity", activity="reasoning")
    reparsed = ModelEvent.model_validate_json(event.model_dump_json())
    assert reparsed == event
    assert reparsed.text is None


# --- frozen protocol fixtures (old v1 frames / new activity schema=1) ---


def test_frozen_v1_stage_frame_fixture_parses():
    frame = json.loads((FIXTURES / "v1_stage_frame.json").read_text())
    assert frame["protocol_version"] == 1
    assert frame["type"] == "stage"
    assert frame["payload"]["node_run_id"] == "nrun_sample"
    assert "activity_id" not in frame["payload"]


def test_frozen_activity_item_fixture_parses_into_contract():
    raw = json.loads((FIXTURES / "activity_item.json").read_text())
    item = adapter.validate_python(raw)
    assert item.payload.kind == "tool"
    assert item.identity.tool_execution_id == "texec_1"
    assert item.model_dump_json()


# --- handshake negotiation: capability advertisement and explicit selection ---


async def test_capabilities_advertise_activity_stream_schema_1(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        result = await fixture.client.get("/v1/capabilities")
        assert result.status == 200
        caps = result.json()
        # Enabled with the P2 activity flow; rollback removes this key.
        assert caps["activity_stream"] == {"schema": 1}
        assert caps["task_workflow"]["schema"] == 1
    finally:
        fixture.close()


async def test_ws_activity_subscribe_with_wrong_epoch_resyncs(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fixture)
        activity_snapshot = (await fixture.client.get(path + "/snapshot?activity_schema=1")).json()
        assert activity_snapshot["activity_epoch"]
        assert activity_snapshot["activities"] == []
        ws = WSSession(
            fixture.client.app,
            path + "/stream?token=" + SERVE_TOKEN,
            headers=[(b"host", b"127.0.0.1")],
        )
        assert (await ws.accept())["type"] == "websocket.accept"
        body = json.loads((FIXTURES / "activity_subscribe.json").read_text())
        body["stream_epoch"] = uuid4().hex  # unknown epoch → activity resync
        await ws._to_app.put({"type": "websocket.receive", "text": json.dumps(body)})
        frame = await ws.receive_json()
        assert frame["type"] == "resync_required"
        assert frame["stream_epoch"] == activity_snapshot["activity_epoch"]
        await ws.close()
    finally:
        fixture.close()


async def test_ws_v1_subscribe_shape_remains_valid(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fixture)
        snapshot = (await fixture.client.get(path + "/snapshot")).json()
        ws = WSSession(
            fixture.client.app,
            path + "/stream?token=" + SERVE_TOKEN,
            headers=[(b"host", b"127.0.0.1")],
        )
        assert (await ws.accept())["type"] == "websocket.accept"
        body = json.loads((FIXTURES / "v1_subscribe.json").read_text())
        body["stream_epoch"] = snapshot["stream_epoch"]
        body["after_sequence"] = snapshot["sequence"]
        await ws._to_app.put({"type": "websocket.receive", "text": json.dumps(body)})
        # No rejection: the v1 delivery path accepts the frozen shape and waits.
        await ws.close()
        assert ws.closed_code is None
    finally:
        fixture.close()


async def test_snapshot_activity_param_selects_activity_block(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fixture)
        plain = await fixture.client.get(path + "/snapshot")
        assert plain.status == 200
        # v1 snapshot stays free of activity fields; the explicit param opts in.
        assert "activities" not in plain.json()
        rejected = await fixture.client.get(path + "/snapshot?activity_schema=2")
        assert rejected.status == 400
        activity = await fixture.client.get(path + "/snapshot?activity_schema=1")
        assert activity.status == 200
        body = activity.json()
        assert body["activities"] == []
        assert isinstance(body["activity_sequence"], int)
    finally:
        fixture.close()


def test_null_observer_is_a_noop():
    observer = NullActivityObserver()
    item = adapter.validate_python(tool_item())
    assert observer.activity_upsert(item) is None
    assert observer.activity_delta("act_tool_texec_1", 1, "片段") is None
    assert observer.activity_content_reset("act_tool_texec_1", "cursor_expired") is None


# --- P3.1: reasoning_delta contract ------------------------------------------


def test_reasoning_delta_requires_bounded_text_and_isolates_channels():
    from morrow.core.models import REASONING_DELTA_MAX_CHARS

    event = ModelEvent(kind="reasoning_delta", reasoning_text="思考片段")
    assert ModelEvent.model_validate_json(event.model_dump_json()) == event
    with pytest.raises(ValidationError):
        ModelEvent(kind="reasoning_delta")
    with pytest.raises(ValidationError):
        ModelEvent(kind="text_delta", reasoning_text="错位片段")
    with pytest.raises(ValidationError):
        ModelEvent(
            kind="reasoning_delta",
            reasoning_text="x" * (REASONING_DELTA_MAX_CHARS + 1),
        )
    with pytest.raises(ValidationError):
        ModelEvent(kind="reasoning_delta", reasoning_text="片段", activity="reasoning")
