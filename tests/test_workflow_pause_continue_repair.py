"""Pause/continue/cancel repair: control contract and end-to-end repro harness.

Plan: docs/reviews/workflow-pause-continue-repair-plan-2026-09-09.md (steps A/B).
Every Provider is scripted; model-call synchronization uses a durable gate
(Core-loop ``asyncio.Event``) instead of wall-clock sleeps.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from test_stage8_chat_submission import drain, new_session
from test_stage8_core_api import ServerFixture
from test_workflow_task_planning import request, spec

# A node whose scripted reply is this sentinel parks until the test releases it.
GATE_SENTINEL = "GATED_NODE_REPLY"


class ModelGate:
    """Deterministic model-call gate shared between the test and the Core loop."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self._release: asyncio.Event | None = None

    def bind(self) -> None:
        """Create the release event on the Core loop (inside a Core callback)."""

        if self._release is None:
            self._release = asyncio.Event()

    async def wait(self) -> None:
        # The event is created on the Core loop at first use: the node's model
        # request may already be in flight before the test can bind the gate.
        self.bind()
        self.entered.set()
        await self._release.wait()

    def release(self) -> None:
        assert self._release is not None
        self._release.set()


class RepairFixture:
    """A three-node plan whose first node blocks until the gate is released."""

    def __init__(self, tmp_path, nodes=("one", "two", "three")) -> None:
        self.nodes = tuple(nodes)
        self.gate = ModelGate()
        scripts = [[[json.dumps(spec(*self.nodes))]]]
        scripts.append([[GATE_SENTINEL]])
        scripts.extend([[f"{name} done"]] for name in self.nodes[1:])
        self.holder: dict = {}
        self.fx = ServerFixture(tmp_path, scripts=scripts, on_create=self._on_create)
        # ServerFixture stores carry workflow_node_segments/workflow_pause_points
        # natively; no manual schema construction is needed here.
        self.holder["fx"] = self.fx

    def _on_create(self, _count: int) -> None:
        fixture = self.holder.get("fx")
        if fixture is None:
            return
        provider = fixture.bank.providers[-1]
        if provider.responses != [[GATE_SENTINEL]]:
            return
        original = provider.stream

        async def stream(*args, **kwargs):
            await self.gate.wait()
            async for event in original(*args, **kwargs):
                yield event

        provider.stream = stream

    def close(self) -> None:
        self.fx.close()

    async def bind_gate(self) -> None:
        await self.fx.on_core(self.gate.bind)

    async def release_gate(self) -> None:
        await self.fx.on_core(self.gate.release)

    async def wait_entered(self) -> None:
        for _ in range(4000):
            if self.gate.entered.is_set():
                return
            await asyncio.sleep(0)
        raise AssertionError("the gated node never reached its model request")

    async def plan(self):
        sid, root = await new_session(self.fx)
        reply = await self.fx.client.post(root + "/task-plan", request(sid, command_id="cmd_plan"))
        assert reply.status == 200 and reply.json()["status"] == "succeeded", reply.body
        return sid, root

    async def view(self, root):
        response = await self.fx.client.get(root + "/task-plan")
        assert response.status == 200, response.body
        return response.json()

    async def start(self, sid, root, *, key="cmd_start"):
        execution = (await self.view(root))["execution"]
        response = await self.fx.client.post(
            root + "/task-plan/start",
            {
                "command_id": key,
                "session_id": sid,
                "draft_id": execution["draft_id"],
                "draft_version": execution["draft_version"],
                "execution_digest": execution["digest"],
                "action_source": "button",
                "interaction_id": "message1",
            },
        )
        assert response.status == 200, response.body
        return response.json()["run"]["workflow_run_id"]

    async def pause(self, sid, root, *, key="cmd_pause", expected=None):
        body = {"command_id": key, "session_id": sid}
        if expected is not None:
            body["expected_run_row_version"] = expected
        return await self.fx.client.post(root + "/task-plan/pause", body)

    async def control(self, sid, root, text, *, key="cmd_ctrl", client_message_id=None):
        body = {"command_id": key, "session_id": sid, "text": text}
        if client_message_id is not None:
            body["client_message_id"] = client_message_id
        return await self.fx.client.post(root + "/task-plan/control", body)

    async def receipts(self, root, *, after=0, limit=50):
        response = await self.fx.client.get(
            root + f"/task-plan/control-receipts?after={after}&limit={limit}"
        )
        assert response.status == 200, response.body
        return response.json()["items"]

    async def cancel(self, run_id, *, key="cmd_cancel"):
        return await self.fx.client.post(f"/v1/workflow-runs/{run_id}/cancel", {"command_id": key})

    async def wait_status(self, root, *statuses):
        for _ in range(600):
            view = await self.view(root)
            run = view["run"]
            if run is not None and run["status"] in statuses:
                return view
            await asyncio.sleep(0)
        raise AssertionError(f"run never reached {statuses}")

    async def counts(self):
        def read():
            backend = self.fx.host.context.journal._backend
            return {
                "runs": backend.read_one("SELECT count(*) FROM workflow_runs", ())[0],
                "agent_runs": backend.read_one("SELECT count(*) FROM agent_runs", ())[0],
            }

        return await self.fx.on_core(read)


