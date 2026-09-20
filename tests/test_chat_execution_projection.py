"""Unified execution projection: lifecycle coverage, input capability, stop routing.

The projection must keep the Chat surface in the run state across Workflow
node switches, parallel frontiers, pause/drain settlement and cancel intents,
and the backend itself must reject input into isolated node conversations.
"""

import asyncio
import json

from morrow.application.execution import ExecutionProjection
from morrow.core.workflows.contracts import NodeOutputRef
from morrow.core.workflows.definitions import WorkflowBudget, WorkflowDefinitionSource, WorkflowEdge
from test_stage7_serial_scheduler import chain_node
from test_stage8_chat_submission import drain, new_session
from test_stage8_core_api import ServerFixture, publish_pipeline
from test_workflow_task_planning import request as plan_request
from test_workflow_task_planning import spec as plan_spec


def fanout_source(ref, *, count=2, concurrency=2):
    """Parallel read frontier: two isolated readers admitted together."""
    ids = tuple(f"reader_{index}" for index in range(count))
    return WorkflowDefinitionSource(
        workflow_definition_id="pipeline",
        name="Read frontier",
        default_budget=WorkflowBudget(
            max_agent_generation_requests=10,
            default_node_max_agent_generation_requests=3,
            admission_timeout_seconds=300,
            max_concurrency=concurrency,
        ),
        nodes=(
            *(chain_node(ref, name, f"Inspect area {index}") for index, name in enumerate(ids)),
            chain_node(ref, "summary", "Summarize the findings"),
        ),
        edges=tuple(WorkflowEdge(from_node_id=name, to_node_id="summary") for name in ids),
        required_outputs=(NodeOutputRef(node_id="summary", output_slot="result"),),
    )


def install_stream_gate(fx, expected):
    """Park every subsequently created Provider inside stream() until released.

    Returns (wait_parked, release). Both cross the Core-loop boundary through
    the same patterns the existing chat workflow tests use; no wall-clock
    assertion is involved.
    """

    state = {"parked": 0, "release": None}

    def hook(_count):
        provider = fx.bank.providers[-1]
        original = provider.stream

        async def held(*args, **kwargs):
            state["parked"] += 1
            await state["release"].wait()
            async for event in original(*args, **kwargs):
                yield event

        provider.stream = held

    def install():
        state["release"] = asyncio.Event()
        state["parked"] = 0
        fx.bank.on_create = hook

    async def wait_parked():
        # Poll on the Core loop itself: the parked streams and the counter
        # share that loop, so the observable fact is authoritative.
        for _ in range(2000):
            if state["parked"] >= expected:
                return True
            await asyncio.sleep(0.01)
        return False

    def release():
        state["release"].set()

    return install, wait_parked, release


async def snapshot_execution(fx, path):
    return (await fx.client.get(path + "/snapshot")).json()


