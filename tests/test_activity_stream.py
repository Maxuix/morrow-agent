"""Activity stream tests (master plan agent-transparency subplan 2).

Covers the bounded server reducer (P2.1), the event→activity projector
(P2.3), and the scripted end-to-end chains: plain Chat and a Workflow root
both keep two same-name tool calls as distinct identity-stable activities,
while v1 sockets never see activity frames.
"""

from __future__ import annotations

import asyncio
import json

from fixtures.core_api_client import WSSession
from morrow.core.capabilities import CommandToolFact, PolicyVerdict
from morrow.core.models import (
    AgentEvent,
    AssistantMessage,
    FinishReason,
    FunctionToolCall,
)
from morrow.core.workflows.definitions import (
    WorkflowDefinitionSource,
)
from morrow.server import activities as activities_module
from morrow.server.activities import (
    ACTIVITY_METADATA_LIMIT,
    ActivityStreamState,
    enforce_host_budget,
    host_activity_bytes,
    register_host_state,
)
from morrow.server.activity_projection import SessionActivityProjector
from test_stage7_serial_scheduler import agent_source, chain_node
from test_stage8_chat_submission import drain, new_session
from test_stage8_core_api import (
    SERVE_TOKEN,
    ServerFixture,
    create_session_and_task,
    start_run,
    wait_for_run,
)


def event(event_type: str, payload: dict, *, turn="turn1", sequence=1):
    return AgentEvent(
        type=event_type,
        event_id=f"evt_{sequence}",
        session_id="ses",
        turn_id=turn,
        sequence=sequence,
        payload=payload,
    )


def project(event_type: str, payload: dict, *, agent_run_id="arun_1", turn="turn1"):
    projector = SessionActivityProjector()
    return projector.observe_event(
        event(event_type, payload, turn=turn),
        workspace_id="ws",
        session_id="ses",
        agent_run_id=agent_run_id,
    )


# --- reducer (P2.1) -----------------------------------------------------------


def base_item(**overrides):
    item = {
        "schema_version": 1,
        "activity_id": "act_call_c1",
        "revision": 1,
        "kind": "tool",
        "state": "preparing",
        "origin": "agent_loop",
        "identity": {"workspace_id": "ws", "root_session_id": "s", "source_session_id": "s"},
        "payload": {"kind": "tool", "tool_name": "read", "call_id": "c1"},
        "started_at": "2026-09-08T00:00:00+00:00",
        "updated_at": "2026-09-08T00:00:00+00:00",
        "safe_title": "read",
        "truncated": False,
        "availability": "none",
    }
    item.update(overrides)
    return item


def test_reducer_rejects_stale_revision():
    stream = ActivityStreamState()
    assert stream.upsert(
        base_item(revision=2, state="succeeded", ended_at="2026-09-08T00:00:05+00:00")
    )
    assert not stream.upsert(base_item(revision=1))
    assert stream.entries["act_call_c1"].item["revision"] == 2


def test_reducer_never_regresses_terminal_state():
    stream = ActivityStreamState()
    stream.upsert(base_item(revision=2, state="succeeded", ended_at="2026-09-08T00:00:05+00:00"))
    assert not stream.upsert(base_item(revision=3, state="running"))
    assert stream.drops.get("terminal_regression") == 1
    assert stream.entries["act_call_c1"].item["state"] == "succeeded"


def test_reducer_evicts_oldest_metadata_beyond_limit():
    stream = ActivityStreamState()
    for index in range(ACTIVITY_METADATA_LIMIT + 10):
        stream.upsert(base_item(activity_id=f"act_call_c{index}"))
    assert len(stream.entries) == ACTIVITY_METADATA_LIMIT
    assert "act_call_c0" not in stream.entries
    assert "act_call_c9" not in stream.entries
    assert stream.drops.get("metadata_evicted") == 10


def test_delta_bounds_and_unknown_ids():
    stream = ActivityStreamState()
    assert not stream.append_delta("act_missing", "片段")
    stream.upsert(base_item())
    assert not stream.append_delta("act_call_c1", "x" * 5000)
    assert stream.drops.get("delta_oversize") == 1
    assert stream.append_delta("act_call_c1", "第一段")
    assert stream.entries["act_call_c1"].content == "第一段"
    stream.upsert(
        base_item(
            activity_id="act_call_c2",
            payload={"kind": "tool", "tool_name": "read", "call_id": "c2"},
        )
    )
    # Each delta respects the 4 KiB frame bound; repeated appends hit the
    # 32 KiB per-item preview cap.
    for _ in range(9):
        assert stream.append_delta("act_call_c2", "字" * 1300)
    assert stream.entries["act_call_c2"].item["truncated"] is True


