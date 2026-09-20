"""Workflow stage streaming: scheduler identity forwarding, bounded recovery,
slow-consumer tolerance and planning wait evidence."""

import asyncio
import json

import pytest

from morrow.application.workflows.observation import stage_payload
from morrow.core.events import lifecycle_is_valid
from morrow.core.models import (
    AgentEvent,
    AssistantMessage,
    FunctionToolCall,
    ModelEvent,
    ModelFinishReason,
    ModelRef,
)
from morrow.runtime.agent import AgentLoop
from morrow.runtime.session import Session
from morrow.testing import make_context_builder
from test_stage8_chat_submission import drain, new_session
from test_stage8_core_api import ServerFixture, publish_pipeline

# AgentLoop factual stages -----------------------------------------------------


async def _collect(iterator):
    return [event async for event in iterator]


def _tool_provider():
    class Provider:
        async def stream(self, model, messages, tools=()):
            del model, messages, tools
            yield ModelEvent(kind="activity", activity="tool_call")
            yield ModelEvent(
                kind="completed",
                finish_reason=ModelFinishReason.TOOL_CALLS,
                message=AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(id="call_1", name="read", arguments='{"path":"a"}'),
                    ),
                ),
            )

    return Provider()


@pytest.mark.asyncio
async def test_agent_loop_reports_request_wait_and_tool_preparation_stages():
    """A tool-only response with no streamed text still shows factual stages."""

    events = await _collect(
        AgentLoop(
            _tool_provider(), ModelRef(provider_id="p", model_id="m"), make_context_builder()
        ).run_task(Session(session_id="s"), "go")
    )
    statuses = [
        (event.type, event.payload.get("status"))
        for event in events
        if event.type == "status.changed"
    ]
    # The tool-fragment marker is real output: it reports the responding stage,
    # then the tool-preparation stage, without ever leaking the arguments.
    assert statuses == [
        ("status.changed", "awaiting_model"),
        ("status.changed", "model_responding"),
        ("status.changed", "tool_preparing"),
    ]
    assert lifecycle_is_valid(events)
    assert not any(
        "call_1" in str(event.payload) or "path" in str(event.payload)
        for event in events
        if event.type == "status.changed"
    )


def test_stage_payload_maps_only_bounded_content_free_facts():
    waiting = stage_payload(
        AgentEvent(
            type="status.changed",
            event_id="evt_1",
            session_id="s",
            turn_id="t",
            sequence=1,
            payload={"status": "awaiting_model", "attempt_ordinal": 2},
        )
    )
    assert waiting == {"stage": "awaiting_model", "attempt_ordinal": 2}
    tool = stage_payload(
        AgentEvent(
            type="tool.status",
            event_id="evt_2",
            session_id="s",
            turn_id="t",
            sequence=2,
            payload={"call_id": "c", "name": "read", "status": "running", "ordinal": 1, "total": 2},
        )
    )
    assert tool == {
        "stage": "tool_running",
        "tool": "read",
        "ordinal": 1,
        "total": 2,
        "ok": False,
    }
    assert (
        stage_payload(
            AgentEvent(
                type="text.delta",
                event_id="evt_3",
                session_id="s",
                turn_id="t",
                sequence=3,
                payload={"text": "never projected"},
            )
        )
        is None
    )


# Scheduler -> root session stream ----------------------------------------------


async def _drive_pipeline(fx, sid, path, key="workflow.stage"):
    revision = await publish_pipeline(fx)
    request = {
        "client_message_id": key,
        "intent": "explicit_workflow",
        "text": "Inspect project structure",
        "workflow": {
            "workflow_definition_id": "pipeline",
            "workflow_revision_id": revision.workflow_revision_id,
        },
    }
    reply = await fx.client.post(path + "/interactions", request)
    assert reply.status == 202, reply.body
    await drain(fx, sid)
    receipt = (await fx.client.get(path + f"/interactions/{key}")).json()["receipt"]
    assert receipt["status"] == "settled", receipt
    return receipt["workflow_run_id"]


