"""Task-plan pause and change-mode binding: durable drain, no in-place rewrite."""

import json

import pytest

from morrow.application.workflow_controls import classify_change_request, classify_pause_request
from test_stage8_chat_submission import new_session
from test_stage8_core_api import ServerFixture, wait_for_run
from test_workflow_task_planning import request, spec


def _plan_scripts(*nodes):
    return [[[json.dumps(spec(*nodes))]]]


async def _planned(tmp_path, *nodes):
    fx = ServerFixture(tmp_path, scripts=_plan_scripts(*nodes))
    sid, root = await new_session(fx)
    reply = await fx.client.post(root + "/task-plan", request(sid, command_id="cmd_plan"))
    assert reply.status == 200 and reply.json()["status"] == "succeeded", reply.body
    view = (await fx.client.get(root + "/task-plan")).json()
    return fx, sid, root, view


def _start_body(sid, view, key="cmd_start"):
    execution = view["execution"]
    return {
        "command_id": key,
        "session_id": sid,
        "draft_id": execution["draft_id"],
        "draft_version": execution["draft_version"],
        "execution_digest": execution["digest"],
        "action_source": "button",
        "interaction_id": "message1",
    }


async def _started(tmp_path, *nodes, pause_at_first=False, pause_after=0):
    fx, sid, root, view = await _planned(tmp_path, *nodes)
    existing = len(fx.bank.providers)
    target = 1 if pause_at_first else pause_after
    if target:

        def hold(count):
            if count < existing + target:
                return
            run = fx.host.context.chat.admission._current_run(sid)
            if run is not None and not run.pause_requested and not run.status.terminal:
                fx.host.context.runtime.transitions.request_pause(run.workflow_run_id)

        fx.bank.on_create = hold
    for _ in nodes:
        fx.bank.scripts.append(["node complete"])
    started = await fx.client.post(root + "/task-plan/start", _start_body(sid, view))
    assert started.status == 200, started.body
    return fx, sid, root, started.json()["run"]["workflow_run_id"]


def test_pause_and_change_classifiers():
    assert classify_pause_request("暂停") is True
    assert classify_pause_request("先停一下") is True
    assert classify_pause_request("pause") is True
    assert classify_pause_request("不要暂停") is False
    assert classify_pause_request("取消") is False
    assert classify_pause_request("暂停后改计划") is False
    assert classify_change_request("改一下后续步骤") is True
    assert classify_change_request("加一个检查节点") is True
    assert classify_change_request("暂停后改计划") is True
    assert classify_change_request("暂停") is False