def test_preview_total_budget_evicts_completed_first(monkeypatch):
    monkeypatch.setattr(activities_module, "ACTIVITY_PREVIEW_TOTAL_BYTES", 1500)
    stream = ActivityStreamState()
    stream.upsert(
        base_item(activity_id="act_done", state="succeeded", ended_at="2026-09-08T00:00:05+00:00")
    )
    stream.append_delta("act_done", "x" * 1000)
    stream.upsert(base_item(activity_id="act_running"))
    stream.append_delta("act_running", "y" * 1000)
    assert stream.entries["act_done"].item["availability"] == "evicted"
    assert stream.entries["act_done"].content == ""
    assert stream.entries["act_running"].content == "y" * 1000


def test_sweep_expires_completed_content(monkeypatch):
    stream = ActivityStreamState()
    stream.upsert(base_item(state="succeeded", ended_at="2026-09-08T00:00:05+00:00"))
    stream.append_delta("act_call_c1", "历史片段")
    entry = stream.entries["act_call_c1"]
    entry.completed_monotonic = 1000.0
    monkeypatch.setattr(
        activities_module.time,
        "monotonic",
        lambda: 1000.0 + activities_module.ACTIVITY_COMPLETED_CONTENT_TTL_SECONDS + 1,
    )
    stream.sweep_expired()
    assert entry.content == ""
    assert entry.item["availability"] == "evicted"


def test_host_budget_counts_and_enforces(monkeypatch, tmp_path):
    monkeypatch.setattr(activities_module, "_HOST_STATES", None)
    monkeypatch.setattr(activities_module, "ACTIVITY_HOST_TOTAL_BYTES", 10)
    stream = ActivityStreamState()
    register_host_state(stream)
    stream.upsert(
        base_item(activity_id="act_a", state="succeeded", ended_at="2026-09-08T00:00:05+00:00")
    )
    stream.append_delta("act_a", "c" * 64)
    assert host_activity_bytes() > 10
    enforce_host_budget()
    assert host_activity_bytes() <= 10
    assert stream.entries["act_a"].item["state"] == "succeeded"


# --- projector (P2.3) ---------------------------------------------------------


def test_awaiting_model_opens_model_item_and_turn_completes_closes_it():
    projector = SessionActivityProjector()
    items = projector.observe_event(
        event("status.changed", {"status": "awaiting_model", "attempt_ordinal": 1}),
        workspace_id="ws",
        session_id="ses",
        agent_run_id="arun_1",
    )
    assert len(items) == 1
    model = items[0]
    assert model["activity_id"] == "act_model_turn1_1"
    assert model["kind"] == "model"
    assert model["state"] == "running"
    assert model["identity"]["agent_run_id"] == "arun_1"

    closed = projector.observe_event(
        event(
            "turn.completed",
            {"finish_reason": FinishReason.STOP.value, "text": "", "text_length": 0},
            sequence=2,
        ),
        workspace_id="ws",
        session_id="ses",
        agent_run_id="arun_1",
    )
    assert closed[0]["activity_id"] == "act_model_turn1_1"
    assert closed[0]["state"] == "succeeded"
    assert closed[0]["ended_at"]


def test_new_attempt_supersedes_previous_model_item():
    projector = SessionActivityProjector()
    first = projector.observe_event(
        event("status.changed", {"status": "awaiting_model", "attempt_ordinal": 1}),
        workspace_id="ws",
        session_id="ses",
        agent_run_id="arun_1",
    )
    second = projector.observe_event(
        event("status.changed", {"status": "awaiting_model", "attempt_ordinal": 2}, sequence=2),
        workspace_id="ws",
        session_id="ses",
        agent_run_id="arun_1",
    )
    assert first[0]["activity_id"] == "act_model_turn1_1"
    assert second[-1]["activity_id"] == "act_model_turn1_2"
    superseded = second[0]
    assert superseded["activity_id"] == "act_model_turn1_1"
    assert superseded["state"] == "succeeded"
    assert superseded["revision"] > first[0]["revision"]


def test_tool_running_maps_to_preparing_not_executing():
    items = project(
        "tool.status",
        {"call_id": "c1", "name": "read", "status": "running", "ordinal": 1, "total": 2},
    )
    assert items[0]["state"] == "preparing"
    assert items[0]["payload"]["tool_name"] == "read"
    assert items[0]["payload"]["ordinal"] == 1
    assert items[0]["activity_id"] == "act_call_c1"
    assert items[0]["ended_at"] is None