@pytest.fixture
def repair(tmp_path):
    fixture = RepairFixture(tmp_path)
    yield fixture
    fixture.close()


async def test_control_projection_exposes_state_and_allowed_intents(repair):
    sid, root = await repair.plan()
    draft = await repair.view(root)
    assert draft["control"]["state"] == "draft"
    assert draft["control"]["start_available"] is True
    assert "start" in draft["control"]["allowed_intents"]
    assert draft["control"]["resume_available"] is False

    run_id = await repair.start(sid, root)
    await repair.bind_gate()
    running = await repair.wait_status(root, "running")
    assert running["control"]["state"] == "running"
    assert running["control"]["target"]["workflow_run_id"] == run_id
    assert running["control"]["target"]["run_row_version"] == running["run"]["row_version"]
    assert set(running["control"]["allowed_intents"]) >= {"pause_run", "cancel_run", "steer"}
    assert "resume_run" not in running["control"]["allowed_intents"]

    paused = await repair.pause(sid, root)
    assert paused.status == 200, paused.body
    draining = await repair.view(root)
    assert draining["control"]["state"] == "draining"
    assert draining["control"]["resume_available"] is True
    assert "resume_run" in draining["control"]["allowed_intents"]


async def test_recovery_status_projects_workflow_owner_after_pause(repair):
    sid, root = await repair.plan()
    run_id = await repair.start(sid, root)
    await repair.bind_gate()
    await repair.wait_entered()

    assert (await repair.pause(sid, root)).status == 200
    await repair.release_gate()
    await repair.wait_status(root, "paused")

    response = await repair.fx.client.get(root + "/recovery/status")
    assert response.status == 200, response.body
    status = response.json()
    assert status["display_state"] == "paused"
    assert status["owner"] == "workflow"
    assert status["opaque_target_kind"] == "workflow_run"
    assert status["opaque_target"] == run_id
    assert status["allowed_actions"] == ["continue", "new_session"]
    assert status["decision_required"] is False

    replay = await repair.fx.client.get(root + "/recovery/status")
    assert replay.json()["revision"] == status["revision"]

    _other_sid, other_root = await new_session(repair.fx, "cmd_other_recovery")
    outside = await repair.fx.client.post(
        other_root + f"/workflow-recovery/{run_id}",
        {"command_id": "cmd_outside_recovery", "resolution": "acknowledge"},
    )
    assert outside.status == 404, outside.body


async def test_true_pause_then_continue_resumes_the_same_run(repair):
    """running → real pause interrupts the node's turn → continue resumes in place.

    Updated for P03/P04 (D02/D04): the user pause interrupts the gated model
    request inside the node; the node parks at its suspended segment and the
    continue word resumes the same run, node, Session and TaskRun with a new
    execution segment — the run never restarts and nothing is cancelled.
    """

    sid, root = await repair.plan()
    run_id = await repair.start(sid, root)
    await repair.bind_gate()
    await repair.wait_entered()

    paused = await repair.pause(sid, root, expected=None)
    assert paused.status == 200, paused.body
    assert paused.json()["run"]["pause_requested"] is True

    # The gated model request is interrupted before its first token; the run
    # settles to paused with the node still open at its suspended segment.
    await repair.release_gate()
    settled = await repair.wait_status(root, "paused")
    assert settled["run"]["workflow_run_id"] == run_id
    assert [node["status"] for node in settled["run"]["nodes"]][0] == "running"

    continued = await repair.control(sid, root, "继续", key="cmd_continue")
    assert continued.status == 200, continued.body
    body = continued.json()
    assert body["disposition"] == "executed", body
    assert body["intent"] == "resume_run", body
    assert body["run"]["workflow_run_id"] == run_id, body
    assert body["run"]["status"] == "running", body
    assert body["deterministic"] is True, body

    finished = await repair.wait_status(root, "completed")
    assert finished["run"]["workflow_run_id"] == run_id
    assert [node["status"] for node in finished["run"]["nodes"]] == [
        "completed",
        "completed",
        "completed",
    ]
    await drain(repair.fx, sid)