@pytest.mark.asyncio
async def test_pause_closes_admission_and_does_not_pretend_stopped(tmp_path):
    fx, sid, root, run_id = await _started(tmp_path, "work", "audit", pause_at_first=True)
    try:
        await wait_for_run(fx.client, run_id, "paused", "draining", "running", "queued")
        body = {"command_id": "cmd_pause", "session_id": sid}
        paused = await fx.client.post(root + "/task-plan/pause", body)
        assert paused.status == 200, paused.body
        run = paused.json()["run"]
        assert run["pause_requested"] is True
        assert run["status"] in {"draining", "paused"}
        if run["status"] == "draining":
            assert run["status"] != "paused"
            assert run["active_node_ids"] or any(
                node["status"] in {"running", "blocked"} for node in run["nodes"]
            )
        replay = await fx.client.post(root + "/task-plan/pause", body)
        assert replay.status == 200 and replay.json()["replayed"] is True

        settled = await wait_for_run(fx.client, run_id, "paused", "completed", "failed")
        assert settled["run"]["pause_requested"] is True
        if settled["run"]["status"] == "paused":
            nodes = {item["node"]["node_id"]: item["node"]["status"] for item in settled["nodes"]}
            queued = [status for status in nodes.values() if status == "queued"]
            running = [status for status in nodes.values() if status == "running"]
            assert running == []
            assert queued or "completed" in nodes.values()

        view = (await fx.client.get(root + "/task-plan")).json()
        assert view["run"]["pause_requested"] is True
        assert "change" in view["allowed_actions"]
        assert "pause" not in view["allowed_actions"] or view["run"]["status"] != "paused"
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_prepare_change_pauses_and_opens_change_binding(tmp_path):
    fx, sid, root, run_id = await _started(tmp_path, "work", "audit", pause_at_first=True)
    try:
        await wait_for_run(fx.client, run_id, "paused", "draining", "running", "queued")
        await fx.client.post(
            root + "/task-plan/pause", {"command_id": "cmd_pause_first", "session_id": sid}
        )
        await wait_for_run(fx.client, run_id, "paused", "draining")
        body = {
            "command_id": "cmd_change",
            "session_id": sid,
            "origin_interaction_id": "message_change",
            "action_source": "button",
        }
        changed = await fx.client.post(root + "/task-plan/change", body)
        assert changed.status == 200, changed.body
        payload = changed.json()
        assert payload["run"]["pause_requested"] is True
        binding = payload["binding"]
        assert binding["mode"] == "change"
        assert binding["parent_run_id"] == run_id
        assert binding["parent_revision_id"] == payload["run"]["workflow_revision_id"]
        assert binding["status"] == "active"
        replay = await fx.client.post(root + "/task-plan/change", body)
        assert replay.status == 200 and replay.json()["replayed"] is True
        assert replay.json()["binding"]["planning_binding_id"] == binding["planning_binding_id"]

        view = (await fx.client.get(root + "/task-plan")).json()
        assert view["binding"]["mode"] == "change"
        assert view["binding"]["planning_binding_id"] == binding["planning_binding_id"]
        assert view["binding"]["parent_run_id"] == run_id
        assert view["draft"] is not None
        assert view["candidate"]["parent_run_id"] == run_id
        assert view["candidate"]["digest"]
        assert "save_candidate" in view["allowed_actions"]

        def facts():
            journal = fx.host.context.journal
            active = journal.workflows.planning.binding(fx.workspace_id, sid)
            initial = journal._backend.read_one(
                "SELECT count(*) FROM workflow_planning_bindings "
                "WHERE workspace_id=? AND session_id=? AND status='superseded'",
                (fx.workspace_id, sid),
            )[0]
            return active.mode, active.parent_run_id, initial

        mode, parent, superseded = await fx.on_core(facts)
        assert mode == "change" and parent == run_id and superseded >= 1
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_running_revise_routes_to_change_binding_not_in_place_generate(tmp_path):
    fx, sid, root, run_id = await _started(tmp_path, "work", "audit", pause_at_first=True)
    try:
        await wait_for_run(fx.client, run_id, "paused", "draining", "running", "queued")
        await fx.client.post(
            root + "/task-plan/pause", {"command_id": "cmd_pause_hold", "session_id": sid}
        )
        await wait_for_run(fx.client, run_id, "paused", "draining")
        generate = await fx.client.post(
            root + "/task-plan",
            request(sid, command_id="cmd_regen", task={"objective": "rewrite the running plan"}),
        )
        assert generate.status == 409, generate.body

        fx.bank.scripts[:] = [[json.dumps({"intent": "revise", "answer": ""})]]
        control = await fx.client.post(
            root + "/task-plan/control",
            {"command_id": "cmd_ctrl_change", "session_id": sid, "text": "改一下后续步骤"},
        )
        assert control.status == 200, control.body
        body = control.json()
        assert body["disposition"] == "executed" and body["intent"] == "revise"
        assert body["binding"]["mode"] == "change"
        assert body["run"]["pause_requested"] is True
        assert "operation" not in body

        view = (await fx.client.get(root + "/task-plan")).json()
        assert view["binding"]["mode"] == "change"
        assert view["run"]["workflow_run_id"] == run_id
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_chat_pause_control_is_durable_not_frontend_only(tmp_path):
    fx, sid, root, run_id = await _started(tmp_path, "work", "audit", pause_at_first=True)
    try:
        await wait_for_run(fx.client, run_id, "paused", "draining", "running", "queued")
        await fx.client.post(
            root + "/task-plan/pause", {"command_id": "cmd_pause_ctrl", "session_id": sid}
        )
        await wait_for_run(fx.client, run_id, "paused", "draining")
        fx.bank.scripts[:] = [[json.dumps({"intent": "pause_run", "answer": ""})]]
        control = await fx.client.post(
            root + "/task-plan/control",
            {"command_id": "cmd_ctrl_pause", "session_id": sid, "text": "暂停"},
        )
        assert control.status == 200, control.body
        body = control.json()
        assert body["disposition"] == "executed" and body["intent"] == "pause_run"
        assert body["run"]["pause_requested"] is True
        assert body["run"]["status"] in {"draining", "paused"}

        def pause_fact():
            run = fx.host.context.journal.workflows.get_run(fx.workspace_id, run_id)
            return run.pause_requested, run.status.value

        requested, status = await fx.on_core(pause_fact)
        assert requested is True
        assert status in {"draining", "paused", "completed"}
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_terminal_run_cannot_pause_or_open_change_binding(tmp_path):
    fx, sid, root, run_id = await _started(tmp_path, "work")
    try:
        finished = await wait_for_run(fx.client, run_id, "completed", "failed")
        assert finished["run"]["status"] in {"completed", "failed"}
        pause = await fx.client.post(
            root + "/task-plan/pause", {"command_id": "cmd_pause_done", "session_id": sid}
        )
        assert pause.status == 400, pause.body
        change = await fx.client.post(
            root + "/task-plan/change",
            {
                "command_id": "cmd_change_done",
                "session_id": sid,
                "origin_interaction_id": "message_done",
                "action_source": "button",
            },
        )
        assert change.status == 400, change.body
        view = (await fx.client.get(root + "/task-plan")).json()
        assert view["binding"]["mode"] == "initial"
        assert "change" not in view["allowed_actions"]
    finally:
        fx.close()


