"""Targeted node steering integration tests (master plan P6, proposal 5.2).

Covers: root-session authorization, exact node binding, idempotent command
receipts, injection at the next model request boundary, applied/expired
receipts and stale-target rejection. All providers stay scripted.
"""

from __future__ import annotations

import asyncio
import json

from morrow.core.models import (
    AssistantMessage,
    FunctionToolCall,
    ModelEvent,
    ModelFinishReason,
)
from test_stage8_chat_submission import new_session
from test_stage8_core_api import (
    ServerFixture,
    create_session_and_task,
    start_run,
    wait_for_run,
)


def read_call(call_id: str) -> FunctionToolCall:
    return FunctionToolCall(id=call_id, name="read", arguments=json.dumps({"path": "notes.md"}))


def single_source(ref):
    from morrow.core.workflows.contracts import NodeOutputRef
    from morrow.core.workflows.definitions import WorkflowDefinitionSource
    from test_stage7_serial_scheduler import chain_node

    return WorkflowDefinitionSource(
        **{
            "workflow_definition_id": "pipeline",
            "name": "Single node pipeline",
            "nodes": (chain_node(ref, "work", "Read the notes"),),
            "edges": (),
            "required_outputs": (NodeOutputRef(node_id="work", output_slot="result"),),
        }
    )


async def publish_single(fx):
    from morrow.core.agent_runs import AgentDefinitionRef

    def work():
        from test_stage7_serial_scheduler import agent_source

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

    return await fx.host.execute_query(work)


class _GatedLeafProvider:
    """Request 1 = one read call; request 2 waits on the gate, then answers."""

    def __init__(self, gate: asyncio.Event) -> None:
        self.gate = gate
        self.calls = 0
        self.stream_calls: list[list[str]] = []

    async def stream(self, model, messages, tools=(), generation=None):
        del model, tools, generation
        self.calls += 1
        self.stream_calls.append([m.content for m in messages if getattr(m, "role", "") == "user"])
        if self.calls == 1:
            yield ModelEvent(
                kind="completed",
                finish_reason=ModelFinishReason.TOOL_CALLS,
                message=AssistantMessage(tool_calls=(read_call("call_1"),)),
            )
            # Hold the stream open so the node stays mid-run while the test
            # enqueues the steer; releasing lets the tool round proceed and
            # the next request boundary inject it.
            await self.gate.wait()
            return
        yield ModelEvent(kind="reasoning_delta", reasoning_text="收到纠偏")
        yield ModelEvent(
            kind="completed",
            finish_reason=ModelFinishReason.STOP,
            message=AssistantMessage(content="已按纠偏完成"),
        )


def arm_provider(fx, gate: asyncio.Event) -> _GatedLeafProvider:
    provider = _GatedLeafProvider(gate)
    fx.bank.on_create = lambda _: setattr(fx.bank.providers[-1], "stream", provider.stream)
    return provider


async def _running_node_run_id(fx, run_id):
    for _ in range(2000):
        view = (await fx.client.get(f"/v1/workflow-runs/{run_id}")).json()["view"]
        for node in view["nodes"]:
            if node["node"]["status"] == "running":
                return node["node"]["node_run_id"]
        await asyncio.sleep(0.01)
    raise AssertionError("no running node observed")


async def activity_snapshot(fx, session_id):
    path = f"/v1/workspaces/{fx.workspace_id}/sessions/{session_id}"
    return (await fx.client.get(path + "/snapshot?activity_schema=1")).json()


