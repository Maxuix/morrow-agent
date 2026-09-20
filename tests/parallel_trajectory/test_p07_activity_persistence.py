"""P07 acceptance: durable safe content, commit-then-send, recovery.

Reuses the P06 isolated-store fixture (the current production schema).
Planning projections go through the real journal transaction and
the durable planning events table; no mocks except the lightweight stream
manager stub (the real SessionRuntimeManager needs a full application).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from morrow.core.application import ApplicationError
from morrow.server.replies import ReplyStreams
from morrow.server.workflow_stream import WorkflowStreamBridge
from parallel_trajectory.test_p06_timeline_index import (
    SESSION,
    WORKSPACE,
    build_journal,
    make_index,
    seed_session,
)

CONTENT_CAP = 65536


def make_streams(journal, index=None) -> ReplyStreams:
    manager = SimpleNamespace(
        require_session=lambda sid: None,
        drivers=set(),
        timeline=SimpleNamespace(index=index) if index is not None else None,
    )
    return ReplyStreams(manager, journal, WORKSPACE)


def test_persist_then_read_round_trip_with_bounded_offsets(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    ref = index.persist_activity_content(
        SESSION, "act_call_c1", "读取文件中", kind="tool", revision=1
    )
    assert ref.availability == "committed"
    assert ref.committed_offset == len("读取文件中".encode())
    assert ref.total_length == ref.committed_offset
    ref2 = index.persist_activity_content(
        SESSION, "act_call_c1", "，输出完成", kind="tool", revision=2
    )
    assert ref2.committed_offset == ref2.total_length
    result = index.read_activity_content(SESSION, "act_call_c1")
    assert result["body"] == "读取文件中，输出完成"
    assert not result["truncated"]
    assert result["ref"].model_dump(mode="json")["availability"] == "committed"


def test_content_cap_keeps_bounded_body_and_true_total(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    for _chunk in range(8):
        index.persist_activity_content(
            SESSION, "act_call_big", "x" * 16384, kind="tool", revision=1
        )
    result = index.read_activity_content(SESSION, "act_call_big")
    assert len(result["body"].encode()) <= CONTENT_CAP
    assert result["ref"].committed_offset == CONTENT_CAP
    assert result["ref"].total_length == 8 * 16384  # more existed than stored
    # Nothing after the cap is silently appended: offset stops at the cap.
    index.persist_activity_content(SESSION, "act_call_big", "y", kind="tool", revision=1)
    after = index.read_activity_content(SESSION, "act_call_big")
    assert after["ref"].committed_offset == CONTENT_CAP


def test_activity_delta_commits_before_broadcast(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    streams = make_streams(journal, index)
    emitted = []
    streams._emit_activity = (  # type: ignore[method-assign]
        lambda sid, state, kind, payload: emitted.append((kind, payload))
    )

    streams.activity_upsert(SESSION, _tool_item("act_call_c9", revision=1))
    streams.activity_delta(SESSION, "act_call_c9", "安全片段")

    delta_frames = [payload for kind, payload in emitted if kind == "activity_delta"]
    assert delta_frames and delta_frames[0]["availability"] == "committed"
    assert delta_frames[0]["delta"] == "安全片段"
    entry = streams.state(SESSION).activities.entries["act_call_c9"]
    assert entry.content == "安全片段"
    assert entry.item["content_ref"].endswith("/activity-content/act_call_c9")
    assert entry.item["preview_ref"] is None  # JSON read path, not a binary artifact
    durable = index.read_activity_content(SESSION, "act_call_c9")
    assert durable is not None and durable["body"] == "安全片段"


def _observation(phase: str, *, tool_execution_id=None, disposition=None) -> dict:
    return {
        "call_id": "c1",
        "tool_name": "read_file",
        "ordinal": 1,
        "total": 1,
        "phase": phase,
        "timestamp": "2026-09-09T12:00:00Z",
        "tool_execution_id": tool_execution_id,
        "disposition": disposition,
    }


def test_tool_observation_emits_rekey_before_stable_upsert(tmp_path):
    """Admission rekeys the stream: rekey frame first, stable upsert second,
    and late output deltas follow the stable act_tool_* identity."""
    from morrow.server.replies import SessionReasoningObserver

    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    streams = make_streams(journal, index)
    emitted = []
    streams._emit_activity = (  # type: ignore[method-assign]
        lambda sid, state, kind, payload: emitted.append((kind, payload))
    )

    streams.tool_observation(SESSION, _observation("prepared"))
    streams.tool_observation(SESSION, _observation("executing", tool_execution_id="tex_1"))

    assert [kind for kind, _ in emitted] == [
        "activity_upsert",
        "activity_rekey",
        "activity_upsert",
    ]
    assert emitted[1][1] == {"from_activity_id": "act_call_c1", "to_activity_id": "act_tool_tex_1"}
    entry = streams.state(SESSION).activities.entries
    assert "act_call_c1" not in entry
    assert entry["act_tool_tex_1"].item["payload"]["tool_execution_id"] == "tex_1"

    observer = SessionReasoningObserver(streams, WORKSPACE, SESSION)
    observer.tool_output(call_id="c1", text="输出片段")
    assert streams.state(SESSION).activities.entries["act_tool_tex_1"].content == "输出片段"


def test_recovery_backfills_tool_skeleton_content_ref(tmp_path):
    """Cold-start recovery rebuilds durable tool skeletons; executions with
    persisted content get their content ref and committed availability."""
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    index.persist_activity_content(SESSION, "act_tool_tex_1", "输出片段", kind="tool", revision=1)
    skeleton = {
        **_tool_item("act_tool_tex_1"),
        "state": "succeeded",
        "ended_at": "2026-09-09T12:00:05Z",
        "origin": "tool_executor",
        "identity": {
            "workspace_id": WORKSPACE,
            "root_session_id": SESSION,
            "source_session_id": SESSION,
            "tool_execution_id": "tex_1",
            "call_id": "c1",
        },
        "availability": "unsaved",
    }
    facts = {"items": [skeleton], "truncated": False}
    manager = SimpleNamespace(
        require_session=lambda sid: None,
        drivers=set(),
        timeline=SimpleNamespace(
            index=index,
            tool_activities=lambda sid, limit=128: facts,
        ),
    )
    streams = ReplyStreams(manager, journal, WORKSPACE)
    entries = streams.state(SESSION).activities.entries
    entry = entries["act_tool_tex_1"]
    assert entry.item["content_ref"].endswith("/activity-content/act_tool_tex_1")
    assert entry.item["availability"] == "committed"


def test_recovery_keeps_tool_skeleton_unsaved_without_content(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    skeleton = {
        **_tool_item("act_tool_tex_2"),
        "state": "succeeded",
        "ended_at": "2026-09-09T12:00:05Z",
        "origin": "tool_executor",
        "identity": {
            "workspace_id": WORKSPACE,
            "root_session_id": SESSION,
            "source_session_id": SESSION,
            "tool_execution_id": "tex_2",
            "call_id": "c2",
        },
        "availability": "unsaved",
    }
    facts = {"items": [skeleton], "truncated": False}
    manager = SimpleNamespace(
        require_session=lambda sid: None,
        drivers=set(),
        timeline=SimpleNamespace(
            index=index,
            tool_activities=lambda sid, limit=128: facts,
        ),
    )
    streams = ReplyStreams(manager, journal, WORKSPACE)
    entry = streams.state(SESSION).activities.entries["act_tool_tex_2"]
    assert entry.item["preview_ref"] is None
    assert entry.item["content_ref"] is None
    assert entry.item["availability"] == "unsaved"


def test_persist_failure_never_claims_durability(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    streams = make_streams(journal, index)
    streams.activity_upsert(SESSION, _tool_item("act_call_c10", revision=1))

    def broken(*args, **kwargs):
        raise RuntimeError("storage unavailable")

    index.persist_activity_content = broken  # type: ignore[method-assign]
    emitted = []
    streams._emit_activity = (  # type: ignore[method-assign]
        lambda sid, state, kind, payload: emitted.append(payload)
    )
    streams.activity_delta(SESSION, "act_call_c10", "未保存片段")
    assert emitted[0]["availability"] == "unsaved"
    state = streams.state(SESSION)
    assert state.activities.drops.get("content_persist_failed") == 1
    entry = state.activities.entries["act_call_c10"]
    assert entry.item["content_ref"] is None  # never points at a durable ref


def test_planning_projection_runs_only_after_commit(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    streams = make_streams(journal, index)
    bridge = WorkflowStreamBridge(SimpleNamespace(streams=streams), journal, WORKSPACE)
    journal.workflows.planning.activity_listener = bridge.planning_event

    def rolled_back(_):
        journal.workflows.planning.event(
            WORKSPACE, SESSION, {"operation_id": "plop_rb", "stage": "awaiting_model", "attempt": 1}
        )
        raise RuntimeError("planning fact rolled back")

    with pytest.raises(RuntimeError):
        journal.transact(rolled_back)
    assert streams.state(SESSION).activities.entries == {}

    def committed(_):
        journal.workflows.planning.event(
            WORKSPACE, SESSION, {"operation_id": "plop_ok", "stage": "awaiting_model", "attempt": 1}
        )

    journal.transact(committed)
    entries = streams.state(SESSION).activities.entries
    assert "act_model_plop_plop_ok_1" in entries
    assert entries["act_model_plop_plop_ok_1"].item["state"] == "running"
    assert "act_model_plop_plop_rb_1" not in entries


def test_recovery_replays_events_and_marks_evidence_free_attempts_unknown(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)

    def seed_events(_):
        planning = journal.workflows.planning
        planning.event(
            WORKSPACE,
            SESSION,
            {
                "operation_id": "plop_closed",
                "stage": "awaiting_model",
                "attempt": 1,
                "session_id": SESSION,
                "started_at": "2026-09-09T12:00:00Z",
            },
        )
        planning.event(
            WORKSPACE,
            SESSION,
            {
                "operation_id": "plop_closed",
                "stage": "operation_terminal",
                "status": "succeeded",
                "session_id": SESSION,
                "ended_at": "2026-09-09T12:00:05Z",
            },
        )
        planning.event(
            WORKSPACE,
            SESSION,
            {
                "operation_id": "plop_open",
                "stage": "awaiting_model",
                "attempt": 2,
                "session_id": SESSION,
                "started_at": "2026-09-09T12:00:10Z",
            },
        )

    journal.transact(seed_events)
    index.persist_activity_content(
        SESSION, "act_model_plop_plop_open_2", "规划等待片段", kind="model", revision=1
    )

    # Cold start: a fresh stream with empty memory rebuilds from durable facts.
    streams = make_streams(journal, index)
    entries = streams.state(SESSION).activities.entries
    closed = entries["act_model_plop_plop_closed_1"]
    assert closed.item["state"] == "succeeded" and closed.item["ended_at"] is not None
    opened = entries["act_model_plop_plop_open_2"]
    assert opened.item["state"] == "unknown"
    assert opened.item["ended_at"] is None  # no fabricated end, no ghost timer
    assert opened.item["started_at"] == "2026-09-09T12:00:10Z"  # timing from the durable fact
    assert opened.item["content_ref"].endswith("/activity-content/act_model_plop_plop_open_2")


def test_activity_content_authorization_follows_lineage_root(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    index.persist_activity_content(SESSION, "act_call_leaf1", "叶子片段", kind="tool", revision=1)
    result = index.read_activity_content_authorized(SESSION, "act_call_leaf1")
    assert result["body"] == "叶子片段"
    with pytest.raises(ApplicationError):
        index.read_activity_content_authorized("ses_stranger1", "act_call_leaf1")


def _tool_item(activity_id: str, *, revision: int = 1) -> dict:
    from morrow.core.activity import stable_tool_activity_id

    del stable_tool_activity_id
    return {
        "schema_version": 1,
        "activity_id": activity_id,
        "revision": revision,
        "kind": "tool",
        "state": "running",
        "origin": "tool_executor",
        "identity": {
            "workspace_id": WORKSPACE,
            "root_session_id": SESSION,
            "source_session_id": SESSION,
            "call_id": activity_id.replace("act_call_", ""),
        },
        "payload": {"kind": "tool", "tool_name": "read_file"},
        "started_at": "2026-09-09T12:00:00Z",
        "updated_at": "2026-09-09T12:00:00Z",
        "ended_at": None,
        "last_activity_at": None,
        "safe_title": "读取文件",
        "safe_summary": None,
        "preview_ref": None,
        "content_ref": None,
        "truncated": False,
        "availability": "live",
    }


def test_model_finished_carries_the_real_outcome(tmp_path):
    """B-line coordination: model_finished.status is the real outcome now."""
    from morrow.server.workflow_stream import PlanningActivityReducer

    reducer = PlanningActivityReducer(WORKSPACE)
    closed = []
    for payload in (
        {"operation_id": "plop_a", "stage": "awaiting_model", "attempt": 1, "session_id": SESSION},
        {
            "operation_id": "plop_a",
            "stage": "model_finished",
            "attempt": 1,
            "status": "interrupted",
            "ended_at": "2026-09-09T12:00:05Z",
        },
    ):
        closed.extend(reducer.apply(payload))
    # A user interruption is a cancellation, never a fabricated failure.
    assert closed[-1]["state"] == "cancelled"
    assert closed[-1]["ended_at"] == "2026-09-09T12:00:05Z"

    legacy = PlanningActivityReducer(WORKSPACE)
    for payload in (
        {"operation_id": "plop_b", "stage": "awaiting_model", "attempt": 1, "session_id": SESSION},
        {"operation_id": "plop_b", "stage": "model_finished", "attempt": 1, "status": "finished"},
    ):
        legacy.apply(payload)
    assert legacy.apply({"operation_id": "plop_b", "stage": "noop"}) == []


def test_request_outcome_model_request_layer_closes_the_wait(tmp_path):
    from morrow.server.workflow_stream import PlanningActivityReducer

    reducer = PlanningActivityReducer(WORKSPACE)
    items: list[dict] = []
    for payload in (
        {"operation_id": "plop_c", "stage": "awaiting_model", "attempt": 2, "session_id": SESSION},
        {
            "operation_id": "plop_c",
            "stage": "request_outcome",
            "attempt": 2,
            "layer": "candidate_validation",
            "outcome": "invalid",
        },
        {
            "operation_id": "plop_c",
            "stage": "request_outcome",
            "attempt": 2,
            "layer": "model_request",
            "outcome": "succeeded",
            "ended_at": "2026-09-09T12:00:07Z",
        },
    ):
        items.extend(reducer.apply(payload))
    # The validation layer owns no wait item; only the model-request layer closes.
    assert [i["state"] for i in items] == ["running", "succeeded"]
    assert items[-1]["revision"] == 2


def test_pause_parks_without_ghost_timer_and_resume_reopens(tmp_path):
    from morrow.server.workflow_stream import PlanningActivityReducer

    reducer = PlanningActivityReducer(WORKSPACE)
    items: list[dict] = []
    for payload in (
        {"operation_id": "plop_d", "stage": "awaiting_model", "attempt": 1, "session_id": SESSION},
        {
            "operation_id": "plop_d",
            "stage": "planning_pause",
            "attempt": 1,
            "reason": "user_interrupt",
        },
        {
            "operation_id": "plop_d",
            "stage": "planning_resume",
            "attempt": 1,
            "started_at": "2026-09-09T12:01:00Z",
        },
    ):
        items.extend(reducer.apply(payload))
    assert items[1]["state"] == "unknown" and items[1]["ended_at"] is None
    assert items[1]["safe_title"] == "任务规划已暂停"
    assert items[2]["state"] == "running"
    assert items[2]["started_at"] == "2026-09-09T12:01:00Z"  # timing from the durable fact
    assert [i["revision"] for i in items] == [1, 2, 3]  # monotonic, no regression

    # A late awaiting_model for the same attempt is a stale-revision drop for
    # consumers, and the operation terminal still closes the resumed wait.
    closed = reducer.apply(
        {
            "operation_id": "plop_d",
            "stage": "operation_terminal",
            "status": "succeeded",
            "ended_at": "2026-09-09T12:02:00Z",
        }
    )
    assert [i["state"] for i in closed] == ["succeeded"]
    assert closed[0]["revision"] == 4


def test_operation_terminal_closes_parked_attempts(tmp_path):
    from morrow.server.workflow_stream import PlanningActivityReducer

    reducer = PlanningActivityReducer(WORKSPACE)
    items: list[dict] = []
    for payload in (
        {"operation_id": "plop_e", "stage": "awaiting_model", "attempt": 1, "session_id": SESSION},
        {"operation_id": "plop_e", "stage": "planning_pause", "attempt": 1},
        {"operation_id": "plop_e", "stage": "operation_terminal", "status": "cancelled"},
    ):
        items.extend(reducer.apply(payload))
    assert [i["state"] for i in items] == ["running", "unknown", "cancelled"]
    # Evidence-free recovery close only touches genuinely open attempts.
    assert reducer.close_open_without_evidence() == []
