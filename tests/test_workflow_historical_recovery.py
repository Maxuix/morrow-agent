"""Restricted recovery of historical serial model-failure Workflow runs.

The screenshot-era failures were settled by the pre-continuity runtime: a
Provider-layer model fault failed the node, cancelled the queued successor,
closed the tasks and left the Workflow terminal. This suite manufactures that
exact durable shape through the public journal, then proves the recovery
command reopens the SAME run identities, rebuilds the pause cycle and hands
over to the ordinary resume flow — and that ineligible failures refuse with
concrete reasons instead of mutating anything.
"""

from __future__ import annotations

from morrow.core.domain import DurableTaskRunTransition, TaskRunStatus
from morrow.core.models import AgentStopCode, FinishReason
from morrow.core.workflows.runs import WorkflowStatus
from test_stage7_serial_scheduler import pair_source, reader_agent
from test_stage8_core_api import (
    ServerFixture,
    create_session_and_task,
    publish_pipeline,
    start_run,
)
from test_workflow_interrupt_continuation import READ_CALL_MSG
from test_workflow_repeat_interruption import (
    ALPHA_SCRIPT,
    GAMMA_DRIVE3,
    _net,
    _wait_status,
)

#: gamma drive #1 commits a read tool call then fails non-retryably; the
#: recovery scripts finish gamma and run alpha after the historical repair.
_HIST_GAMMA_DRIVE1 = [READ_CALL_MSG, _net(retryable=False)]


async def _segment_statuses(fx, run_id):
    def query():
        backend = fx.host.context.journal._backend
        return backend.read_all(
            "SELECT status FROM workflow_node_segments WHERE workflow_run_id=? ORDER BY rowid",
            (run_id,),
        )

    rows = await fx.on_core(query)
    return [row[0] for row in rows]


async def _tool_count(fx):
    def query():
        backend = fx.host.context.journal._backend
        return backend.read_one(
            "SELECT count(*) FROM tool_executions WHERE workspace_id=?",
            (fx.workspace_id,),
        )[0]

    return await fx.on_core(query)


async def _fail_like_legacy(fx, run_id, *, stop_code: str) -> None:
    """Settle a parked run the way the pre-continuity runtime did."""

    def work():
        journal = fx.host.context.journal
        runtime = fx.host.context.runtime
        transitions = runtime.transitions
        id_source = runtime.scheduler.id_source
        run = journal.workflows.get_run(fx.workspace_id, run_id)
        nodes = journal.workflows.list_nodes(fx.workspace_id, run_id)
        failed_node = next(n for n in nodes if n.status is WorkflowStatus.RUNNING)
        segment = journal.workflows.segments_for_node(fx.workspace_id, failed_node.node_run_id)[-1]
        # The old runtime finalized the parked agent run as a Provider-layer
        # error; the current exactly-once guard must be bypassed at fixture
        # level to reproduce that historical row.
        metrics = journal.get_agent_run_terminal_metrics(fx.workspace_id, segment.agent_run_id)
        legacy = metrics.model_copy(
            update={
                "finish_reason": FinishReason.ERROR,
                "stop_code": AgentStopCode(stop_code),
            }
        )
        backend = journal._backend

        def rewrite():
            def w():
                backend.executor().execute(
                    "UPDATE agent_runs SET terminal_metrics_json=? WHERE agent_run_id=?",
                    (legacy.model_dump_json(), segment.agent_run_id),
                )

            backend.transact(w)

        rewrite()

        def close_tasks(txn):
            for task_run_id in (
                failed_node.leaf_task_run_id,
                run.root_task_run_id,
            ):
                task = txn.get_task_run(fx.workspace_id, task_run_id)
                txn.transition_workflow_task(
                    fx.workspace_id,
                    run_id,
                    task_run_id,
                    target=TaskRunStatus.FAILED,
                    transition=DurableTaskRunTransition(
                        transition_id=id_source.new_id("ttr"),
                        workspace_id=fx.workspace_id,
                        session_id=task.session_id,
                        task_run_id=task.task_run_id,
                        from_status=task.status,
                        to_status=TaskRunStatus.FAILED,
                        reason="workflow_node_failed",
                        attempt=task.attempt,
                    ),
                    expected_row_version=task.row_version,
                )

        journal.transact(close_tasks)
        transitions.fail_node(failed_node.node_run_id)
        for node in nodes:
            if node.status is WorkflowStatus.QUEUED:
                transitions.cancel_node(node.node_run_id)
        # The legacy runtime failed straight from RUNNING; the parked run is
        # unpaused first so the terminal mapping matches the old evidence.
        transitions.resume_run(run_id)
        transitions.fail_run(run_id)

    await fx.on_core(work)