async def test_scheduler_streams_stage_frames_with_identity_to_root_session(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fx)
        run_id = await _drive_pipeline(fx, sid, path)
        assert run_id

        def inspect():
            chat = fx.host.context.chat
            state = chat.streams.states[sid]
            stage_frames = [frame for frame, _ in state.frames if frame["type"] == "stage"]
            assert stage_frames, "root session received no stage frames"
            by_node = {}
            for frame in stage_frames:
                payload = frame["payload"]
                assert payload["workflow_run_id"] == run_id
                assert payload["node_run_id"].startswith("nrun_")
                assert payload["agent_run_id"] is None or payload["agent_run_id"].startswith(
                    "arun_"
                )
                by_node.setdefault(payload["node_id"], set()).add(payload["stage"])
            assert {"gamma", "alpha"} <= set(by_node)
            assert "awaiting_model" in by_node["gamma"]
            assert "model_responding" in by_node["gamma"]
            # Snapshots replay the bounded stage state for reconnect recovery.
            snapshot = chat.streams.snapshot(sid)
            assert {item["node_run_id"] for item in snapshot["stages"]} == {
                frame["payload"]["node_run_id"] for frame in stage_frames
            }
            # Leaf sessions keep their own reply stream with full events.
            leaf_states = [key for key in chat.streams.states if key != sid]
            assert leaf_states, "isolated leaf sessions received no reply stream"
            return stage_frames

        await fx.on_core(inspect)
    finally:
        fx.close()


async def test_slow_or_disconnected_subscriber_never_blocks_or_breaks_the_run(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fx)
        loop = asyncio.get_running_loop()

        def subscribe_and_abandon():
            streams = fx.host.context.chat.streams
            return streams.subscribe(sid, loop)

        token, queue = await fx.on_core(subscribe_and_abandon)
        run_id = await _drive_pipeline(fx, sid, path)
        run = await fx.on_core(
            lambda: fx.host.context.journal.workflows.get_run(fx.workspace_id, run_id)
        )
        assert run.status.value == "completed"

        # Coalesced hints: the unread subscriber stays bounded, never unbounded.
        def metrics():
            streams = fx.host.context.chat.streams
            streams.unsubscribe(sid, token)
            return queue.qsize(), streams.subscribers

        unread, subscribers = await fx.on_core(metrics)
        assert unread <= 1
        assert subscribers == 0
    finally:
        fx.close()


async def test_raising_observer_and_full_stream_cache_cannot_abort_execution(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fx)

        def sabotage():
            scheduler = fx.host.context.products.workflow_runtime.scheduler

            class Exploding:
                def node_event(self, *args, **kwargs):
                    raise RuntimeError("projection is down")

            scheduler.event_observer = Exploding()

        await fx.on_core(sabotage)
        run_id = await _drive_pipeline(fx, sid, path)
        run = await fx.on_core(
            lambda: fx.host.context.journal.workflows.get_run(fx.workspace_id, run_id)
        )
        assert run.status.value == "completed"
    finally:
        fx.close()


async def test_stage_state_is_bounded_and_replays_after_reconnect(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fx)

        def flood():
            streams = fx.host.context.chat.streams
            from morrow.server.replies import STAGES_LIMIT

            for index in range(STAGES_LIMIT + 8):
                streams.stage(
                    sid,
                    {
                        "workflow_run_id": "wrun_flood",
                        "node_run_id": f"nrun_{index}",
                        "node_id": f"node_{index}",
                        "stage": "awaiting_model",
                        "ts": None,
                    },
                )
            state = streams.states[sid]
            return len(state.stages), state.sequence, state.epoch

        count, sequence, epoch = await fx.on_core(flood)
        assert count == 64
        frames = await fx.on_core(
            lambda: fx.host.context.chat.streams.pull(sid, epoch, sequence - 3)
        )
        assert [frame["type"] for frame in frames].count("stage") == 3
        expired = await fx.on_core(lambda: fx.host.context.chat.streams.pull(sid, "stale", 0))
        assert expired[0]["type"] == "resync_required"
        snapshot = await fx.on_core(lambda: fx.host.context.chat.streams.snapshot(sid))
        assert len(snapshot["stages"]) == 64
        assert snapshot["stream_epoch"] == epoch
    finally:
        fx.close()