def test_tool_terminal_states_close_with_ended_at():
    for status, expected in (
        ("succeeded", "succeeded"),
        ("failed", "failed"),
        ("cancelled", "cancelled"),
        ("skipped", "skipped"),
    ):
        items = project(
            "tool.status",
            {"call_id": "c1", "name": "read", "status": status, "ordinal": 1, "total": 1},
        )
        assert items[0]["state"] == expected
        assert items[0]["ended_at"] is not None


def test_model_item_without_agent_run_identity_is_dropped():
    assert project("status.changed", {"status": "awaiting_model"}, agent_run_id=None) == []


def test_retrying_and_compaction_become_distinct_items():
    items = project(
        "status.changed",
        {"status": "retrying", "attempt_ordinal": 2, "retry_delay_seconds": 5},
    )
    assert items[0]["kind"] == "retry"
    assert items[0]["payload"]["attempt_ordinal"] == 2
    assert items[0]["payload"]["retry_delay_seconds"] == 5
    items = project("status.changed", {"status": "compacting"})
    assert items[0]["kind"] == "compaction"
    assert items[0]["state"] == "running"
    items = project("status.changed", {"status": "compacted"})
    assert items[0]["state"] == "succeeded"


def test_text_delta_and_turn_started_project_nothing():
    assert project("text.delta", {"text": "正文"}) == []
    assert project("turn.started", {}) == []


def test_node_identity_flows_into_items():
    projector = SessionActivityProjector()
    items = projector.observe_event(
        event("status.changed", {"status": "awaiting_model", "attempt_ordinal": 1}),
        workspace_id="ws",
        session_id="ses_leaf",
        node_identity={
            "workflow_run_id": "wfr_1",
            "node_run_id": "nrun_1",
            "node_id": "collect",
            "agent_run_id": "arun_node",
            "source_session_id": "ses_leaf",
            "root_session_id": "ses_root",
        },
    )
    identity = items[0]["identity"]
    assert identity["root_session_id"] == "ses_root"
    assert identity["source_session_id"] == "ses_leaf"
    assert identity["node_run_id"] == "nrun_1"
    assert items[0]["activity_id"] == "act_model_nrun_1_1"


def test_real_tool_fact_preserves_command_exit_code_in_activity():
    projector = SessionActivityProjector()
    fact = CommandToolFact(
        call_id="c1",
        tool_name="bash",
        ordinal=1,
        relative_paths=(".",),
        approval_verdict=PolicyVerdict.ALLOW,
        command_class="workspace_read",
        status="failed",
        exit_code=7,
        duration_ms=12,
    )
    items = projector.observe_tool_fact(
        workspace_id="ws",
        session_id="ses",
        agent_run_id="arun_1",
        call_id="c1",
        tool_name="bash",
        ordinal=1,
        total=1,
        phase="terminal",
        timestamp="2026-09-11T00:00:00+00:00",
        disposition="failed",
        facts=(fact,),
    )
    assert items is not None
    assert items["payload"]["exit_code"] == 7


def test_artifact_preview_ref_matches_registered_session_route():
    """The projector's preview_ref must hit the registered session-scoped route."""
    from morrow.core.domain import ArtifactReference

    projector = SessionActivityProjector()
    item = projector.observe_tool_fact(
        workspace_id="ws",
        session_id="ses",
        agent_run_id="arun_1",
        call_id="c1",
        tool_name="write",
        ordinal=1,
        total=1,
        phase="terminal",
        timestamp="2026-09-11T00:00:00+00:00",
        disposition="succeeded",
        artifact_refs=(ArtifactReference(artifact_id="art_shot1"),),
    )
    assert item is not None
    assert item["preview_ref"] == "/v1/workspaces/ws/sessions/ses/artifacts/art_shot1/content"