async def test_continue_while_running_is_an_acknowledgement_not_a_second_run(repair):
    sid, root = await repair.plan()
    run_id = await repair.start(sid, root)
    await repair.bind_gate()
    await repair.wait_status(root, "running")

    reply = await repair.control(sid, root, "continue", key="cmd_continue_running")
    assert reply.status == 200, reply.body
    body = reply.json()
    assert body["disposition"] == "acknowledged", body
    assert body["intent"] == "resume_run", body
    assert body["run"]["workflow_run_id"] == run_id

    def run_count():
        return repair.fx.host.context.journal._backend.read_one(
            "SELECT count(*) FROM workflow_runs", ()
        )[0]

    assert await repair.fx.on_core(run_count) == 1
    await repair.release_gate()
    await repair.wait_status(root, "completed")
    await drain(repair.fx, sid)


@pytest.mark.parametrize(
    "text",
    ["不要继续", "先别继续", "继续吗？", "文档里写 continue", "改完再继续", "继续但修改第二步"],
)
async def test_negated_quoted_conditional_or_change_continue_never_resumes(repair, text):
    """The deterministic gate must not resume on ambiguous wording (D03)."""

    sid, root = await repair.plan()
    await repair.start(sid, root)
    await repair.bind_gate()
    await repair.wait_entered()
    assert (await repair.pause(sid, root)).status == 200
    await repair.release_gate()
    await repair.wait_status(root, "paused")

    # No scripted classification reply is left for these texts: a deterministic
    # resume would be the only way they could act, so an unresolved/needs-choice
    # answer proves the gate held.
    reply = await repair.control(sid, root, text, key=f"cmd_ctrl_{abs(hash(text))}")
    assert reply.status == 200, reply.body
    body = reply.json()
    assert body["intent"] != "resume_run" or body["disposition"] != "executed", body
    assert (await repair.view(root))["run"]["status"] == "paused"


async def test_user_cancel_closes_the_leaf_turn_task_run_and_metrics(repair):
    """模型等待中取消：叶子 turn/TaskRun/metrics、NodeRun、root run 终态一致。"""

    sid, root = await repair.plan()
    run_id = await repair.start(sid, root)
    await repair.bind_gate()
    await repair.wait_entered()

    assert (await repair.cancel(run_id)).status == 200
    await repair.release_gate()
    await repair.wait_status(root, "cancelled")

    def facts():
        journal = repair.fx.host.context.journal
        workspace = repair.fx.workspace_id
        run = journal.workflows.get_run(workspace, run_id)
        root_task = journal.get_task_run(workspace, run.root_task_run_id)
        nodes = []
        for node in journal.workflows.list_nodes(workspace, run_id):
            leaf = (
                journal.get_task_run(workspace, node.leaf_task_run_id)
                if node.leaf_task_run_id
                else None
            )
            nodes.append(
                {
                    "node": node.status.value,
                    "leaf": leaf.status.value if leaf is not None else None,
                    "open_turn": (
                        journal.has_open_turn_submission(workspace, node.conversation_session_id)
                        if node.conversation_session_id
                        else None
                    ),
                    "metrics": (
                        journal.get_agent_run_terminal_metrics(workspace, node.agent_run_id)
                        if node.agent_run_id
                        else None
                    ),
                }
            )
        return {"run": run.status.value, "root": root_task.status.value, "nodes": nodes}

    data = await repair.fx.on_core(facts)
    assert data["run"] == "cancelled", data
    assert data["root"] == "cancelled", data
    assert all(item["node"] == "cancelled" for item in data["nodes"]), data
    admitted = [item for item in data["nodes"] if item["leaf"] is not None]
    assert admitted, data
    assert all(item["leaf"] == "cancelled" for item in admitted), data
    assert all(item["open_turn"] is False for item in admitted), data
    assert all(item["metrics"] is not None for item in admitted), data