# Planning wait evidence ---------------------------------------------------------


def _plan_spec(*nodes):
    return {
        "nodes": [
            {
                "node_id": n,
                "title": n,
                "task": f"Perform {n}",
                "agent": "preset:general",
                "responsibility": "implementation",
                "depends_on": [nodes[i - 1]] if i else [],
                "completion": [f"{n} is verified"],
            }
            for i, n in enumerate(nodes or ("work",))
        ],
        "deliverables": [nodes[-1] if nodes else "work"],
    }


async def test_planning_request_started_event_carries_wait_evidence(tmp_path):
    fx = ServerFixture(tmp_path, scripts=[[[json.dumps(_plan_spec("work"))]]])
    try:
        sid, root = await new_session(fx)
        body = {
            "command_id": "cmd_plan_stage",
            "session_id": sid,
            "origin_interaction_id": "message1",
            "task": {"objective": "Implement the requested calculator"},
        }
        reply = await fx.client.post(root + "/task-plan", body)
        assert reply.status == 200, reply.body
        events = (await fx.client.get(root + "/task-plan/events")).json()
        started = [event for event in events if event.get("stage") == "awaiting_model"]
        assert started, events
        assert started[0]["status"] == "running"
        assert isinstance(started[0]["attempt"], int)
        assert started[0]["started_at"]
    finally:
        fx.close()


async def test_agent_loop_reports_thinking_stage_and_routes_fragments_to_observer():
    """Reasoning fragments get a distinct stage and the observer seam; the
    reply channel never carries them (P3.2/P3.4)."""

    class ReasoningProvider:
        async def stream(self, model, messages, tools=()):
            del model, messages, tools
            yield ModelEvent(kind="reasoning_delta", reasoning_text="内部推理片段一")
            yield ModelEvent(kind="reasoning_delta", reasoning_text="片段二")
            yield ModelEvent(
                kind="completed",
                finish_reason=ModelFinishReason.STOP,
                message=AssistantMessage(content="最终答案"),
            )

    class Collector:
        def __init__(self):
            self.fragments = []

        def reasoning_delta(self, *, turn_id, attempt_ordinal, fragment):
            self.fragments.append((turn_id, attempt_ordinal, fragment))

    collector = Collector()
    loop = AgentLoop(
        ReasoningProvider(),
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
        activity_observer=collector,
    )
    events = await _collect(loop.run_task(Session(session_id="s"), "go"))

    statuses = [event.payload.get("status") for event in events if event.type == "status.changed"]
    assert statuses == ["awaiting_model", "thinking"]
    # The reply channel stays clean: no reasoning text in any text.delta.
    assert not any("推理" in str(event.payload) for event in events if event.type == "text.delta")
    completed = events[-1]
    assert completed.payload["finish_reason"] == "stop"
    assert collector.fragments == [
        (collector.fragments[0][0], 1, "内部推理片段一"),
        (collector.fragments[0][0], 1, "片段二"),
    ]


async def test_broken_observer_never_aborts_the_run():
    """Observer failures are bounded counters, not execution failures."""

    class BrokenObserver:
        def reasoning_delta(self, **_):
            raise RuntimeError("observer exploded")

    class ReasoningProvider:
        async def stream(self, model, messages, tools=()):
            del model, messages, tools
            yield ModelEvent(kind="reasoning_delta", reasoning_text="片段")
            yield ModelEvent(
                kind="completed",
                finish_reason=ModelFinishReason.STOP,
                message=AssistantMessage(content="答案"),
            )

    loop = AgentLoop(
        ReasoningProvider(),
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
        activity_observer=BrokenObserver(),
    )
    events = await _collect(loop.run_task(Session(session_id="s"), "go"))
    assert events[-1].type == "turn.completed"
    assert loop.activity_observer_drops == 1