async def _start(fx):
    from morrow.core.workflows.definitions import WorkflowBudget

    budget = WorkflowBudget(
        max_agent_generation_requests=40,
        default_node_max_agent_generation_requests=15,
        admission_timeout_seconds=300,
        max_concurrency=1,
    )
    revision = await publish_pipeline(
        fx, agent=reader_agent(), make_source=pair_source, budget=budget
    )
    session_id, task_id, task_version = await create_session_and_task(fx.client)
    started = await start_run(
        fx.client, revision.workflow_revision_id, session_id, task_id, task_version
    )
    assert started.status == 200, started.body
    run_id = started.json()["result"]["run"]["workflow_run_id"]
    await _wait_status(fx, run_id, "running")
    return run_id


async def test_historical_provider_failure_recovers_in_place(tmp_path):
    fx = ServerFixture(tmp_path, scripts=[_HIST_GAMMA_DRIVE1, GAMMA_DRIVE3, ALPHA_SCRIPT])
    run_id = await _start(fx)
    try:
        # Interrupt #1 parks the run; the legacy settlement then leaves the
        # exact terminal shape the old runtime committed.
        await _wait_status(fx, run_id, "paused")
        await _fail_like_legacy(fx, run_id, stop_code="provider_network")
        view = (await fx.client.get(f"/v1/workflow-runs/{run_id}")).json()["view"]
        assert view["run"]["status"] == "failed"
        statuses = await _segment_statuses(fx, run_id)
        assert statuses == ["interrupted"]

        # One "从中断处继续" command recovers and resumes to completion.
        resumed = await fx.client.post(
            f"/v1/workflow-runs/{run_id}/resume", {"command_id": "cmd_recover_1"}
        )
        assert resumed.status == 200, resumed.body
        await _wait_status(fx, run_id, "completed")

        def facts():
            backend = fx.host.context.journal._backend
            journal = fx.host.context.journal
            run = journal.workflows.get_run(fx.workspace_id, run_id)
            nodes = journal.workflows.list_nodes(fx.workspace_id, run_id)
            tasks = backend.read_all(
                "SELECT task_run_id, status, attempt FROM task_runs ORDER BY rowid", ()
            )
            transitions = backend.read_all(
                "SELECT task_run_id, from_status, to_status, reason FROM task_run_transitions "
                "WHERE reason='workflow_historical_recovery'",
                (),
            )
            receipt = backend.read_one(
                "SELECT count(*) FROM command_receipts WHERE command_id=?",
                ("cmd_recover_1_recover",),
            )[0]
            return run, nodes, tasks, transitions, receipt

        run, nodes, tasks, transitions, receipt = await fx.on_core(facts)
        assert run.status is WorkflowStatus.COMPLETED
        assert all(n.status is WorkflowStatus.COMPLETED for n in nodes)
        assert await _segment_statuses(fx, run_id) == [
            "interrupted",
            "completed",
            "completed",
        ]
        assert await _tool_count(fx) == 1, "the committed tool call must not replay"
        assert receipt == 1, "the recovery command must leave its own receipt"
        assert len(transitions) == 2, "root and leaf tasks reopen with audited transitions"
        reopened = [row for row in tasks if row[1] == "ready_for_acceptance" and row[2] == 2]
        assert len(reopened) == 2, f"both tasks reopen with attempt 2: {tasks}"

        # A repeated command replays without mutating anything.
        replay = await fx.client.post(
            f"/v1/workflow-runs/{run_id}/resume", {"command_id": "cmd_recover_1"}
        )
        assert replay.status == 200, replay.body
        assert replay.json()["receipt"]["disposition"] == "replay"
    finally:
        fx.close()