async def test_cancelled_run_continue_explains_and_never_runs_business(repair):
    """主输入区取消 → continue：旧 run 保持 cancelled，明确说明不能原地恢复。"""

    sid, root = await repair.plan()
    run_id = await repair.start(sid, root)
    await repair.bind_gate()
    await repair.wait_entered()
    cancelled = await repair.cancel(run_id)
    assert cancelled.status == 200, cancelled.body
    await repair.release_gate()
    terminal = await repair.wait_status(root, "cancelled")
    assert terminal["control"]["state"] == "terminal"
    assert terminal["control"]["repair_available"] is True
    before = await repair.counts()

    reply = await repair.control(sid, root, "继续", key="cmd_continue_after_cancel")
    assert reply.status == 200, reply.body
    body = reply.json()
    assert body["disposition"] == "unresolved", body
    assert body["next_action"] == "repair", body
    assert "无法原地恢复" in body["message"], body
    assert (await repair.view(root))["run"]["status"] == "cancelled"
    after = await repair.counts()
    assert after["runs"] == before["runs"], after
    assert after["agent_runs"] == before["agent_runs"], after


async def test_cancelled_repair_draft_ready_starts_a_new_run_from_text(repair):
    """cancelled → repair ready → 文字"开始执行"启动新的 run，不再返回同一 repair。"""

    sid, root = await repair.plan()
    run_id = await repair.start(sid, root)
    await repair.bind_gate()
    await repair.wait_entered()
    assert (await repair.cancel(run_id)).status == 200
    await repair.release_gate()
    await repair.wait_status(root, "cancelled")

    repair_call = await repair.control(sid, root, "生成修复计划", key="cmd_repair")
    assert repair_call.status == 200, repair_call.body
    assert repair_call.json()["intent"] == "repair", repair_call.json()
    ready = await repair.view(root)
    assert ready["control"]["state"] == "repair_ready", ready["control"]
    assert ready["control"]["start_available"] is True
    assert ready["binding"]["mode"] == "repair"
    binding_id = ready["binding"]["planning_binding_id"]

    # "继续" must not silently start or regenerate; it asks for the explicit start.
    asking = await repair.control(sid, root, "继续", key="cmd_continue_repair")
    assert asking.json()["disposition"] == "needs_choice", asking.json()
    assert (await repair.view(root))["binding"]["planning_binding_id"] == binding_id

    started = await repair.control(sid, root, "开始执行", key="cmd_start_repair")
    assert started.status == 200, started.body
    body = started.json()
    assert body["disposition"] == "executed" and body["intent"] == "start", body
    new_run = body["run"]["workflow_run_id"]
    assert new_run != run_id
    finished = await repair.wait_status(root, "completed", "failed")
    assert finished["run"]["workflow_run_id"] == new_run
    assert finished["run"]["status"] == "completed", finished["run"]
    # The consumed repair draft can never advertise itself as startable again:
    # the binding is closed, so the projection reports a terminal run.
    assert finished["control"]["state"] == "terminal", finished["control"]
    assert finished["control"]["start_available"] is False, finished["control"]
    await drain(repair.fx, sid)


async def test_continue_without_any_plan_is_ordinary_chat(repair):
    """无待恢复任务 + 继续：可见地说明，并显式指向普通发送（S2.6）。"""

    sid, root = await new_session(repair.fx)
    reply = await repair.control(sid, root, "继续", key="cmd_none")
    assert reply.status == 200, reply.body
    body = reply.json()
    assert body["state"] == "none", body
    assert body["disposition"] == "unresolved", body
    assert body["next_action"] == "ordinary_send", body
    assert "没有可恢复的运行" in body["message"], body
    assert (await repair.counts())["runs"] == 0