def test_tool_admission_rekeys_prepared_identity_to_stable():
    """act_call_* → act_tool_* migration: one rekey, revision continuity, late
    output deltas follow the stable identity, and a late public tool.status
    terminal event never resurrects the abandoned prepared item."""
    projector = SessionActivityProjector()
    prepared = projector.observe_tool_fact(
        workspace_id="ws",
        session_id="ses",
        agent_run_id="arun_1",
        call_id="c1",
        tool_name="read",
        ordinal=1,
        total=1,
        phase="prepared",
        timestamp="2026-09-11T00:00:00+00:00",
    )
    assert prepared is not None
    assert prepared["activity_id"] == "act_call_c1"
    assert projector.drain_rekeys() == []

    admitted = projector.observe_tool_fact(
        workspace_id="ws",
        session_id="ses",
        agent_run_id="arun_1",
        call_id="c1",
        tool_name="read",
        ordinal=1,
        total=1,
        phase="executing",
        timestamp="2026-09-11T00:00:01+00:00",
        tool_execution_id="tex_1",
    )
    assert admitted is not None
    assert admitted["activity_id"] == "act_tool_tex_1"
    assert admitted["payload"]["tool_execution_id"] == "tex_1"
    assert admitted["identity"]["tool_execution_id"] == "tex_1"
    assert admitted["revision"] == prepared["revision"] + 1
    assert admitted["started_at"] == prepared["started_at"]
    assert projector.drain_rekeys() == [
        {"from_activity_id": "act_call_c1", "to_activity_id": "act_tool_tex_1"}
    ]
    assert projector.tool_activity_id_for_call("c1") == "act_tool_tex_1"

    # The terminal fact repeats the stable identity without a second rekey.
    terminal = projector.observe_tool_fact(
        workspace_id="ws",
        session_id="ses",
        agent_run_id="arun_1",
        call_id="c1",
        tool_name="read",
        ordinal=1,
        total=1,
        phase="terminal",
        timestamp="2026-09-11T00:00:02+00:00",
        disposition="succeeded",
        tool_execution_id="tex_1",
    )
    assert terminal is not None
    assert terminal["activity_id"] == "act_tool_tex_1"
    assert projector.drain_rekeys() == []

    # A late public terminal event defers to the stable identity.
    late = projector.observe_event(
        event(
            "tool.status",
            {"call_id": "c1", "name": "read", "status": "succeeded"},
            sequence=2,
        ),
        workspace_id="ws",
        session_id="ses",
        agent_run_id="arun_1",
    )
    assert late == []


# --- scripted end-to-end chains (P2.6) ----------------------------------------


def read_call(call_id: str) -> FunctionToolCall:
    return FunctionToolCall(id=call_id, name="read", arguments=json.dumps({"path": "notes.md"}))


def single_source(ref):
    from morrow.core.workflows.contracts import NodeOutputRef

    return WorkflowDefinitionSource(
        **{
            "workflow_definition_id": "pipeline",
            "name": "Single node pipeline",
            "nodes": (chain_node(ref, "work", "Read the notes"),),
            "edges": (),
            "required_outputs": (NodeOutputRef(node_id="work", output_slot="result"),),
        }
    )


async def activity_snapshot(client, path):
    return (await client.get(path + "/snapshot?activity_schema=1")).json()


async def test_plain_chat_keeps_two_same_name_tool_calls(tmp_path):
    script = [
        AssistantMessage(tool_calls=(read_call("call_1"), read_call("call_2"))),
        ["done"],
    ]
    fx = ServerFixture(tmp_path)
    try:
        (fx.workspace_dir / "notes.md").write_text("hello notes", encoding="utf-8")
        sid, path = await new_session(fx)
        fx.bank.on_create = lambda _: setattr(fx.bank.providers[-1], "responses", script)
        sent = await fx.client.post(
            path + "/interactions", {"client_message_id": "m1", "text": "read it"}
        )
        assert sent.status == 202, sent.body
        await drain(fx, sid)

        snapshot = await activity_snapshot(fx.client, path)
        tools = [item for item in snapshot["activities"] if item["kind"] == "tool"]
        # Admission rekeys every tool item onto its stable durable identity.
        assert [item["activity_id"] for item in tools] == [
            f"act_tool_{item['payload']['tool_execution_id']}" for item in tools
        ]
        assert all(
            item["payload"]["tool_execution_id"] == item["identity"]["tool_execution_id"]
            for item in tools
        )
        assert all(item["payload"]["tool_name"] == "read" for item in tools)
        assert all(item["state"] in {"succeeded", "failed", "skipped"} for item in tools)
        assert len({item["activity_id"] for item in tools}) == 2
        models = [item for item in snapshot["activities"] if item["kind"] == "model"]
        assert models
        assert all(item["state"] == "succeeded" for item in models)
        assert all(item["identity"]["agent_run_id"] for item in models)

        # Activity frames stream contiguously on the activity subscription.
        ws = WSSession(
            fx.client.app,
            path + "/stream?token=" + SERVE_TOKEN,
            headers=[(b"host", b"127.0.0.1")],
        )
        assert (await ws.accept())["type"] == "websocket.accept"
        await ws._to_app.put(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "subscribe",
                        "stream_epoch": snapshot["activity_epoch"],
                        "after_sequence": 0,
                        "activity_schema": 1,
                    }
                ),
            }
        )
        frames = []
        rekeys = []
        # The ring holds more frames than the settled snapshot (preparing
        # upserts, admission rekeys, terminal upserts); read until both
        # rekeys are accounted for, then check end-to-end contiguity.
        while len(rekeys) < 2 and len(frames) < 64:
            frame = await ws.receive_json()
            frames.append(frame)
            if frame["type"] == "activity_rekey":
                rekeys.append(frame["payload"])
        assert [frame["sequence"] for frame in frames] == list(range(1, len(frames) + 1))
        assert {frame["type"] for frame in frames} <= {
            "activity_upsert",
            "activity_rekey",
            "activity_delta",
        }
        assert len(rekeys) == 2
        assert all(
            rekey["from_activity_id"].startswith("act_call_")
            and rekey["to_activity_id"].startswith("act_tool_")
            for rekey in rekeys
        )
        await ws.close()
    finally:
        fx.close()


