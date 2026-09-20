"""P11 cross-process restart, kill durability and backup/restore acceptance.

Real OS-process coverage for A16-A18: a subprocess boots the real
composition against a data root, writes durable facts, then exits cleanly or
is SIGKILLed; the parent reopens the same root through the real server stack
and asserts what survives. A paused run never auto-drives after a restart, a
killed mid-run driver reconciles through recovery instead of faking state,
and backup/restore preserves the trajectory, pause facts and the ability to
continue. All model synchronization uses Core-loop gates, never sleeps.
"""

from __future__ import annotations

import json
import shutil

from fixtures.cross_process import BackendWorker
from test_stage8_chat_submission import drain
from test_stage8_core_api import ServerFixture, wait_for_run
from test_workflow_interrupt_continuation import RepairFixture
from test_workflow_task_planning import spec

#: Cross-process worker: plan a workflow, start it, park its node provider
#: behind a never-released gate, record the run identity, and wait to be
#: SIGKILLed. The run is durably RUNNING with an open turn at kill time.
KILL_MID_RUN_SNIPPET = """
import asyncio
import json
import time

from fixtures.parallel_gates import AsyncGate, gate_provider_stream
from test_workflow_task_planning import request, spec

gate = AsyncGate()


async def go():
    created = await client.post("/v1/sessions", {"command_id": "cmd_kill_session"})
    assert created.status == 200, created.body
    sid = created.json()["result"]["session"]["session_id"]
    root = f"/v1/workspaces/{workspace_id}/sessions/{sid}"
    plan = await client.post(root + "/task-plan", request(sid, command_id="cmd_kill_plan"))
    assert plan.status == 200 and plan.json()["status"] == "succeeded", plan.body
    # Only providers created from now on belong to the run: park them forever.
    bank.on_create = lambda _count: gate_provider_stream(
        bank.providers[-1], gate, hold_before_event=0
    )
    execution = (await client.get(root + "/task-plan")).json()["execution"]
    started = await client.post(
        root + "/task-plan/start",
        {
            "command_id": "cmd_kill_start",
            "session_id": sid,
            "draft_id": execution["draft_id"],
            "draft_version": execution["draft_version"],
            "execution_digest": execution["digest"],
            "action_source": "button",
            "interaction_id": "message1",
        },
    )
    assert started.status == 200, started.body
    run_id = started.json()["run"]["workflow_run_id"]
    deadline = time.monotonic() + 60
    while not gate.entered.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert gate.entered.is_set(), "the run never reached its gated model request"
    with open(settings["result_file"], "w", encoding="utf-8") as handle:
        json.dump({"run_id": run_id, "session_id": sid}, handle)


asyncio.run(go())
"""


async def test_paused_run_stays_paused_across_restart_and_continues(tmp_path):
    """A17 (paused half): the restart keeps the pause; the user continue resumes."""
    repair = RepairFixture(tmp_path)
    try:
        sid, root = await repair.plan()
        await repair.start(sid, root)
        await repair.bind_gate()
        await repair.wait_entered()
        assert (await repair.pause(sid, root)).status == 200
        await repair.wait_status(root, "paused")
    finally:
        await repair.release_gate()
        await repair.wait_status(root, "paused")
        repair.close()

    reopened = ServerFixture(tmp_path, scripts=[["node one continues"], ["node two done"]])
    try:
        # No driver may pick the paused run up on its own; it stays paused.
        view = (await reopened.client.get(root + "/task-plan")).json()
        assert view["run"]["status"] == "paused"
        assert view["run"]["pause_requested"] is True
        # The parked node still projects its paused execution state from the
        # same durable facts — no driver, no auto-resume, no ghost running.
        run_view = await wait_run_view(reopened, view["run"]["workflow_run_id"])
        parked = [item for item in run_view["nodes"] if item["node"]["status"] == "running"]
        assert len(parked) == 1
        assert parked[0]["execution"]["state"] == "paused"
        assert parked[0]["execution"]["segment_id"] is not None
        assert parked[0]["execution"]["control_generation"] == 1
        assert parked[0]["execution"]["reason"] == "user_interrupt"
        assert parked[0]["execution"]["settled_at"] is not None
        assert all(
            item["execution"] is None
            for item in run_view["nodes"]
            if item["node"]["status"] != "running"
        )

        continued = await reopened.client.post(
            root + "/task-plan/control",
            {"command_id": "cmd_restart_continue", "session_id": sid, "text": "继续"},
        )
        assert continued.status == 200, continued.body
        assert continued.json()["disposition"] == "executed", continued.body

        def run_counts():
            backend = reopened.host.context.journal._backend
            return backend.read_one("SELECT count(*) FROM workflow_runs", ())[0]

        assert await reopened.on_core(run_counts) == 1
        view = await wait_for_run(reopened.client, view["run"]["workflow_run_id"], "completed")
        assert all(node["node"]["status"] == "completed" for node in view["nodes"])
        await drain(reopened, sid)
    finally:
        reopened.close()