async def test_recovered_pause_state_resumes_after_a_crash(tmp_path):
    fx = ServerFixture(tmp_path, scripts=[_HIST_GAMMA_DRIVE1, GAMMA_DRIVE3, ALPHA_SCRIPT])
    run_id = await _start(fx)
    try:
        await _wait_status(fx, run_id, "paused")
        await _fail_like_legacy(fx, run_id, stop_code="provider_network")

        # Recovery alone commits the paused hand-off state; a crash before the
        # resume must leave the run continuable from durable facts only.
        def before_snapshot():
            journal = fx.host.context.journal
            backend = journal._backend
            nodes = journal.workflows.list_nodes(fx.workspace_id, run_id)
            gamma = next(n for n in nodes if n.node_id == "gamma")
            segment = journal.workflows.segments_for_node(fx.workspace_id, gamma.node_run_id)[-1]
            records = backend.read_one("SELECT count(*) FROM conversation_records", ())[0]
            return (
                segment.model_dump(),
                records,
            )

        before = await fx.on_core(before_snapshot)

        await fx.on_core(
            lambda: fx.host.context.management.recover_failed_workflow(
                run_id, command_id="cmd_recover_crash"
            )
        )
        view = (await fx.client.get(f"/v1/workflow-runs/{run_id}")).json()["view"]
        assert view["run"]["status"] == "paused"
        assert await _segment_statuses(fx, run_id) == ["interrupted"]

        # The recovered mid-state: the failed node runs again, the proven
        # never-started successor is queued again, both tasks are open and the
        # durable pause cycle carries the failure boundary as its safety point.
        def midstate():
            journal = fx.host.context.journal
            backend = journal._backend
            nodes = {
                n.node_id: str(getattr(n.status, "value", n.status))
                for n in journal.workflows.list_nodes(fx.workspace_id, run_id)
            }
            tasks = backend.read_all("SELECT status, attempt FROM task_runs ORDER BY rowid", ())
            point = backend.read_one(
                "SELECT lifecycle, body_json LIKE '%safety%' FROM workflow_pause_points "
                "WHERE owner='workflow_run' ORDER BY control_generation DESC LIMIT 1",
                (),
            )
            safety_stop = backend.read_one(
                r"""SELECT json_extract(body_json,'$.safety.stop_code') FROM workflow_pause_points """
                "WHERE owner='workflow_run' ORDER BY control_generation DESC LIMIT 1",
                (),
            )[0]
            return nodes, tasks, point, safety_stop

        nodes, tasks, point, safety_stop = await fx.on_core(midstate)
        assert nodes == {"gamma": "running", "alpha": "queued"}, nodes
        assert tasks == (("open", 2), ("open", 2)), tasks
        assert point[0] == "suspended" and point[1] == 1, point
        assert safety_stop == "provider_network", safety_stop

        # Old terminal evidence is untouched by the recovery transaction.
        after = await fx.on_core(before_snapshot)
        assert after[0] == before[0], "the interrupted segment row must not change"
        assert after[1] == before[1], "conversation records must not change"

        resumed = await fx.client.post(
            f"/v1/workflow-runs/{run_id}/resume", {"command_id": "cmd_after_crash"}
        )
        assert resumed.status == 200, resumed.body
        await _wait_status(fx, run_id, "completed")
        assert await _segment_statuses(fx, run_id) == [
            "interrupted",
            "completed",
            "completed",
        ]
        assert await _tool_count(fx) == 1
    finally:
        fx.close()


async def test_ambiguous_internal_failure_refuses_recovery(tmp_path):
    fx = ServerFixture(tmp_path, scripts=[_HIST_GAMMA_DRIVE1, GAMMA_DRIVE3, ALPHA_SCRIPT])
    run_id = await _start(fx)
    try:
        await _wait_status(fx, run_id, "paused")
        await _fail_like_legacy(fx, run_id, stop_code="internal")
        providers_before = len(fx.bank.providers)

        resumed = await fx.client.post(
            f"/v1/workflow-runs/{run_id}/resume", {"command_id": "cmd_refused"}
        )
        assert resumed.status != 200, resumed.body
        assert b"historical recovery refused" in resumed.body, resumed.body

        view = (await fx.client.get(f"/v1/workflow-runs/{run_id}")).json()["view"]
        assert view["run"]["status"] == "failed", "an ineligible run stays failed"
        assert await _segment_statuses(fx, run_id) == ["interrupted"]
        assert await _tool_count(fx) == 1, "no tool may replay during a refusal"
        assert len(fx.bank.providers) == providers_before, (
            "a refused recovery must not prepare any new Provider"
        )
    finally:
        fx.close()