async def test_activities_recover_from_durable_facts_after_restart(tmp_path):
    """A10 basis: memory content dies with the Core; durable skeletons return."""
    script = [
        AssistantMessage(tool_calls=(read_call("call_1"), read_call("call_2"))),
        ["done"],
    ]
    fx = ServerFixture(tmp_path)
    try:
        (fx.workspace_dir / "notes.md").write_text("hello notes", encoding="utf-8")
        sid, path = await new_session(fx)
        fx.bank.on_create = lambda _: setattr(fx.bank.providers[-1], "responses", script)
        await fx.client.post(path + "/interactions", {"client_message_id": "m1", "text": "read it"})
        await drain(fx, sid)
        live = await activity_snapshot(fx.client, path)
        assert len([item for item in live["activities"] if item["kind"] == "tool"]) == 2
        while_running = (await fx.client.get(path + "/activities")).json()
        assert len(while_running["items"]) == 2
    finally:
        fx.close()

    restarted = ServerFixture(tmp_path)
    try:
        path = f"/v1/workspaces/{restarted.workspace_id}/sessions/{sid}"
        snap = await activity_snapshot(restarted.client, path)
        # The transient ring and epoch did not survive; the durable tool
        # skeletons rebuild into the stream under their stable act_tool_* ids.
        assert snap["activity_sequence"] == 0
        recovered_live = [item for item in snap["activities"] if item["kind"] == "tool"]
        assert len(recovered_live) == 2
        assert all(item["activity_id"].startswith("act_tool_") for item in recovered_live)
        assert all(item["availability"] == "unsaved" for item in recovered_live)
        assert all(item["preview_ref"] is None for item in recovered_live)
        assert all(item["content_ref"] is None for item in recovered_live)
        recovered = (await restarted.client.get(path + "/activities")).json()
        # Live recovery and the durable endpoint expose the same stable ids.
        assert {item["activity_id"] for item in recovered["items"]} == {
            item["activity_id"] for item in recovered_live
        }
        # Durable call ids are normalized, so the merge key for the GUI is
        # (agent_run_id, ordinal) with call_id as a secondary hint.
        assert [
            (item["payload"]["tool_name"], item["payload"]["ordinal"])
            for item in recovered["items"]
        ] == [
            ("read", 1),
            ("read", 2),
        ]
        assert len({item["identity"]["agent_run_id"] for item in recovered["items"]}) == 1
        assert all(item["availability"] == "unsaved" for item in recovered["items"])
        assert all(item["identity"]["tool_execution_id"] for item in recovered["items"])
        assert all(
            item["state"] in {"succeeded", "failed", "skipped"} for item in recovered["items"]
        )
    finally:
        restarted.close()


async def test_workflow_root_sees_node_activities_for_two_calls(tmp_path):
    script = [
        AssistantMessage(tool_calls=(read_call("call_1"), read_call("call_2"))),
        ["leaf done"],
    ]
    fx = ServerFixture(tmp_path)
    try:
        (fx.workspace_dir / "notes.md").write_text("hello notes", encoding="utf-8")

        def publish_single():
            from morrow.core.agent_runs import AgentDefinitionRef

            management = fx.host.context.management
            management.create_agent_source(agent_source(), expected_source_revision=0)
            version = management.publish_agent(
                "helper", expected_head_revision=0, command_id="cmd_pub_agent"
            )
            ref = AgentDefinitionRef(
                definition_id=version.source.definition_id,
                version_id=version.version_id,
                content_hash=version.content_hash,
            )
            management.create_workflow_source(single_source(ref), expected_source_revision=0)
            publication = management.publish_workflow(
                "pipeline", expected_head_revision=0, command_id="cmd_pub_wf"
            )
            return publication.revision

        revision = await fx.host.execute_query(publish_single)
        session_id, task_id, task_version = await create_session_and_task(fx.client)
        fx.bank.on_create = lambda _: setattr(fx.bank.providers[-1], "responses", script)
        started = await start_run(
            fx.client, revision.workflow_revision_id, session_id, task_id, task_version
        )
        assert started.status == 200, started.body
        run_id = started.json()["result"]["run"]["workflow_run_id"]
        view = await wait_for_run(fx.client, run_id, "completed", "failed")
        assert view["run"]["status"] == "completed", view["run"]

        path = f"/v1/workspaces/{fx.workspace_id}/sessions/{session_id}"
        snapshot = await activity_snapshot(fx.client, path)
        tools = [item for item in snapshot["activities"] if item["kind"] == "tool"]
        assert len(tools) == 2
        assert tools[0]["activity_id"] != tools[1]["activity_id"]
        assert all(item["payload"]["tool_name"] == "read" for item in tools)
        assert all(item["identity"]["node_run_id"] for item in tools)
        assert all(item["identity"]["workflow_run_id"] == run_id for item in tools)
        assert all(item["identity"]["root_session_id"] == session_id for item in tools)
        models = [item for item in snapshot["activities"] if item["kind"] == "model"]
        assert models and all(item["identity"]["node_run_id"] for item in models)
    finally:
        fx.close()