async def test_kill_mid_run_reconciles_without_ghost_running(tmp_path):
    """A17/A18 (killed half): SIGKILL mid-run leaves honest recovery evidence."""
    result_file = tmp_path / "kill-result.json"
    ready_marker = tmp_path / "kill-ready"
    worker = BackendWorker(
        tmp_path / "state-root",
        tmp_path / "workspace",
        KILL_MID_RUN_SNIPPET,
        exit_mode="kill",
        ready_marker=ready_marker,
        result_file=result_file,
        scripts=[[[json.dumps(spec("one", "two"))]], [["one done"]], [["two done"]]],
    )
    worker.wait_ready()
    written = json.loads(result_file.read_text(encoding="utf-8"))
    assert worker.kill() != 0  # SIGKILLed, never a clean exit

    reopened = ServerFixture(tmp_path, scripts=[["recovered turn"], ["one done"], ["two done"]])
    try:
        sid = written["session_id"]
        run_id = written["run_id"]
        root = f"/v1/workspaces/{reopened.workspace_id}/sessions/{sid}"

        # The durable facts survived the kill: exactly one run, still running.
        def run_count():
            backend = reopened.host.context.journal._backend
            return backend.read_one("SELECT count(*) FROM workflow_runs", ())[0]

        assert await reopened.on_core(run_count) == 1
        view = await wait_run_view(reopened, run_id)
        assert view["run"]["status"] == "running", view["run"]

        # The projection reports the lost driver honestly; nothing shows a
        # live model/tool working, and nothing auto-drives the run.
        snapshot = await reopened.client.get(root + "/snapshot")
        assert snapshot.status == 200, snapshot.body
        execution = snapshot.json()["execution"]
        assert execution is not None and execution["state"] == "needs_recovery", execution

        # The user-facing resume drives recovery of the open turn to a terminal
        # run without resurrecting a ghost driver or a second workflow run.
        resumed = await reopened.client.post(
            f"/v1/workflow-runs/{run_id}/resume", {"command_id": "cmd_resume_kill"}
        )
        assert resumed.status == 200, resumed.body
        view = await wait_for_run(reopened.client, run_id, "completed")
        assert view["run"]["result_status"] == "succeeded"
        assert await reopened.on_core(run_count) == 1
    finally:
        reopened.close()


async def test_backup_restore_preserves_trajectory_and_pause_facts(tmp_path):
    """P11.4: backup/restore keeps trajectory, pause facts and continuation."""
    from morrow.adapters.state.operational import OperationalStore
    from morrow.application.backup import OperationalBackupService
    from morrow.core.store import SUPPORTED_SCHEMA_VERSION

    repair = RepairFixture(tmp_path)
    try:
        sid, root = await repair.plan()
        await repair.start(sid, root)
        await repair.bind_gate()
        await repair.wait_entered()
        assert (await repair.pause(sid, root)).status == 200
        await repair.wait_status(root, "paused")
    finally:
        await repair.release_gate()
        await repair.wait_status(root, "paused")
        repair.close()

    state_root = tmp_path / "state-root"
    store = OperationalStore(state_root)
    backup = OperationalBackupService(store)
    created = backup.create("workflow-continuity-p11")
    assert created.schema_version == SUPPORTED_SCHEMA_VERSION
    bundle = store.layout.backups_dir / created.bundle_name
    assert backup.verify(bundle).ok

    # Recovery flow: restore into a fresh data root from the surviving bundle,
    # then swap it in as the live store (same workspace directory, so the
    # restored identity still matches the workspace on disk).
    restored_root = tmp_path / "state-root-restored"
    report = backup.restore(bundle, restored_root)
    assert report.ok, report
    shutil.rmtree(state_root)
    restored_root.rename(state_root)

    reopened = ServerFixture(tmp_path, scripts=[["node one continues"], ["node two done"]])
    try:
        view = (await reopened.client.get(root + "/task-plan")).json()
        assert view["run"] is not None and view["run"]["status"] == "paused", view["run"]
        timeline = (await reopened.client.get(root + "/timeline")).json()
        assert any(item["kind"] == "planning_input" for item in timeline["items"]), timeline

        continued = await reopened.client.post(
            root + "/task-plan/control",
            {"command_id": "cmd_restore_continue", "session_id": sid, "text": "继续"},
        )
        assert continued.status == 200, continued.body
        assert continued.json()["disposition"] == "executed", continued.body
        view = await wait_for_run(reopened.client, view["run"]["workflow_run_id"], "completed")
        assert all(node["node"]["status"] == "completed" for node in view["nodes"])
        await drain(reopened, sid)
    finally:
        reopened.close()


async def wait_run_view(fixture, run_id):
    response = await fixture.client.get(f"/v1/workflow-runs/{run_id}")
    assert response.status == 200, response.body
    return response.json()["view"]
