import pytest
from morrow.core.workflows.runs import WorkflowStatus
from test_stage7_serial_scheduler import DagFixture, ScriptBank, pair_source, publish, start

@pytest.mark.asyncio
async def test_debug(tmp_path):
    fx = DagFixture(tmp_path, bank=ScriptBank([["phase one"], ["phase three"]]))
    try:
        _, publication = publish(fx, pair_source)
        started = start(fx, publication.revision)
        done = await fx.runtime.scheduler.run(started.run.workflow_run_id)
        assert done.status is WorkflowStatus.COMPLETED
        import traceback
        try:
            fx.runtime.patches.rerun(started.run.workflow_run_id, full=True)
        except Exception:
            traceback.print_exc()
    finally:
        fx.close()