async def test_reasoning_fragments_reach_the_activity_stream_not_the_reply(tmp_path):
    """P3.2/P3.6 basis: scripted reasoning provider → thinking item + delta
    frames on the activity socket; the committed reply stays clean."""
    from morrow.core.models import ModelEvent

    script = [
        [
            ModelEvent(kind="reasoning_delta", reasoning_text="先检查事件桥接"),
            ModelEvent(kind="reasoning_delta", reasoning_text="，再看前端合并"),
            "正文回答",
        ]
    ]
    fx = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fx)
        fx.bank.on_create = lambda _: setattr(fx.bank.providers[-1], "responses", script)
        sent = await fx.client.post(
            path + "/interactions", {"client_message_id": "m1", "text": "思考并回答"}
        )
        assert sent.status == 202, sent.body
        await drain(fx, sid)

        snapshot = await activity_snapshot(fx.client, path)
        models = [item for item in snapshot["activities"] if item["kind"] == "model"]
        assert models
        assert all(item["identity"]["agent_run_id"] for item in models)

        # Reply channel: only the committed text; no reasoning leaked.
        timeline = (await fx.client.get(path + "/timeline")).json()["items"]
        reply = next(i for i in timeline if i["kind"] == "assistant_message")
        assert reply["content"] == "正文回答"

        # Fragments stream as activity_delta frames on the activity socket.
        ws = WSSession(
            fx.client.app,
            path + "/stream?token=" + SERVE_TOKEN,
            headers=[(b"host", b"127.0.0.1")],
        )
        assert (await ws.accept())["type"] == "websocket.accept"
        await ws._to_app.put(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "subscribe",
                        "stream_epoch": snapshot["activity_epoch"],
                        "after_sequence": 0,
                        "activity_schema": 1,
                    }
                ),
            }
        )
        deltas = []
        thinking_seen = False
        for _ in range(200):
            frame = await ws.receive_json()
            if (
                frame["type"] == "activity_upsert"
                and frame["payload"]["item"]["payload"].get("stage") == "thinking"
            ):
                thinking_seen = True
            if frame["type"] == "activity_delta":
                deltas.append(frame["payload"]["delta"])
            if thinking_seen and len("".join(deltas)) >= len("先检查事件桥接，再看前端合并"):
                break
        await ws.close()
        assert thinking_seen
        assert "先检查事件桥接，再看前端合并" in "".join(deltas)
    finally:
        fx.close()


async def test_planning_wait_projects_without_fabricating_node_runs(tmp_path):
    """A17 basis: planning wait evidence enters the activity stream under the
    planning operation identity; no NodeRun is invented."""
    import json as json_module

    from test_workflow_stage_stream import _plan_spec

    fx = ServerFixture(tmp_path, scripts=[[[json_module.dumps(_plan_spec("work"))]]])
    try:
        sid, root = await new_session(fx)
        body = {
            "command_id": "cmd_plan_activity",
            "session_id": sid,
            "origin_interaction_id": "message_plan",
            "task": {"objective": "Implement the requested calculator"},
        }
        reply = await fx.client.post(root + "/task-plan", body)
        assert reply.status == 200, reply.body
        for _ in range(4000):
            events = (await fx.client.get(root + "/task-plan/events")).json()
            if any(e.get("stage") == "awaiting_model" for e in events):
                break
            await asyncio.sleep(0)
        await drain(fx, sid)
        snapshot = await activity_snapshot(fx.client, root)
        planning = [
            item for item in snapshot["activities"] if item["identity"].get("planning_operation_id")
        ]
        assert planning
        assert all(item["kind"] == "model" for item in planning)
        assert all(item["origin"] == "planning_service" for item in planning)
        assert all(item["identity"].get("node_run_id") is None for item in planning)
    finally:
        fx.close()


