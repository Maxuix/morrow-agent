"""Workflow consistency dry-run and safe settlement (plan S5.1/S5.2)."""

from __future__ import annotations

import pytest

from morrow.application.workflows.consistency import WorkflowConsistencyService
from test_workflow_pause_continue_repair import RepairFixture


@pytest.fixture
def repair(tmp_path):
    fixture = RepairFixture(tmp_path)
    yield fixture
    fixture.close()


def _service(repair: RepairFixture) -> WorkflowConsistencyService:
    context = repair.fx.host.context
    return WorkflowConsistencyService(
        context.journal,
        repair.fx.workspace_id,
        id_source=context.application.id_source,
    )


async def _cancelled_run(repair: RepairFixture):
    sid, root = await repair.plan()
    run_id = await repair.start(sid, root)
    await repair.bind_gate()
    await repair.wait_entered()
    assert (await repair.cancel(run_id)).status == 200
    await repair.release_gate()
    await repair.wait_status(root, "cancelled")
    return sid, root, run_id


async def test_consistency_scan_is_clean_after_a_cancelled_run(repair):
    """S3 收束后，已取消运行不再留下 open 叶子或未闭合 turn。"""

    sid, _root, run_id = await _cancelled_run(repair)
    report = await repair.fx.on_core(lambda: _service(repair).scan(session_id=sid))
    assert report.runs_scanned >= 1, report.wire()
    assert report.findings == [], report.wire()

    def runs():
        return [
            run.workflow_run_id
            for run in repair.fx.host.context.journal.workflows.list_runs(repair.fx.workspace_id)
        ]

    assert run_id in await repair.fx.on_core(runs)


async def test_consistency_settles_a_legacy_open_leaf_idempotently(repair):
    """旧库遗留的 open 叶子被正式 API 收束；重放同一 command_id 不再执行。"""

    sid, _root, run_id = await _cancelled_run(repair)

    def plant_legacy_residue():
        journal = repair.fx.host.context.journal
        node = journal.workflows.list_nodes(repair.fx.workspace_id, run_id)[0]

        # Simulate a store written before the cancellation closure fix: the
        # leaf TaskRun row is open even though its owner run is terminal.
        def work(_):
            journal._backend.executor().execute(
                "UPDATE task_runs SET status='open', closed_at_unix=NULL WHERE task_run_id=?",
                (node.leaf_task_run_id,),
            )

        journal.transact(work)
        return node.node_run_id, node.leaf_task_run_id

    node_run_id, leaf_task_run_id = await repair.fx.on_core(plant_legacy_residue)

    def scan():
        return _service(repair).scan(session_id=sid).wire()

    findings = await repair.fx.on_core(scan)
    kinds = [item["kind"] for item in findings["findings"]]
    assert "open_leaf_task" in kinds, findings

    def settle():
        return _service(repair).settle(session_id=sid, command_id="cmd_consistency_1").wire()

    report = await repair.fx.on_core(settle)
    assert [item["task_run_id"] for item in report["settled"]] == [leaf_task_run_id], report
    assert report["settled"][0]["to_status"] == "cancelled", report
    assert report["settled"][0]["reason"] == "consistency_settle", report

    def leaf_status():
        leaf = repair.fx.host.context.journal.get_task_run(repair.fx.workspace_id, leaf_task_run_id)
        return leaf.status.value

    assert await repair.fx.on_core(leaf_status) == "cancelled"
    assert await repair.fx.on_core(scan) == {
        "runs_scanned": report["runs_scanned"],
        "findings": [],
        "settled": [],
        "skipped": [],
        "replayed": False,
    }

    # The same command ID replays instead of settling again; nothing is left
    # to close, and the receipt marks the call as a replay.
    replayed = await repair.fx.on_core(settle)
    assert replayed["replayed"] is True, replayed
    assert replayed["settled"] == [], replayed
    assert node_run_id


async def test_consistency_never_reopens_a_cancelled_run(repair):
    """已取消运行不能改回 running；收束只动可证明安全的叶子。"""

    sid, _root, run_id = await _cancelled_run(repair)

    def settle_and_status():
        _service(repair).settle(session_id=sid, command_id="cmd_consistency_2")
        run = repair.fx.host.context.journal.workflows.get_run(repair.fx.workspace_id, run_id)
        return run.status.value

    assert await repair.fx.on_core(settle_and_status) == "cancelled"
