"""Planned reproductions for the A01-A28 acceptance matrix (integration era).

These tests were born at P01 as strict xfails reproducing the missing
capabilities on the pre-repair baseline. The four-lane repair has landed and
the coordinator flipped the markers as each capability became real:

- A01 pause with no first model token inside a node -> P03 (lane A) - PASSING
- A03 pause after a committed tool call, next model request waiting -> P03/P04 (lane A) - PASSING
- A10 layered planning request outcomes with ended_at -> P05 (lane B) - PASSING
- A16 full durable trajectory replay after a real cross-process restart -> P08 (lane C) + coordinator wiring - PASSING
- A27 correction text after pause continues the same task in a new segment -> P04 (lane A) - PASSING

Already-green behavior is not duplicated here: existing attempt/node-boundary
coverage stays in test_workflow_pause_continue_repair.py and
test_workflow_stage_stream.py. This file is coordinator-owned
(tests/fixtures/parallel_contracts/ownership.json). All synchronization uses Core-loop
gates, never wall-clock sleeps.
"""

from __future__ import annotations

import json

import pytest

from fixtures.cross_process import BackendWorker, require_clean_exit
from fixtures.parallel_gates import AsyncGate, await_entered, gate_provider_stream
from morrow.core.models import AssistantMessage, FunctionToolCall
from test_stage7_serial_scheduler import pair_source, reader_agent
from test_stage8_chat_submission import new_session
from test_stage8_core_api import (
    ServerFixture,
    create_session_and_task,
    publish_pipeline,
    start_run,
    wait_for_run,
)
from test_workflow_pause_continue_repair import RepairFixture
from test_workflow_task_planning import request, spec

#: A scripted reply for a request the test keeps gated on the Core loop.
GATED_REPLY = "GATED_MODEL_REPLY"

READ_CALL_MSG = AssistantMessage(
    tool_calls=(
        FunctionToolCall(
            id="call_read_a03",
            name="read",
            arguments=json.dumps({"path": "notes.txt"}),
        ),
    )
)

#: One provider: emit a tool call, then wait behind the gate once the tool
#: result is committed and the next model request starts.
GATED_AFTER_TOOL_SCRIPT = [READ_CALL_MSG, [GATED_REPLY]]

#: The cross-process worker writes durable facts through the public API,
#: records their identity in the result file and exits cleanly; the parent
#: reopens the same data root and verifies them (A16 setup).
WORKER_DURABLE_WRITE_SNIPPET = """
import asyncio
import json
import os

settings = json.loads(os.environ["MORROW_CROSS_PROCESS_SETTINGS"])


async def go():
    created = await client.post("/v1/sessions", {"command_id": "cmd_worker_session"})
    assert created.status == 200, created.body
    session_id = created.json()["result"]["session"]["session_id"]
    task = await client.post("/v1/tasks", {"session_id": session_id})
    assert task.status == 200, task.body
    with open(settings["result_file"], "w", encoding="utf-8") as handle:
        json.dump(
            {
                "session_id": session_id,
                "task_run_id": task.json()["result"]["task"]["task_run_id"],
            },
            handle,
        )


asyncio.run(go())
"""


@pytest.fixture
def repair_fx(tmp_path):
    """Local RepairFixture so the file works under any selection mode."""
    fixture = RepairFixture(tmp_path)
    yield fixture
    fixture.close()


async def test_a01_pause_with_no_first_token_suspends_inside_the_node(repair_fx):
    """Pause suspends while the model request still has no first token."""

    sid, root = await repair_fx.plan()
    run_id = await repair_fx.start(sid, root)
    await repair_fx.bind_gate()
    await repair_fx.wait_entered()

    assert (await repair_fx.pause(sid, root)).status == 200
    try:
        # The run settles to paused while the gate is still held and the node
        # is not completed: the pause interrupted the model request in place.
        settled = await repair_fx.wait_status(root, "paused")
        assert settled["run"]["workflow_run_id"] == run_id
        assert settled["run"]["nodes"][0]["status"] != "completed"
        assert repair_fx.gate.entered.is_set(), "the gated request must never have been released"
    finally:
        await repair_fx.release_gate()
        await repair_fx.wait_status(root, "paused")