async def _change_view(tmp_path, *nodes):
    fx, sid, root, run_id = await _started(tmp_path, *nodes, pause_after=2)
    await wait_for_run(fx.client, run_id, "paused", "draining")
    pause = await fx.client.post(
        root + "/task-plan/pause", {"command_id": "cmd_pause_change", "session_id": sid}
    )
    assert pause.status == 200, pause.body
    changed = await fx.client.post(
        root + "/task-plan/change",
        {
            "command_id": "cmd_open_change",
            "session_id": sid,
            "origin_interaction_id": "message_change",
            "action_source": "button",
        },
    )
    assert changed.status == 200, changed.body
    view = (await fx.client.get(root + "/task-plan")).json()
    return fx, sid, root, run_id, view


@pytest.mark.asyncio
async def test_past_nodes_cannot_be_rewritten_on_change_draft(tmp_path):
    fx, sid, root, run_id, view = await _change_view(tmp_path, "work", "audit")
    try:
        past = view["candidate"]["past_node_ids"]
        assert past
        node_id = past[0]
        edit = await fx.client.post(
            root + "/task-plan/nodes",
            {
                "command_id": "cmd_edit_past",
                "binding_id": view["binding"]["planning_binding_id"],
                "expected_version": view["draft"]["draft"]["row_version"],
                "action": "replace",
                "node": {
                    "node_id": node_id,
                    "title": node_id,
                    "task": "rewrite an admitted node",
                    "agent": "preset:general",
                    "responsibility": "implementation",
                    "depends_on": [],
                    "completion": ["rewritten"],
                },
            },
        )
        assert edit.status == 400, edit.body
        added = await fx.client.post(
            root + "/task-plan/nodes",
            {
                "command_id": "cmd_edit_future",
                "binding_id": view["binding"]["planning_binding_id"],
                "expected_version": view["draft"]["draft"]["row_version"],
                "action": "add",
                "node": {
                    "node_id": "followup",
                    "title": "followup",
                    "task": "Verify remaining work against the inherited result",
                    "agent": "preset:general",
                    "responsibility": "implementation",
                    "depends_on": [past[0]],
                    "completion": ["follow-up is verified"],
                },
            },
        )
        assert added.status == 200, added.body
        assert added.json()["status"] == "succeeded"
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_save_discard_and_resume_do_not_handoff(tmp_path):
    fx, sid, root, run_id, view = await _change_view(tmp_path, "work", "audit")
    try:
        saved = await fx.client.post(
            root + "/task-plan/apply-change",
            {
                "command_id": "cmd_save_only",
                "session_id": sid,
                "decision": "save_candidate",
                "action_source": "button",
                "interaction_id": "message_save",
                "candidate_digest": view["candidate"]["digest"],
                "expected_parent_row_version": view["candidate"]["expected_parent_row_version"],
            },
        )
        assert saved.status == 200, saved.body
        assert saved.json()["decision"] == "save_candidate"
        assert saved.json()["child"] is None

        def run_count():
            return fx.host.context.journal._backend.read_one(
                "SELECT count(*) FROM workflow_runs", ()
            )[0]

        assert await fx.on_core(run_count) == 1
        parent = await wait_for_run(fx.client, run_id, "paused", "draining")
        assert parent["run"]["pause_requested"] is True

        discarded = await fx.client.post(
            root + "/task-plan/apply-change",
            {
                "command_id": "cmd_discard",
                "session_id": sid,
                "decision": "reject_change",
                "action_source": "button",
                "interaction_id": "message_discard",
                "candidate_digest": view["candidate"]["digest"],
                "expected_parent_row_version": (await fx.client.get(root + "/task-plan")).json()[
                    "candidate"
                ]["expected_parent_row_version"]
                if (await fx.client.get(root + "/task-plan")).json()["candidate"]
                else view["candidate"]["expected_parent_row_version"],
            },
        )
        # Row version may have moved during drain; refresh once on stale.
        if discarded.status == 409:
            latest = (await fx.client.get(root + "/task-plan")).json()
            discarded = await fx.client.post(
                root + "/task-plan/apply-change",
                {
                    "command_id": "cmd_discard_retry",
                    "session_id": sid,
                    "decision": "reject_change",
                    "action_source": "button",
                    "interaction_id": "message_discard",
                    "candidate_digest": latest["candidate"]["digest"],
                    "expected_parent_row_version": latest["candidate"][
                        "expected_parent_row_version"
                    ],
                },
            )
        assert discarded.status == 200, discarded.body
        after = (await fx.client.get(root + "/task-plan")).json()
        assert after["binding"] is None or after["binding"]["mode"] != "change"
        resumed = await fx.client.post(
            root + "/task-plan/resume",
            {"command_id": "cmd_resume_original", "session_id": sid},
        )
        assert resumed.status == 200, resumed.body
        assert resumed.json()["run"]["pause_requested"] is False
        assert await fx.on_core(run_count) == 1
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_accept_change_creates_one_child_and_replays(tmp_path):
    fx, sid, root, run_id, view = await _change_view(tmp_path, "work", "audit")
    try:
        await wait_for_run(fx.client, run_id, "paused")
        latest = (await fx.client.get(root + "/task-plan")).json()
        assert latest["candidate"]["stable"] is True
        fx.bank.scripts.extend([["continued work"], ["continued audit"]])
        body = {
            "command_id": "cmd_accept",
            "session_id": sid,
            "decision": "accept_change",
            "action_source": "button",
            "interaction_id": "message_accept",
            "candidate_digest": latest["candidate"]["digest"],
            "expected_parent_row_version": latest["candidate"]["expected_parent_row_version"],
        }
        accepted = await fx.client.post(root + "/task-plan/apply-change", body)
        assert accepted.status == 200, accepted.body
        child = accepted.json()["child"]
        assert child is not None and child["workflow_run_id"] != run_id
        replay = await fx.client.post(root + "/task-plan/apply-change", body)
        assert replay.status == 200 and replay.json()["replayed"] is True
        assert replay.json()["child"]["workflow_run_id"] == child["workflow_run_id"]

        def facts():
            journal = fx.host.context.journal
            parent = journal.workflows.get_run(fx.workspace_id, run_id)
            child_run = journal.workflows.get_run(fx.workspace_id, child["workflow_run_id"])
            return (
                parent.status.value,
                child_run.parent_run_id,
                child_run.effective_lineage_budget_root_run_id,
                child_run.admission_deadline_at == parent.admission_deadline_at,
                journal._backend.read_one("SELECT count(*) FROM workflow_runs", ())[0],
            )

        status, parent_id, lineage, same_deadline, runs = await fx.on_core(facts)
        assert status == "superseded"
        assert parent_id == run_id
        assert lineage == view["run"]["lineage_root_run_id"]
        assert same_deadline is True
        assert runs == 2
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_succeeded_run_cannot_open_repair_plan(tmp_path):
    fx, sid, root, run_id = await _started(tmp_path, "work")
    try:
        finished = await wait_for_run(fx.client, run_id, "completed", "failed")
        assert finished["run"]["status"] == "completed"
        repair = await fx.client.post(
            root + "/task-plan/repair",
            {
                "command_id": "cmd_repair_ok",
                "session_id": sid,
                "origin_interaction_id": "message_repair",
                "action_source": "button",
            },
        )
        assert repair.status == 400, repair.body
        view = (await fx.client.get(root + "/task-plan")).json()
        assert "repair" not in view["allowed_actions"]
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_repair_plan_is_new_root_not_continuation(tmp_path):
    fx, sid, root, run_id = await _started(tmp_path, "build", "audit")
    try:
        finished = await wait_for_run(fx.client, run_id, "completed", "failed")
        assert (
            finished["run"]["status"] != "completed"
            or finished["run"]["result_status"] != "succeeded"
        )
        repaired = await fx.client.post(
            root + "/task-plan/repair",
            {
                "command_id": "cmd_repair",
                "session_id": sid,
                "origin_interaction_id": "message_repair",
                "action_source": "button",
            },
        )
        assert repaired.status == 200, repaired.body
        assert repaired.json()["binding"]["mode"] == "repair"
        assert repaired.json()["binding"]["parent_run_id"] == run_id
        view = (await fx.client.get(root + "/task-plan")).json()
        assert view["binding"]["mode"] == "repair"
        assert view["draft"] is not None
        assert view["candidate"]["past_node_ids"] == []
        assert "start" in view["allowed_actions"]
        fx.bank.scripts.extend([["repair work"], ["repair audit"]])
        started = await fx.client.post(
            root + "/task-plan/start",
            {
                "command_id": "cmd_start_repair",
                "session_id": sid,
                "draft_id": view["execution"]["draft_id"],
                "draft_version": view["execution"]["draft_version"],
                "execution_digest": view["execution"]["digest"],
                "action_source": "button",
                "interaction_id": "message_start_repair",
            },
        )
        assert started.status == 200, started.body
        child_id = started.json()["run"]["workflow_run_id"]
        assert child_id != run_id

        def lineage():
            journal = fx.host.context.journal
            parent = journal.workflows.get_run(fx.workspace_id, run_id)
            child = journal.workflows.get_run(fx.workspace_id, child_id)
            origin = journal.workflows.get_task_plan_provenance(
                fx.workspace_id, child.workflow_revision_id
            )
            return {
                "parent_status": parent.status.value,
                "child_parent": child.parent_run_id,
                "child_relation": child.run_relation,
                "child_root": child.root_task_run_id,
                "parent_root": parent.root_task_run_id,
                "origin": origin.origin if origin else None,
                "repair_of": origin.repair_of_run_id if origin else None,
                "imports": journal._backend.read_one(
                    "SELECT count(*) FROM workflow_run_artifact_imports WHERE workflow_run_id=?",
                    (child_id,),
                )[0],
            }

        facts = await fx.on_core(lineage)
        assert facts["parent_status"] in {"failed", "cancelled"}
        assert facts["child_parent"] is None
        assert facts["child_relation"] == "initial"
        assert facts["child_root"] != facts["parent_root"]
        assert facts["origin"] == "repair"
        assert facts["repair_of"] == run_id
        assert facts["imports"] == 0
    finally:
        fx.close()