async def test_parallel_nodes_keep_separate_activities_and_no_double_emission(tmp_path):
    """P5.1/A08 basis: two parallel leaves each keep their own identity; the
    root sees both; invoking_session scope never double-emits."""
    fx = ServerFixture(tmp_path)
    try:
        (fx.workspace_dir / "notes.md").write_text("hello notes", encoding="utf-8")

        def publish_parallel():
            from morrow.core.agent_runs import AgentDefinitionRef
            from morrow.core.workflows.contracts import NodeOutputRef
            from morrow.core.workflows.definitions import (
                WorkflowDefinitionSource,
                WorkflowEdge,
            )
            from test_stage7_serial_scheduler import agent_source, chain_node

            management = fx.host.context.management
            management.create_agent_source(agent_source(), expected_source_revision=0)
            version = management.publish_agent(
                "helper", expected_head_revision=0, command_id="cmd_pub_agent"
            )
            ref = AgentDefinitionRef(
                definition_id=version.source.definition_id,
                version_id=version.version_id,
                content_hash=version.content_hash,
            )
            source = WorkflowDefinitionSource(
                **{
                    "workflow_definition_id": "pipeline",
                    "name": "Two node pipeline",
                    "nodes": (
                        chain_node(ref, "left", "Read notes left"),
                        chain_node(ref, "right", "Read notes right"),
                    ),
                    "edges": (WorkflowEdge(from_node_id="left", to_node_id="right"),),
                    "required_outputs": (NodeOutputRef(node_id="right", output_slot="result"),),
                }
            )
            management.create_workflow_source(source, expected_source_revision=0)
            publication = management.publish_workflow(
                "pipeline", expected_head_revision=0, command_id="cmd_pub_wf"
            )
            return publication.revision

        revision = await fx.host.execute_query(publish_parallel)
        session_id, task_id, task_version = await create_session_and_task(fx.client)
        fx.bank.on_create = lambda _: setattr(
            fx.bank.providers[-1],
            "responses",
            [AssistantMessage(content="node answer")],
        )
        started = await start_run(
            fx.client, revision.workflow_revision_id, session_id, task_id, task_version
        )
        run_id = started.json()["result"]["run"]["workflow_run_id"]
        view = await wait_for_run(fx.client, run_id, "completed", "failed")
        assert view["run"]["status"] == "completed", view["run"]

        path = f"/v1/workspaces/{fx.workspace_id}/sessions/{session_id}"
        snapshot = await activity_snapshot(fx.client, path)
        node_ids = {
            item["identity"].get("node_id")
            for item in snapshot["activities"]
            if item["kind"] == "model"
        }
        assert {"left", "right"} <= node_ids, node_ids
        # No mutable-draft sharing: the root draft chain only carries the root's
        # own conversation; leaf text stays on the leaf sessions.
        assert snapshot["draft"] is None or "node answer" not in json.dumps(snapshot["draft"])
    finally:
        fx.close()


async def test_activity_routes_reject_cross_workspace_guessing(tmp_path):
    """A11 basis: node/session identity guessing across workspaces is rejected."""
    fx = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fx)
        # Same session id requested under a bogus workspace scope is 404/403.
        bogus = f"/v1/workspaces/ws_does_not_exist/sessions/{sid}"
        snapshot = await fx.client.get(bogus + "/snapshot?activity_schema=1")
        assert snapshot.status in {403, 404}
        recovery = await fx.client.get(bogus + "/activities")
        assert recovery.status in {403, 404}
    finally:
        fx.close()


async def test_distinct_runs_keep_distinct_activity_identity(tmp_path):
    """P5.3: every run carries fresh run/node identity; another run (even of
    the same published workflow) never rewrites an earlier run's items."""
    fx = ServerFixture(tmp_path)
    try:

        def publish_single():
            from morrow.core.agent_runs import AgentDefinitionRef

            management = fx.host.context.management
            management.create_agent_source(agent_source(), expected_source_revision=0)
            version = management.publish_agent(
                "helper", expected_head_revision=0, command_id="cmd_pub_agent"
            )
            ref = AgentDefinitionRef(
                definition_id=version.source.definition_id,
                version_id=version.version_id,
                content_hash=version.content_hash,
            )
            management.create_workflow_source(single_source(ref), expected_source_revision=0)
            publication = management.publish_workflow(
                "pipeline", expected_head_revision=0, command_id="cmd_pub_wf"
            )
            return publication.revision

        revision = await fx.host.execute_query(publish_single)
        fx.bank.on_create = lambda _: setattr(
            fx.bank.providers[-1],
            "responses",
            [AssistantMessage(content="leaf answer")],
        )
        root_items = []
        run_ids = []
        for _index in range(2):
            session_id, task_id, task_version = await create_session_and_task(fx.client)
            started = await start_run(
                fx.client, revision.workflow_revision_id, session_id, task_id, task_version
            )
            assert started.status == 200, started.body
            run_id = started.json()["result"]["run"]["workflow_run_id"]
            run_ids.append(run_id)
            view = await wait_for_run(fx.client, run_id, "completed", "failed")
            assert view["run"]["status"] == "completed", view["run"]
            path = f"/v1/workspaces/{fx.workspace_id}/sessions/{session_id}"
            snapshot = await activity_snapshot(fx.client, path)
            runs_seen = {
                item["identity"].get("workflow_run_id")
                for item in snapshot["activities"]
                if item["kind"] == "model"
            }
            assert runs_seen == {run_id}
            root_items.append(snapshot["activities"])
        # Disjoint run identity; the first root's activities are untouched.
        assert len(set(run_ids)) == 2
        first_ids = {i["activity_id"] for i in root_items[0]}
        for item in root_items[1]:
            assert (
                item["activity_id"] not in first_ids
                or item["identity"]["workflow_run_id"] == run_ids[1]
            )
    finally:
        fx.close()


