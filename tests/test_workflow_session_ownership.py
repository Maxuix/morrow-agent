"""Session ownership resolution: child execution sessions locate their own run.

A leaf (isolated node) Session must resolve to its NodeRun -> WorkflowRun ->
root Task -> root Session chain, read the frozen plan version the run was
admitted with, and stay out of the default Session index. All Providers are
scripted; no Live network access.
"""

from __future__ import annotations

import json

import pytest

from morrow.application.workflows.session_ownership import SessionWorkflowOwnership
from morrow.core.application import ApplicationError
from morrow.core.workflows.runs import WorkflowStatus
from test_stage7_isolated_workflow_slice import WS, SliceFixture, only_node, publish
from test_stage7_isolated_workflow_slice import start as slice_start
from test_stage8_chat_submission import new_session
from test_stage8_core_api import wait_for_run
from test_workflow_plan_admission import _planned
from test_workflow_task_planning import request, spec


@pytest.fixture
def slice_fx(tmp_path):
    fixture = SliceFixture(tmp_path)
    yield fixture
    fixture.close()


async def _start_planned_run(fx, sid, root, view, command_id="cmd_start"):
    execution = view["execution"]
    body = {
        "command_id": command_id,
        "session_id": sid,
        "draft_id": execution["draft_id"],
        "draft_version": execution["draft_version"],
        "execution_digest": execution["digest"],
        "action_source": "button",
        "interaction_id": "message1",
    }
    started = await fx.client.post(root + "/task-plan/start", body)
    assert started.status == 200, started.body
    return started.json()["run"]["workflow_run_id"]


async def _leaf_session(fx, run_id):
    def query():
        node = fx.host.context.journal.workflows.list_nodes(fx.workspace_id, run_id)[0]
        assert node.conversation_session_id is not None
        return node.conversation_session_id, node

    return await fx.host.execute_query(query)


# Planned run: frozen plan + owning DAG ------------------------------------------------


@pytest.mark.asyncio
async def test_leaf_session_view_resolves_owning_run_and_frozen_plan(tmp_path):
    fx, sid, root, view = await _planned(tmp_path, "work")
    try:
        run_id = await _start_planned_run(fx, sid, root, view)
        await wait_for_run(fx.client, run_id, "completed", "failed", "blocked")
        leaf, node = await _leaf_session(fx, run_id)

        leaf_view = (await fx.client.get(f"{root.rsplit('/', 1)[0]}/{leaf}/task-plan")).json()
        ownership = leaf_view["ownership"]
        assert ownership["role"] == "node"
        assert ownership["workflow_run_id"] == run_id
        assert ownership["node_run_id"] == node.node_run_id
        assert ownership["node_id"] == "work"
        assert ownership["root_task_run_id"] is not None
        assert ownership["root_session_id"] == sid
        assert ownership["issues"] == []
        # The frozen plan is the executed draft version, read-only.
        plan = leaf_view["plan"]
        assert plan["mode"] == "frozen"
        assert plan["draft_id"] == view["execution"]["draft_id"]
        assert plan["draft_version"] == view["execution"]["draft_version"]
        assert {n["node_id"] for n in plan["source"]["nodes"]} == {"work"}
        assert leaf_view["allowed_actions"] == []
        assert leaf_view["binding"] is None
        assert leaf_view["run"]["workflow_run_id"] == run_id
        assert {n["node_id"] for n in leaf_view["run"]["nodes"]} == {"work"}

        # The root session keeps its own planning view and reports root ownership.
        root_view = (await fx.client.get(root + "/task-plan")).json()
        assert root_view["ownership"]["role"] == "root"
        assert root_view["ownership"]["session_id"] == sid
        assert root_view["binding"] is not None
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_leaf_session_keeps_frozen_plan_after_root_regenerates(tmp_path):
    fx, sid, root, view = await _planned(tmp_path, "work")
    try:
        run_id = await _start_planned_run(fx, sid, root, view)
        await wait_for_run(fx.client, run_id, "completed", "failed", "blocked")
        leaf, _node = await _leaf_session(fx, run_id)
        frozen_version = view["execution"]["draft_version"]

        # The root session regenerates its draft after the run; the leaf view
        # must still show the version this run was admitted with.
        current = (await fx.client.get(root + "/task-plan")).json()
        fx.bank.scripts.append([[json.dumps(spec("work", "audit"))]])
        revised = await fx.client.post(
            root + "/task-plan",
            request(
                sid,
                command_id="cmd_plan2",
                task={"objective": "Rework the objective"},
                planning_binding_id=current["binding"]["planning_binding_id"],
                base_draft_version=current["draft"]["draft"]["row_version"],
            ),
        )
        assert revised.status == 200 and revised.json()["status"] == "succeeded", revised.body

        leaf_view = (await fx.client.get(f"{root.rsplit('/', 1)[0]}/{leaf}/task-plan")).json()
        assert leaf_view["plan"]["draft_version"] == frozen_version
        root_view = (await fx.client.get(root + "/task-plan")).json()
        assert root_view["draft"]["draft"]["row_version"] > frozen_version
    finally:
        fx.close()


# Direct published run: no planning provenance ----------------------------------


@pytest.mark.asyncio
async def test_direct_published_run_resolves_leaf_with_direct_plan(slice_fx):
    fx = slice_fx
    fx.bank.scripts.append([["final ", "answer"]])
    _, revision = publish(fx)
    started = slice_start(fx, revision)
    run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED
    node = only_node(fx, run.workflow_run_id)

    ownership = SessionWorkflowOwnership(fx.journal, WS).resolve(node.conversation_session_id)
    assert ownership["role"] == "node"
    assert ownership["workflow_run_id"] == run.workflow_run_id
    assert ownership["root_session_id"] == "ses_root"
    assert ownership["node_id"] == "worker"
    # A published Workflow never went through draft planning; the panel shows
    # the run definition instead of judging the plan missing.
    assert ownership["plan"] == {
        "mode": "direct",
        "origin": None,
        "planning_binding_id": None,
        "draft_id": None,
        "draft_version": None,
    }

    root_ownership = SessionWorkflowOwnership(fx.journal, WS).resolve("ses_root")
    assert root_ownership["role"] == "root"
    with pytest.raises(ApplicationError, match="Session is missing"):
        SessionWorkflowOwnership(fx.journal, WS).resolve("ses_unknown")


# Session index filtering --------------------------------------------------------


@pytest.mark.asyncio
async def test_session_index_hides_execution_sessions_until_requested(tmp_path):
    fx, sid, root, view = await _planned(tmp_path, "work")
    try:
        other, _other_root = await new_session(fx, "cmd_other_session")
        run_id = await _start_planned_run(fx, sid, root, view)
        await wait_for_run(fx.client, run_id, "completed", "failed", "blocked")
        leaf, _node = await _leaf_session(fx, run_id)
        base = f"/v1/workspaces/{fx.workspace_id}/sessions"

        default_page = (await fx.client.get(base + "?limit=100")).json()
        listed = {item["session_id"] for item in default_page["sessions"]}
        assert leaf not in listed
        assert sid in listed and other in listed

        full_page = (await fx.client.get(base + "?limit=100&include_execution=true")).json()
        assert leaf in {item["session_id"] for item in full_page["sessions"]}

        search_page = (await fx.client.get(base + f"?search={leaf}&archived=false")).json()
        assert search_page["sessions"] == []
        found = (
            await fx.client.get(base + f"?search={leaf}&archived=false&include_execution=true")
        ).json()
        assert [item["session_id"] for item in found["sessions"]] == [leaf]
    finally:
        fx.close()