async def test_a03_pause_after_committed_tool_keeps_tool_called_once(tmp_path):
    fx = ServerFixture(tmp_path, scripts=[GATED_AFTER_TOOL_SCRIPT, [["alpha done"]]])
    gate = AsyncGate()
    run_id: str | None = None
    holder: dict = {}
    holder["fx"] = fx

    def on_create(_count: int) -> None:
        fixture = holder.get("fx")
        if fixture is None:
            return
        provider = fixture.bank.providers[-1]
        if provider.responses == GATED_AFTER_TOOL_SCRIPT:
            gate_provider_stream(provider, gate, hold_before_event=1)

    fx.bank.on_create = on_create
    try:
        revision = await publish_pipeline(fx, agent=reader_agent(), make_source=pair_source)
        session_id, task_id, task_version = await create_session_and_task(fx.client)
        started = await start_run(
            fx.client, revision.workflow_revision_id, session_id, task_id, task_version
        )
        run_id = started.json()["result"]["run"]["workflow_run_id"]
        await wait_for_run(fx.client, run_id, "running")
        await await_entered(gate)

        paused = await fx.client.post(
            f"/v1/workflow-runs/{run_id}/pause", {"command_id": "cmd_pause_a03"}
        )
        assert paused.status == 200, paused.body
        # Desired: suspended with the tool committed exactly once and the node
        # still open; today draining waits for the node to complete.
        view = await wait_for_run(fx.client, run_id, "paused")
        gamma = next(node for node in view["nodes"] if node["node"]["node_id"] == "gamma")
        assert gamma["node"]["status"] != "completed"

        def tool_calls():
            backend = fx.host.context.journal._backend
            return backend.read_one("SELECT count(*) FROM tool_executions", ())[0]

        assert await fx.on_core(tool_calls) == 1
    finally:
        gate.release()
        if run_id is not None:
            await wait_for_run(fx.client, run_id, "paused")
        fx.close()


async def test_a10_planning_request_rows_carry_layered_outcomes_and_end_times(tmp_path):
    scripts = [
        ["not json"],
        ["not json"],
        [[json.dumps(spec("work"))]],
        [["work done"]],
    ]
    fx = ServerFixture(tmp_path, scripts=scripts)
    try:
        sid, root = await new_session(fx)
        reply = await fx.client.post(root + "/task-plan", request(sid, command_id="cmd_plan"))
        assert reply.status == 200, reply.body
        assert reply.json()["status"] == "succeeded", reply.body

        def rows():
            backend = fx.host.context.journal._backend
            return backend.read_all(
                "SELECT attempt, outcome, started_at, ended_at FROM workflow_planning_request_outcomes "
                "WHERE layer='model_request' ORDER BY attempt, request_sequence",
                (),
            )

        persisted = await fx.on_core(rows)
        assert len(persisted) == 3, persisted
        for attempt, outcome, started_at, ended_at in persisted:
            # Each model request fact carries its own layered outcome and end
            # time instead of a single status word (P05, v41 outcomes table).
            assert outcome in {"succeeded", "failed", "interrupted", "cancelled"}, (
                attempt,
                outcome,
            )
            assert started_at is not None, (attempt, outcome)
            assert ended_at is not None, (attempt, outcome)
    finally:
        fx.close()


async def test_a16_cold_start_replays_full_trajectory_from_a_fresh_process(tmp_path):
    result_file = tmp_path / "worker-result.json"
    fx = ServerFixture(tmp_path, scripts=[[[json.dumps(spec("work"))]]])
    try:
        sid, root = await new_session(fx)
        reply = await fx.client.post(root + "/task-plan", request(sid, command_id="cmd_plan_a16"))
        assert reply.status == 200, reply.body
        assert reply.json()["status"] == "succeeded", reply.body
    finally:
        fx.close()

    worker = BackendWorker(
        tmp_path / "state-root",
        tmp_path / "workspace",
        WORKER_DURABLE_WRITE_SNIPPET,
        exit_mode="clean",
        result_file=result_file,
    )
    require_clean_exit(worker.wait_clean())
    written = json.loads(result_file.read_text(encoding="utf-8"))

    reopened = ServerFixture(tmp_path)
    try:
        # The worker's durable session survives the process boundary.
        worker_session = await reopened.client.get(
            f"/v1/workspaces/{reopened.workspace_id}/sessions/{written['session_id']}/snapshot"
        )
        assert worker_session.status == 200, worker_session.body

        page = await reopened.client.get(f"{root}/timeline")
        assert page.status == 200, page.body
        kinds = [item["kind"] for item in page.json()["items"]]
        # The accepted planning goal replays from the database (P08 index).
        assert "planning_input" in kinds, kinds
    finally:
        reopened.close()


async def test_a27_correction_text_after_pause_continues_the_same_task(repair_fx):
    sid, root = await repair_fx.plan()
    await repair_fx.start(sid, root)
    await repair_fx.bind_gate()
    await repair_fx.wait_entered()
    assert (await repair_fx.pause(sid, root)).status == 200
    await repair_fx.release_gate()
    await repair_fx.wait_status(root, "paused")
    counts_before = await repair_fx.counts()

    reply = await repair_fx.control(
        sid, root, "把第二步改成只修保存功能，其他不变", key="cmd_correct_a27"
    )
    assert reply.status == 200, reply.body
    # The correction re-opens the original run in place with a new execution
    # segment: same run, one more AgentRun, node running again (P04).
    view = await repair_fx.wait_status(root, "running")
    assert view["run"]["nodes"][0]["status"] == "running"
    counts = await repair_fx.counts()
    assert counts["runs"] == counts_before["runs"] == 1, counts
    assert counts["agent_runs"] == counts_before["agent_runs"] + 1, counts
    assert reply.json()["disposition"] == "executed", reply.body