async def test_control_input_is_durably_accepted_and_replayed(repair):
    """输入已落库但 HTTP 响应丢失：重试对账只出现一条消息、一个效果。"""

    sid, root = await repair.plan()
    await repair.start(sid, root)
    await repair.bind_gate()
    await repair.wait_status(root, "running")

    first = await repair.control(
        sid, root, "continue", key="cmd_receipt", client_message_id="client.control.1"
    )
    assert first.status == 200, first.body
    receipt = first.json()["receipt"]
    assert receipt["text"] == "continue"
    assert receipt["status"] == "acknowledged"
    assert receipt["client_message_id"] == "client.control.1"
    assert receipt["revision"] == 2

    # The durable projection is readable without the original response.
    listed = await repair.receipts(root)
    assert [item["command_id"] for item in listed] == ["cmd_receipt"]
    assert listed[0]["outcome"]["disposition"] == "acknowledged"

    # A retry with the same identity replays the stored receipt and never
    # re-runs the command.
    retried = await repair.control(
        sid, root, "continue", key="cmd_receipt", client_message_id="client.control.1"
    )
    assert retried.status == 200, retried.body
    assert retried.json()["replayed"] is True
    assert retried.json()["disposition"] == "acknowledged"
    assert len(await repair.receipts(root)) == 1
    assert (await repair.counts())["runs"] == 1

    # The same ID with different text is a conflict, not a silent second effect.
    conflict = await repair.control(sid, root, "暂停", key="cmd_receipt")
    assert conflict.status == 409, conflict.body

    await repair.release_gate()
    await repair.wait_status(root, "completed")
    await drain(repair.fx, sid)


async def activity_snapshot(fixture, root):
    response = await fixture.client.get(root + "/snapshot?activity_schema=1")
    assert response.status == 200, response.body
    return response.json()


async def test_planning_attempts_are_distinct_and_never_left_running(tmp_path):
    """规划三次 attempt：三个不冲突 identity，owner 结束后无 active 幽灵项。"""

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
        operation_id = reply.json()["planning_operation_id"]

        snapshot = await activity_snapshot(fx, root)
        models = [
            item
            for item in snapshot["activities"]
            if (item.get("identity") or {}).get("planning_operation_id") == operation_id
        ]
        assert [item["activity_id"] for item in models] == [
            f"act_model_plop_{operation_id}_1",
            f"act_model_plop_{operation_id}_2",
            f"act_model_plop_{operation_id}_3",
        ], models
        assert all(item["ended_at"] is not None for item in models), models
        assert all(item["state"] in {"succeeded", "failed", "cancelled"} for item in models), models
        assert [item["revision"] for item in models] == [2, 2, 2], models
    finally:
        fx.close()


async def test_cancel_leaves_no_running_activity(repair):
    """取消后同一 session 不再有活动显示为运行中，已完成工具保持原结果。"""

    sid, root = await repair.plan()
    run_id = await repair.start(sid, root)
    await repair.bind_gate()
    await repair.wait_entered()
    assert (await repair.cancel(run_id)).status == 200
    await repair.release_gate()
    await repair.wait_status(root, "cancelled")
    await drain(repair.fx, sid)

    snapshot = await activity_snapshot(repair.fx, root)
    items = snapshot["activities"]
    assert items, snapshot
    assert all(item["ended_at"] is not None for item in items), items
    assert all(item["state"] not in {"running", "preparing", "waiting"} for item in items), items


async def test_activity_snapshot_reconciles_terminal_owner(repair):
    """旧内存 activity 漏过实时结束事件时，snapshot 仍按所属运行终态收束（S3.6）。"""

    sid, root = await repair.plan()
    run_id = await repair.start(sid, root)
    await repair.bind_gate()
    await repair.wait_entered()
    assert (await repair.cancel(run_id)).status == 200
    await repair.release_gate()
    await repair.wait_status(root, "cancelled")

    def seed():
        streams = repair.fx.host.context.chat.streams
        streams.activity_upsert(
            sid,
            {
                "schema_version": 1,
                "activity_id": "act_model_orphan_1",
                "revision": 1,
                "kind": "model",
                "state": "running",
                "origin": "agent_loop",
                "identity": {
                    "workspace_id": repair.fx.workspace_id,
                    "root_session_id": sid,
                    "source_session_id": sid,
                    "workflow_run_id": run_id,
                },
                "payload": {"kind": "model", "stage": "awaiting_model"},
                "started_at": "2026-09-09T00:00:00+00:00",
                "updated_at": "2026-09-09T00:00:00+00:00",
                "ended_at": None,
                "last_activity_at": None,
                "safe_title": "等待模型响应",
                "safe_summary": None,
                "preview_ref": None,
                "truncated": False,
                "availability": "none",
            },
        )

    await repair.fx.on_core(seed)
    snapshot = await activity_snapshot(repair.fx, root)
    orphan = next(
        item for item in snapshot["activities"] if item["activity_id"] == "act_model_orphan_1"
    )
    assert orphan["state"] == "cancelled", orphan
    assert orphan["ended_at"] is not None, orphan
    assert orphan["revision"] == 2, orphan