async def test_parallel_workflow_projection_covers_nodes_and_rejects_leaf_input(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        revision = await publish_pipeline(fx, make_source=fanout_source)
        sid, path = await new_session(fx)
        install, wait_parked, release = install_stream_gate(fx, expected=2)
        await fx.on_core(install)
        before = await snapshot_execution(fx, path)
        sent = await fx.client.post(
            path + "/interactions",
            {
                "client_message_id": "workflow.input",
                "intent": "explicit_workflow",
                "text": "Inspect project structure",
                "workflow": {
                    "workflow_definition_id": "pipeline",
                    "workflow_revision_id": revision.workflow_revision_id,
                },
            },
        )
        assert sent.status == 202, sent.body
        assert await fx.host.execute_preparation(wait_parked)

        snapshot = await snapshot_execution(fx, path)
        execution = snapshot["execution"]
        assert execution["owner"] == "workflow"
        assert execution["state"] == "running"
        assert execution["root_session_id"] == sid
        assert execution["leaf"] is False
        assert execution["accepts_chat_input"] is True
        assert sorted(execution["node_run_ids"]) and len(execution["node_run_ids"]) == 2
        assert "stop" in execution["allowed_actions"]
        # The stream advanced past the idle snapshot: lifecycle changes push
        # queue_changed frames instead of leaving a stale running view.
        assert snapshot["sequence"] > before["sequence"]
        frames = await fx.on_core(
            lambda: [f[0]["type"] for f in fx.host.context.chat.streams.state(sid).frames]
        )
        assert "queue_changed" in frames
        run_id = execution["workflow_run_id"]

        # The isolated node conversations are execution detail: their own
        # snapshot reports the owning run, and the backend rejects input.
        nodes = await fx.on_core(
            lambda: fx.host.context.journal.workflows.list_nodes(fx.workspace_id, run_id)
        )
        leaf_ids = {
            node.conversation_session_id
            for node in nodes
            if node.conversation_session_id is not None
        }
        assert len(leaf_ids) == 2 and sid not in leaf_ids
        for leaf_sid in leaf_ids:
            leaf_path = f"/v1/workspaces/{fx.workspace_id}/sessions/{leaf_sid}"
            leaf = await snapshot_execution(fx, leaf_path)
            assert leaf["execution"]["owner"] == "workflow"
            assert leaf["execution"]["leaf"] is True
            assert leaf["execution"]["accepts_chat_input"] is False
            rejected = await fx.client.post(
                leaf_path + "/interactions",
                {"client_message_id": f"leaf.{leaf_sid[-6:]}", "text": "hello"},
            )
            assert rejected.status == 409, rejected.body

        # Stop routing: the workflow cancel endpoint records the intent and
        # settles the run; afterwards the projection is idle again.
        cancelled = await fx.client.post(
            f"/v1/workflow-runs/{run_id}/cancel", {"command_id": "cmd_cancel_projection"}
        )
        assert cancelled.status == 200, cancelled.body
        for _ in range(2000):
            run = await fx.on_core(
                lambda: fx.host.context.journal.workflows.get_run(fx.workspace_id, run_id)
            )
            if run.status.terminal:
                break
            await asyncio.sleep(0.01)
        assert run.status.terminal
        settled = (await snapshot_execution(fx, path))["execution"]
        assert settled["owner"] is None and settled["state"] == "idle"
    finally:
        fx.close()


async def test_pause_keeps_projection_through_drain_and_settles_paused(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        revision = await publish_pipeline(fx)
        sid, path = await new_session(fx)
        install, wait_parked, release = install_stream_gate(fx, expected=1)
        await fx.on_core(install)
        sent = await fx.client.post(
            path + "/interactions",
            {
                "client_message_id": "workflow.input",
                "intent": "explicit_workflow",
                "text": "Inspect project structure",
                "workflow": {
                    "workflow_definition_id": "pipeline",
                    "workflow_revision_id": revision.workflow_revision_id,
                },
            },
        )
        assert sent.status == 202, sent.body
        assert await fx.host.execute_preparation(wait_parked)
        execution = (await snapshot_execution(fx, path))["execution"]
        assert execution["state"] == "running"
        assert len(execution["node_run_ids"]) == 1
        suspended_node = execution["node_run_ids"][0]
        run_id = execution["workflow_run_id"]

        paused = await fx.client.post(
            f"/v1/workflow-runs/{run_id}/pause", {"command_id": "cmd_pause_projection"}
        )
        assert paused.status == 200, paused.body
        draining = (await snapshot_execution(fx, path))["execution"]
        assert draining["state"] == "pausing"
        assert draining["owner"] == "workflow"  # never flashes back to idle

        await fx.on_core(release)
        for _ in range(2000):
            run = await fx.on_core(
                lambda: fx.host.context.journal.workflows.get_run(fx.workspace_id, run_id)
            )
            if run.status is type(run.status).PAUSED or run.status.terminal:
                break
            await asyncio.sleep(0.01)
        assert run.status.value == "paused"
        settled = (await snapshot_execution(fx, path))["execution"]
        assert settled["owner"] == "workflow" and settled["state"] == "paused"
        assert settled["allowed_actions"] == ["resume", "change"]
        # The interrupted node suspends at its execution segment instead of
        # finishing: the projection keeps listing it while the run is paused.
        assert settled["node_run_ids"] == [suspended_node]

        resumed = await fx.client.post(
            f"/v1/workflow-runs/{run_id}/resume",
            {"command_id": "cmd_resume_projection", "drive": True},
        )
        assert resumed.status == 200, resumed.body
        for _ in range(2000):
            run = await fx.on_core(
                lambda: fx.host.context.journal.workflows.get_run(fx.workspace_id, run_id)
            )
            if run.status.terminal:
                break
            await asyncio.sleep(0.01)
        await drain(fx, sid)
        final = (await snapshot_execution(fx, path))["execution"]
        assert final["owner"] is None and final["state"] == "idle"
    finally:
        fx.close()


async def test_parallel_pause_barrier_and_node_execution_projection(tmp_path):
    """BUG-GUI-002 (P02): the pause barrier is per-node even though the pause
    point is a run-level singleton.

    Both parallel readers hold gate-funded model calls; a user pause must
    interrupt every turn, close every node's latest segment, and only then
    settle the run paused. Each RUNNING node carries its own execution
    projection (running → pausing → paused) computed from its own segments.
    """
    fx = ServerFixture(tmp_path)
    try:
        revision = await publish_pipeline(fx, make_source=fanout_source)
        sid, path = await new_session(fx)
        install, wait_parked, release = install_stream_gate(fx, expected=2)
        await fx.on_core(install)
        sent = await fx.client.post(
            path + "/interactions",
            {
                "client_message_id": "workflow.input",
                "intent": "explicit_workflow",
                "text": "Inspect project structure",
                "workflow": {
                    "workflow_definition_id": "pipeline",
                    "workflow_revision_id": revision.workflow_revision_id,
                },
            },
        )
        assert sent.status == 202, sent.body
        assert await fx.host.execute_preparation(wait_parked)
        run_id = (await snapshot_execution(fx, path))["execution"]["workflow_run_id"]
        run_view = (await fx.client.get(f"/v1/workflow-runs/{run_id}")).json()["view"]
        active = [item for item in run_view["nodes"] if item["node"]["status"] == "running"]
        assert len(active) == 2
        assert all(item["execution"]["state"] == "running" for item in active)

        paused = await fx.client.post(
            f"/v1/workflow-runs/{run_id}/pause", {"command_id": "cmd_pause_barrier"}
        )
        assert paused.status == 200, paused.body

        # The drain settles WITHOUT releasing the gate: the pause interrupts
        # every gate-held model wait, and the run may pass DRAINING only once
        # EVERY running node's latest segment is interrupted.
        for _ in range(2000):
            run = await fx.on_core(
                lambda: fx.host.context.journal.workflows.get_run(fx.workspace_id, run_id)
            )
            if run.status.value in {"paused", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.01)
        assert run.status.value == "paused", run.status.value

        def segments_and_point():
            journal = fx.host.context.journal
            nodes = journal.workflows.list_nodes(fx.workspace_id, run_id)
            facts = {}
            for node in nodes:
                segments = journal.workflows.segments_for_node(fx.workspace_id, node.node_run_id)
                facts[node.node_id] = [segment.status for segment in segments]
            point = journal.workflows.execution_pause.latest_pause_point(
                fx.workspace_id, owner="workflow_run", owner_id=run_id
            )
            return facts, point.fact.lifecycle if point is not None else None

        segment_facts, lifecycle = await fx.on_core(segments_and_point)
        assert lifecycle == "suspended"
        assert segment_facts["reader_0"][-1] == "interrupted"
        assert segment_facts["reader_1"][-1] == "interrupted"

        # Each still-RUNNING node projects its own paused execution state from
        # its own interrupted segment; the run-level pause point only supplies
        # lifecycle/generation/reason.
        run_view = (await fx.client.get(f"/v1/workflow-runs/{run_id}")).json()["view"]
        for item in run_view["nodes"]:
            if item["node"]["status"] != "running":
                continue
            execution = item["execution"]
            assert execution["state"] == "paused", execution
            assert execution["segment_id"] is not None
            assert execution["control_generation"] == 1
            assert execution["reason"] == "user_interrupt"
            assert execution["settled_at"] is not None
        paused_nodes = [item for item in run_view["nodes"] if item["node"]["status"] == "running"]
        assert len(paused_nodes) == 2
        # Per-node segments, not the run-level singleton's last-suspend refs:
        # both nodes' paused segments must be the two distinct reader segments.
        journal_segments = await fx.on_core(
            lambda: {
                node.node_id: [
                    segment.segment_id
                    for segment in fx.host.context.journal.workflows.segments_for_node(
                        fx.workspace_id, node.node_run_id
                    )
                ]
                for node in fx.host.context.journal.workflows.list_nodes(fx.workspace_id, run_id)
            }
        )
        assert {item["execution"]["segment_id"] for item in paused_nodes} == {
            journal_segments["reader_0"][-1],
            journal_segments["reader_1"][-1],
        }
        assert journal_segments["reader_0"][-1] != journal_segments["reader_1"][-1]

        # The queued summary node never started: no execution projection.
        summary = next(item for item in run_view["nodes"] if item["node"]["node_id"] == "summary")
        assert summary["node"]["status"] == "queued"
        assert summary["execution"] is None

        await fx.on_core(release)
    finally:
        fx.close()


def test_project_node_execution_state_table():
    """The shared per-node projection: five states from durable facts only."""
    from morrow.application.workflows.queries import node_execution_wire, project_node_execution
    from morrow.core.contracts import ExecutionSegmentIdentity, PauseIntentFact
    from morrow.core.execution_pause import WorkflowPausePoint
    from morrow.core.workflows.runs import NodeRun
    from test_stage7_workflow_domain import NOW
    from test_stage8_pause_drain import _run

    def run(**changes):
        return _run(**changes)

    def node(status, **changes):
        return NodeRun(
            workspace_id="ws_one",
            node_run_id="nrun_one",
            workflow_run_id="wrun_one",
            node_id="alpha",
            status=status,
            started_at=NOW,
            conversation_session_id="ses_leaf",
            leaf_task_run_id="task_leaf",
            agent_run_id="arun_leaf",
            **changes,
        )

    def segment(status, *, ordinal=1, pause_reason=None):
        return ExecutionSegmentIdentity(
            segment_id=f"seg_{ordinal}",
            workflow_run_id="wrun_one",
            node_run_id="nrun_one",
            ordinal=ordinal,
            status=status,
            pause_reason=pause_reason,
        )

    def point(lifecycle, *, suspended_at=None):
        return WorkflowPausePoint(
            pause_point_id="pp_one",
            workspace_id="ws_one",
            fact=PauseIntentFact(
                control_generation=1,
                command_id="cmd_pause",
                owner="workflow_run",
                owner_id="wrun_one",
                reason="user_interrupt",
                lifecycle=lifecycle,
                requested_at=NOW,
                suspended_at=suspended_at,
            ),
            created_at=NOW,
            updated_at=NOW + __import__("datetime").timedelta(seconds=1),
        )

    project = project_node_execution

    # Terminal and queued nodes are not projected: the GUI falls back to the
    # business status and a paused parent run can never override it.
    terminal = node("completed", completed_at=NOW)
    assert (
        project(
            run=run(status="paused", pause_requested=True),
            node=terminal,
            segments=(segment("interrupted"),),
            pause_point=point("suspended"),
        )
        is None
    )
    queued = NodeRun(
        workspace_id="ws_one",
        node_run_id="nrun_q",
        workflow_run_id="wrun_one",
        node_id="beta",
        status="queued",
    )
    assert project(run=run(status="running"), node=queued, segments=(), pause_point=None) is None
    # A blocked node always invites recovery.
    blocked = project(
        run=run(status="blocked", pause_requested=True),
        node=node("blocked"),
        segments=(),
        pause_point=None,
    )
    assert blocked is not None and blocked.state == "needs_recovery"
    # No pause accepted: plain running, segment facts irrelevant.
    running = project(
        run=run(status="running"),
        node=node("running"),
        segments=(segment("active"),),
        pause_point=None,
    )
    assert running is not None and running.state == "running"
    assert running.segment_id is None and running.control_generation is None
    # Pause accepted with an active segment (or a still-open cycle): pausing.
    pausing = project(
        run=run(status="draining", pause_requested=True),
        node=node("running"),
        segments=(segment("active"),),
        pause_point=point("requested"),
    )
    assert pausing is not None and pausing.state == "pausing"
    assert pausing.control_generation == 1 and pausing.reason == "user_interrupt"
    quiescing = project(
        run=run(status="draining", pause_requested=True),
        node=node("running"),
        segments=(segment("interrupted", pause_reason="user_interrupt"),),
        pause_point=point("quiescing"),
    )
    assert quiescing is not None and quiescing.state == "pausing"
    # Settled: interrupted own segment + suspended cycle → paused with refs.
    settled = project(
        run=run(status="paused", pause_requested=True),
        node=node("running"),
        segments=(segment("interrupted", pause_reason="user_interrupt"),),
        pause_point=point("suspended", suspended_at=NOW),
    )
    assert settled is not None and settled.state == "paused"
    assert settled.segment_id == "seg_1"
    assert settled.control_generation == 1
    assert settled.reason == "user_interrupt"
    assert settled.settled_at == NOW
    # Missing or contradictory segment facts under an accepted pause: recover.
    missing = project(
        run=run(status="draining", pause_requested=True),
        node=node("running"),
        segments=(),
        pause_point=point("requested"),
    )
    assert missing is not None and missing.state == "needs_recovery"
    contradictory = project(
        run=run(status="draining", pause_requested=True),
        node=node("running"),
        segments=(segment("completed"),),
        pause_point=point("suspended"),
    )
    assert contradictory is not None and contradictory.state == "needs_recovery"
    # The run-level singleton's segment refs never leak into another node's
    # projection: paused reads only its own latest segment.
    other = project(
        run=run(status="paused", pause_requested=True),
        node=NodeRun(
            workspace_id="ws_one",
            node_run_id="nrun_two",
            workflow_run_id="wrun_one",
            node_id="beta",
            status="running",
            started_at=NOW,
            conversation_session_id="ses_leaf2",
            leaf_task_run_id="task_leaf2",
            agent_run_id="arun_leaf2",
        ),
        segments=(segment("interrupted", ordinal=2, pause_reason="user_interrupt"),),
        pause_point=point("suspended"),
    )
    assert other is not None and other.segment_id == "seg_2"
    # Frozen wire shape.
    wire = node_execution_wire(settled)
    assert wire == {
        "state": "paused",
        "segment_id": "seg_1",
        "control_generation": 1,
        "reason": "user_interrupt",
        "settled_at": NOW.isoformat(),
    }
    assert node_execution_wire(None) is None


async def test_node_execution_projection_pause_resume_new_segment(tmp_path):
    """Pause → resume: the projection returns to running, the durable segment
    history gains a new segment, and the control generation is preserved."""
    fx = ServerFixture(tmp_path)
    try:
        revision = await publish_pipeline(fx)
        sid, path = await new_session(fx)
        install, wait_parked, release = install_stream_gate(fx, expected=1)
        await fx.on_core(install)
        sent = await fx.client.post(
            path + "/interactions",
            {
                "client_message_id": "workflow.input",
                "intent": "explicit_workflow",
                "text": "Inspect project structure",
                "workflow": {
                    "workflow_definition_id": "pipeline",
                    "workflow_revision_id": revision.workflow_revision_id,
                },
            },
        )
        assert sent.status == 202, sent.body
        assert await fx.host.execute_preparation(wait_parked)
        run_id = (await snapshot_execution(fx, path))["execution"]["workflow_run_id"]
        node_view = (await fx.client.get(f"/v1/workflow-runs/{run_id}")).json()["view"]
        active = next(item for item in node_view["nodes"] if item["node"]["status"] == "running")
        assert active["execution"]["state"] == "running"

        paused = await fx.client.post(
            f"/v1/workflow-runs/{run_id}/pause", {"command_id": "cmd_pause_seg"}
        )
        assert paused.status == 200, paused.body
        # While the drain is open the node projects pausing with the accepted
        # generation, even though its business status stays running.
        pausing_view = (await fx.client.get(f"/v1/workflow-runs/{run_id}")).json()["view"]
        pausing = next(
            item for item in pausing_view["nodes"] if item["node"]["status"] == "running"
        )
        assert pausing["execution"]["state"] == "pausing"
        assert pausing["execution"]["control_generation"] == 1

        await fx.on_core(release)
        for _ in range(2000):
            run = await fx.on_core(
                lambda: fx.host.context.journal.workflows.get_run(fx.workspace_id, run_id)
            )
            if run.status.value == "paused":
                break
            await asyncio.sleep(0.01)
        assert run.status.value == "paused"
        paused_view = (await fx.client.get(f"/v1/workflow-runs/{run_id}")).json()["view"]
        paused_node = next(
            item for item in paused_view["nodes"] if item["node"]["status"] == "running"
        )
        assert paused_node["execution"]["state"] == "paused"
        interrupted_segment = paused_node["execution"]["segment_id"]
        assert interrupted_segment is not None

        # The plan-admission run projection carries the same per-node state.
        plan_view = (await fx.client.get(path + "/task-plan")).json()
        plan_node = next(
            item
            for item in plan_view["run"]["nodes"]
            if item["node_id"] == paused_node["node"]["node_id"]
        )
        assert plan_node["execution"]["state"] == "paused"
        assert plan_node["execution"]["segment_id"] == interrupted_segment

        resumed = await fx.client.post(
            f"/v1/workflow-runs/{run_id}/resume",
            {"command_id": "cmd_resume_seg", "drive": True},
        )
        assert resumed.status == 200, resumed.body
        for _ in range(2000):
            run = await fx.on_core(
                lambda: fx.host.context.journal.workflows.get_run(fx.workspace_id, run_id)
            )
            if run.status.terminal:
                break
            await asyncio.sleep(0.01)
        assert run.status.value == "completed"

        def segment_history():
            journal = fx.host.context.journal
            node = next(
                n
                for n in journal.workflows.list_nodes(fx.workspace_id, run_id)
                if n.node_id == paused_node["node"]["node_id"]
            )
            segments = journal.workflows.segments_for_node(fx.workspace_id, node.node_run_id)
            point = journal.workflows.execution_pause.latest_pause_point(
                fx.workspace_id, owner="workflow_run", owner_id=run_id
            )
            return [(s.segment_id, s.status) for s in segments], (
                point.fact.lifecycle,
                point.fact.control_generation,
                point.continuation_segment_id,
            )

        segments, cycle = await fx.on_core(segment_history)
        assert len(segments) == 2
        assert segments[0] == (interrupted_segment, "interrupted")
        # NOTE the resumed segment is appended by the continuation turn; the
        # product does not currently close segments on node completion.
        assert cycle == ("resumed", 1, segments[1][0])

        # A completed node is no longer projected; the GUI shows business state.
        final_view = (await fx.client.get(f"/v1/workflow-runs/{run_id}")).json()["view"]
        final_node = next(
            item
            for item in final_view["nodes"]
            if item["node"]["node_id"] == paused_node["node"]["node_id"]
        )
        assert final_node["node"]["status"] == "completed"
        assert final_node["execution"] is None
    finally:
        fx.close()


async def test_stale_running_record_without_driver_needs_recovery(tmp_path):
    """After a restart no driver holds the run; the projection must not
    claim progress from durable running rows alone."""
    fx = ServerFixture(tmp_path)
    try:
        revision = await publish_pipeline(fx)
        sid, path = await new_session(fx)
        install, wait_parked, release = install_stream_gate(fx, expected=1)
        await fx.on_core(install)
        sent = await fx.client.post(
            path + "/interactions",
            {
                "client_message_id": "workflow.input",
                "intent": "explicit_workflow",
                "text": "Inspect project structure",
                "workflow": {
                    "workflow_definition_id": "pipeline",
                    "workflow_revision_id": revision.workflow_revision_id,
                },
            },
        )
        assert sent.status == 202, sent.body
        assert await fx.host.execute_preparation(wait_parked)
        execution = (await snapshot_execution(fx, path))["execution"]
        assert execution["state"] == "running"  # live driver reports progress

        # Post-restart condition: durable RUNNING rows, no in-process driver.
        def rebuild():
            projection = ExecutionProjection(
                manager=fx.host.context.chat,
                journal=fx.host.context.journal,
                workspace_id=fx.workspace_id,
                supervisor=None,
                planning=None,
            )
            return projection.build(sid)

        stale = await fx.on_core(rebuild)
        assert stale["owner"] == "workflow"
        assert stale["state"] == "needs_recovery"
        assert stale["allowed_actions"] == []

        # Mid-cancel observable state: the durable user-cancel intent is
        # recorded while the driver is still settling; the projection shows
        # stopping and never offers a second stop.
        stopping = await fx.on_core(
            lambda: fx.host.context.runtime.transitions.set_pending_user_cancel(
                execution["workflow_run_id"]
            )
        )
        assert stopping.pending_terminal_intent == "user_cancel"
        recorded = (await snapshot_execution(fx, path))["execution"]
        assert recorded["state"] == "stopping"
        assert "stop" not in recorded["allowed_actions"]
        await fx.on_core(release)
    finally:
        fx.close()


async def test_chat_run_projection_and_stop_routing(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fx)
        install, wait_parked, release = install_stream_gate(fx, expected=1)
        await fx.on_core(install)
        idle = (await snapshot_execution(fx, path))["execution"]
        assert idle["owner"] is None and idle["state"] == "idle"
        sent = await fx.client.post(
            path + "/interactions", {"client_message_id": "chat.1", "text": "hello"}
        )
        assert sent.status == 202, sent.body
        assert await fx.host.execute_preparation(wait_parked)
        execution = (await snapshot_execution(fx, path))["execution"]
        assert execution["owner"] == "chat"
        assert execution["state"] == "running"
        assert execution["agent_run_id"] is not None
        queue = (await fx.client.get(path + "/queue")).json()
        assert queue["active_agent_run_id"] == execution["agent_run_id"]

        stopped = await fx.client.post(
            path + "/control",
            {
                "command_id": "cmd_chat_stop_projection",
                "action": "stop",
                "expected_revision": queue["revision"],
                "target_agent_run_id": queue["active_agent_run_id"],
            },
        )
        assert stopped.status == 200, stopped.body
        await fx.on_core(release)
        await drain(fx, sid)
        final = (await snapshot_execution(fx, path))["execution"]
        assert final["owner"] is None and final["state"] == "idle"
    finally:
        fx.close()


async def test_planning_generation_projection_and_cancel(tmp_path):
    fx = ServerFixture(tmp_path, scripts=[[[json.dumps(plan_spec("build", "audit"))]]])
    try:
        sid, root = await new_session(fx)
        install, wait_parked, release = install_stream_gate(fx, expected=1)
        await fx.on_core(install)
        generation = asyncio.create_task(fx.client.post(root + "/task-plan", plan_request(sid)))
        assert await fx.host.execute_preparation(wait_parked)
        execution = (await snapshot_execution(fx, root))["execution"]
        assert execution["owner"] == "planning"
        assert execution["state"] == "running"
        assert execution["planning_operation_id"]
        assert "stop" in execution["allowed_actions"]

        operation_id = execution["planning_operation_id"]
        cancelled = await fx.client.post(root + f"/task-plan/operations/{operation_id}", {})
        assert cancelled.status == 200, cancelled.body
        awaiting = (await snapshot_execution(fx, root))["execution"]
        assert awaiting["owner"] == "planning"

        await fx.on_core(release)
        reply = await asyncio.wait_for(generation, 10)
        assert reply.status == 200, reply.body
        final = (await snapshot_execution(fx, root))["execution"]
        assert final["owner"] is None and final["state"] == "idle"
    finally:
        fx.close()