async def test_node_steer_is_injected_at_next_request_with_applied_receipt(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        (fx.workspace_dir / "notes.md").write_text("hello notes", encoding="utf-8")
        revision = await publish_single(fx)
        session_id, task_id, task_version = await create_session_and_task(fx.client)
        gate = asyncio.Event()
        provider = arm_provider(fx, gate)
        started = await start_run(
            fx.client, revision.workflow_revision_id, session_id, task_id, task_version
        )
        assert started.status == 200, started.body
        run_id = started.json()["result"]["run"]["workflow_run_id"]
        node_run_id = await _running_node_run_id(fx, run_id)

        path = f"/v1/workspaces/{fx.workspace_id}/sessions/{session_id}"
        sent = await fx.client.post(
            path + "/task-plan/node-steer",
            {
                "command_id": "cmd_steer_1",
                "session_id": session_id,
                "workflow_run_id": run_id,
                "node_run_id": node_run_id,
                "text": "重点核对读取结果里的第一行",
            },
        )
        assert sent.status == 200, sent.body
        receipt = sent.json()
        assert receipt["disposition"] == "accepted"
        assert receipt["node_run_id"] == node_run_id

        gate.set()
        view = await wait_for_run(fx.client, run_id, "completed", "failed")
        assert view["run"]["status"] == "completed", view["run"]

        # The injected text reached the leaf's next model request.
        assert len(provider.stream_calls) == 2
        assert provider.stream_calls[1][-1] == "重点核对读取结果里的第一行"

        # The root timeline shows the applied receipt bound to this node.
        snapshot = await activity_snapshot(fx, session_id)
        applied = [
            item
            for item in snapshot["activities"]
            if item["kind"] == "control" and item["payload"]["receipt"] == "applied"
        ]
        assert applied
        assert applied[0]["identity"]["node_run_id"] == node_run_id
    finally:
        fx.close()


async def test_node_steer_is_idempotent_and_rejects_conflicts(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        revision = await publish_single(fx)
        session_id, task_id, task_version = await create_session_and_task(fx.client)
        gate = asyncio.Event()
        arm_provider(fx, gate)
        started = await start_run(
            fx.client, revision.workflow_revision_id, session_id, task_id, task_version
        )
        run_id = started.json()["result"]["run"]["workflow_run_id"]
        node_run_id = await _running_node_run_id(fx, run_id)
        path = f"/v1/workspaces/{fx.workspace_id}/sessions/{session_id}"

        first = await fx.client.post(
            path + "/task-plan/node-steer",
            {
                "command_id": "cmd_steer_dup",
                "session_id": session_id,
                "workflow_run_id": run_id,
                "node_run_id": node_run_id,
                "text": "第一次纠偏",
            },
        )
        assert first.status == 200, first.body

        replay = await fx.client.post(
            path + "/task-plan/node-steer",
            {
                "command_id": "cmd_steer_dup",
                "session_id": session_id,
                "workflow_run_id": run_id,
                "node_run_id": node_run_id,
                "text": "第一次纠偏",
            },
        )
        assert replay.status == 200
        assert replay.json()["disposition"] == "replay"

        conflict = await fx.client.post(
            path + "/task-plan/node-steer",
            {
                "command_id": "cmd_steer_dup",
                "session_id": session_id,
                "workflow_run_id": run_id,
                "node_run_id": node_run_id,
                "text": "同键不同内容",
            },
        )
        assert conflict.status == 409
        gate.set()
        await wait_for_run(fx.client, run_id, "completed", "failed")
    finally:
        fx.close()


async def test_node_steer_requires_the_root_session(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        revision = await publish_single(fx)
        root_session, task_id, task_version = await create_session_and_task(fx.client)
        gate = asyncio.Event()
        arm_provider(fx, gate)
        started = await start_run(
            fx.client, revision.workflow_revision_id, root_session, task_id, task_version
        )
        run_id = started.json()["result"]["run"]["workflow_run_id"]
        node_run_id = await _running_node_run_id(fx, run_id)

        other_sid, _other_path = await new_session(fx, "cmd_other_session")
        path = f"/v1/workspaces/{fx.workspace_id}/sessions/{other_sid}"
        rejected = await fx.client.post(
            path + "/task-plan/node-steer",
            {
                "command_id": "cmd_steer_outsider",
                "session_id": other_sid,
                "workflow_run_id": run_id,
                "node_run_id": node_run_id,
                "text": "越权纠偏",
            },
        )
        assert rejected.status == 403
        assert "root session" in rejected.json()["error"]["message"]
        gate.set()
        await wait_for_run(fx.client, run_id, "completed", "failed")
    finally:
        fx.close()


async def test_node_steer_rejects_completed_targets(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        revision = await publish_single(fx)
        session_id, task_id, task_version = await create_session_and_task(fx.client)
        gate = asyncio.Event()
        arm_provider(fx, gate)
        started = await start_run(
            fx.client, revision.workflow_revision_id, session_id, task_id, task_version
        )
        run_id = started.json()["result"]["run"]["workflow_run_id"]
        node_run_id = await _running_node_run_id(fx, run_id)
        gate.set()
        view = await wait_for_run(fx.client, run_id, "completed")
        completed_node_run_id = view["nodes"][0]["node"]["node_run_id"]
        assert completed_node_run_id == node_run_id

        path = f"/v1/workspaces/{fx.workspace_id}/sessions/{session_id}"
        late = await fx.client.post(
            path + "/task-plan/node-steer",
            {
                "command_id": "cmd_steer_late",
                "session_id": session_id,
                "workflow_run_id": run_id,
                "node_run_id": node_run_id,
                "text": "迟到纠偏",
            },
        )
        assert late.status == 400
        assert "running" in late.json()["error"]["message"]
    finally:
        fx.close()