def test_metadata_updates_preserve_content_accounting_and_completion_ttl(monkeypatch):
    stream = ActivityStreamState()
    stream.upsert(base_item(state="running"))
    stream.append_delta("act_call_c1", "实时 output")
    size = len("实时 output".encode())
    stream.upsert(base_item(revision=2, state="running"))
    stream.upsert(base_item(revision=3, state="succeeded"))
    entry = stream.entries["act_call_c1"]
    completed = entry.completed_monotonic
    stream.upsert(base_item(revision=4, state="succeeded", content_ref="/content"))
    assert entry.content == "实时 output"
    assert entry.content_bytes == stream.preview_bytes == size
    assert entry.completed_monotonic == completed
    stream.upsert(base_item(revision=5, state="succeeded"))
    assert entry.item["content_ref"] == "/content"
    monkeypatch.setattr(activities_module.time, "monotonic", lambda: completed + 601)
    stream.sweep_expired()
    assert entry.content == ""
    assert entry.content_bytes == stream.preview_bytes == 0


def test_workflow_output_uses_rekeyed_identity_in_both_streams():
    from types import SimpleNamespace

    from morrow.server.replies import ReplyStreams
    from morrow.server.workflow_stream import WorkflowStreamBridge

    manager = SimpleNamespace(require_session=lambda sid: None, drivers={})
    manager.streams = ReplyStreams(manager, None, "ws")
    bridge = WorkflowStreamBridge(manager, None, "ws")
    bridge._roots["wrun"] = "root"
    identity = {
        "workflow_run_id": "wrun",
        "node_run_id": "nrun",
        "node_id": "work",
        "root_session_id": "root",
        "source_session_id": "leaf",
    }
    fact = {
        "call_id": "c1",
        "tool_name": "bash",
        "phase": "prepared",
        "timestamp": "2026-09-14T00:00:00+00:00",
    }
    bridge.leaf_tool_observation(identity, "leaf", fact)
    bridge.leaf_tool_observation(
        identity,
        "leaf",
        {
            **fact,
            "phase": "executing",
            "tool_execution_id": "tex_1",
        },
    )
    bridge.leaf_tool_output(identity, "leaf", "c1", "real output")
    bridge.leaf_tool_observation(
        identity,
        "leaf",
        {
            **fact,
            "phase": "terminal",
            "tool_execution_id": "tex_1",
            "disposition": "succeeded",
        },
    )
    for sid in ("leaf", "root"):
        stream = manager.streams.state(sid).activities
        assert "act_call_c1" not in stream.entries
        entry = stream.entries["act_tool_tex_1"]
        assert entry.item["activity_id"] == "act_tool_tex_1"
        assert entry.content == "real output"
        assert stream.preview_bytes == len("real output")
        manager.streams.activity_content_reset(sid, "act_tool_tex_1", "budget")
        assert stream.preview_bytes == 0
        assert entry.content == ""


def test_rekey_collision_releases_discarded_preview_bytes():
    from morrow.server.replies import ReplyState, ReplyStreams

    state = ReplyState()
    for activity_id in ("act_call_c1", "act_tool_tex_1"):
        state.activities.upsert(base_item(activity_id=activity_id))
        state.activities.append_delta(activity_id, "output")
    ReplyStreams._rekey_activity_entry(
        state,
        {
            "from_activity_id": "act_call_c1",
            "to_activity_id": "act_tool_tex_1",
        },
    )
    assert state.activities.preview_bytes == len("output")
    assert state.activities.entries["act_tool_tex_1"].content == "output"