async def test_started_successor_blocks_recovery(tmp_path):
    fx = ServerFixture(tmp_path, scripts=[_HIST_GAMMA_DRIVE1, GAMMA_DRIVE3, ALPHA_SCRIPT])
    run_id = await _start(fx)
    try:
        await _wait_status(fx, run_id, "paused")
        await _fail_like_legacy(fx, run_id, stop_code="provider_network")

        # A cancelled sibling that cannot prove it never started (here: an
        # agent run attribution) must block the automatic requeue.
        def mark_started():
            import json

            journal = fx.host.context.journal
            backend = journal._backend
            alpha = next(
                n
                for n in journal.workflows.list_nodes(fx.workspace_id, run_id)
                if n.node_id == "alpha"
            )

            def w():
                row = backend.read_one(
                    "SELECT body_json FROM workflow_node_runs WHERE node_run_id=?",
                    (alpha.node_run_id,),
                )
                body = json.loads(row[0])
                # Admission references bind together; the ghost run drags its
                # session/task pair along so the row still parses.
                body["agent_run_id"] = "arun_legacy_ghost"
                body["conversation_session_id"] = "ses_legacy_ghost"
                body["leaf_task_run_id"] = "task_legacy_ghost"
                backend.executor().execute(
                    "UPDATE workflow_node_runs SET body_json=? WHERE node_run_id=?",
                    (json.dumps(body), alpha.node_run_id),
                )

            backend.transact(w)

        await fx.on_core(mark_started)

        refused = await fx.client.post(
            f"/v1/workflow-runs/{run_id}/resume", {"command_id": "cmd_refused_successor"}
        )
        assert refused.status != 200, refused.body
        assert b"agent run" in refused.body, refused.body

        view = (await fx.client.get(f"/v1/workflow-runs/{run_id}")).json()["view"]
        assert view["run"]["status"] == "failed"
        nodes = {
            n["node"]["node_id"]: n["node"]["status"]
            for n in (await fx.client.get(f"/v1/workflow-runs/{run_id}")).json()["view"]["nodes"]
        }
        assert nodes["alpha"] == "cancelled", "a started sibling must never be requeued"
    finally:
        fx.close()


async def test_command_id_reuse_across_runs_conflicts(tmp_path):
    fx = ServerFixture(
        tmp_path,
        scripts=[
            _HIST_GAMMA_DRIVE1,
            _HIST_GAMMA_DRIVE1,
            GAMMA_DRIVE3,
            ALPHA_SCRIPT,
            ALPHA_SCRIPT,
        ],
    )
    run_a = await _start(fx)
    run_b = await _start(fx)
    try:
        await _wait_status(fx, run_a, "paused")
        await _fail_like_legacy(fx, run_a, stop_code="provider_network")
        await _wait_status(fx, run_b, "paused")
        await _fail_like_legacy(fx, run_b, stop_code="provider_network")

        shared = "cmd_shared_recover"
        await fx.on_core(
            lambda: fx.host.context.management.recover_failed_workflow(run_a, command_id=shared)
        )
        from morrow.core.application import ApplicationError

        try:
            await fx.on_core(
                lambda: fx.host.context.management.recover_failed_workflow(run_b, command_id=shared)
            )
        except ApplicationError as exc:
            assert "reused" in str(exc) or "conflict" in str(exc).lower(), exc
        else:
            raise AssertionError("a reused command id must conflict")

        view = (await fx.client.get(f"/v1/workflow-runs/{run_b}")).json()["view"]
        assert view["run"]["status"] == "failed", "the conflicting call mutates nothing"
    finally:
        fx.close()


async def test_shared_resume_entry_recovers_a_failed_run(tmp_path):
    """CLI resume and the server resume command share management.resume — a
    failed run resumed through that single entry recovers first, then flows
    into the ordinary continuation admission."""

    fx = ServerFixture(tmp_path, scripts=[_HIST_GAMMA_DRIVE1, GAMMA_DRIVE3, ALPHA_SCRIPT])
    run_id = await _start(fx)
    try:
        await _wait_status(fx, run_id, "paused")
        await _fail_like_legacy(fx, run_id, stop_code="provider_network")

        # management.resume is the shared CLI/server entry; drive the
        # coroutine on the Core thread like the command bus does.
        await fx.host.execute_command(
            lambda: fx.host.context.management.resume(run_id, command_id="cmd_cli_resume")
        )
        await _wait_status(fx, run_id, "completed")
        assert await _segment_statuses(fx, run_id) == [
            "interrupted",
            "completed",
            "completed",
        ]
        assert await _tool_count(fx) == 1
    finally:
        fx.close()


_HIST_GAMMA_DRIVE1 = [READ_CALL_MSG, _net(retryable=False)]
