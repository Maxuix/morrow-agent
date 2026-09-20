"""A provider interruption parks a node and continues the same WorkflowRun."""

import json

import pytest

from morrow.core.models import ModelErrorCode, ModelEvent, ModelFailure, ModelFailureOrigin
from test_stage8_chat_submission import new_session
from test_stage8_core_api import ServerFixture
from test_workflow_task_planning import request, spec


async def _view(fx, root):
    response = await fx.client.get(root + "/task-plan")
    assert response.status == 200, response.body
    return response.json()


@pytest.mark.asyncio
async def test_provider_failure_pauses_workflow_and_resume_keeps_completed_nodes(tmp_path):
    failure = ModelEvent(
        kind="error",
        failure=ModelFailure(
            code=ModelErrorCode.NETWORK,
            origin=ModelFailureOrigin.PROVIDER,
            retryable=False,
            message="上游连接中断",
        ),
    )
    fx = ServerFixture(
        tmp_path,
        scripts=[[[json.dumps(spec("one", "two"))]], [failure], [["two done"]]],
    )
    try:
        sid, root = await new_session(fx)
        planned = await fx.client.post(root + "/task-plan", request(sid, command_id="cmd_plan"))
        assert planned.status == 200, planned.body
        execution = (await _view(fx, root))["execution"]
        started = await fx.client.post(
            root + "/task-plan/start",
            {
                "command_id": "cmd_start",
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

        for _ in range(400):
            view = await _view(fx, root)
            if view["run"] and view["run"]["status"] == "paused":
                break
        else:

            def diagnostics():
                journal = fx.host.context.journal
                run = journal.workflows.get_run(fx.workspace_id, run_id)
                node = journal.workflows.list_nodes(fx.workspace_id, run_id)[0]
                task = journal.get_task_run(fx.workspace_id, node.leaf_task_run_id)
                records = journal.load_records(fx.workspace_id, node.conversation_session_id)
                return (
                    run.status.value,
                    task.status.value if task else None,
                    [
                        (getattr(r, "finish_reason", None), getattr(r, "stop_code", None))
                        for r in records
                    ],
                    journal.workflows.segments_for_node(fx.workspace_id, node.node_run_id),
                )

            raise AssertionError(
                f"provider interruption did not park the workflow: {view!r}; {await fx.on_core(diagnostics)!r}"
            )
        paused = view["run"]
        assert paused["workflow_run_id"] == run_id
        assert paused["nodes"][0]["status"] == "running"
        assert paused["nodes"][1]["status"] == "queued"

        resumed = await fx.client.post(
            f"/v1/workflow-runs/{run_id}/resume", {"command_id": "cmd_resume"}
        )
        assert resumed.status == 200, resumed.body
        for _ in range(400):
            view = await _view(fx, root)
            if view["run"] and view["run"]["status"] == "completed":
                break
        else:
            raise AssertionError("workflow did not complete after provider interruption")
        assert [node["status"] for node in view["run"]["nodes"]] == ["completed", "completed"]
        assert view["run"]["workflow_run_id"] == run_id

        def facts():
            backend = fx.host.context.journal._backend
            return (
                backend.read_one(
                    "SELECT count(*) FROM workflow_node_segments WHERE workflow_run_id=?",
                    (run_id,),
                )[0],
                backend.read_one(
                    "SELECT count(*) FROM tool_executions WHERE workspace_id=?",
                    (fx.workspace_id,),
                )[0],
            )

        segments, tools = await fx.on_core(facts)
        assert segments == 3  # first node has two segments; second has one
        assert tools == 0

        def lineage():
            rows = fx.host.context.journal._backend.read_all(
                "SELECT agent_run_id, resume_of_agent_run_id FROM agent_runs "
                "WHERE agent_run_id IN (SELECT DISTINCT agent_run_id "
                "FROM workflow_node_segments WHERE workflow_run_id=?) ORDER BY rowid",
                (run_id,),
            )
            return rows

        lineage_rows = await fx.on_core(lineage)
        assert sum(parent is not None for _, parent in lineage_rows) == 1
    finally:
        fx.close()
