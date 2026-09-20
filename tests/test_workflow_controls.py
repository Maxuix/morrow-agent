"""State-aware chat control resolution: deterministic gates over model proposals."""

import json
from pathlib import Path

import pytest

from morrow.application.workflow_controls import (
    classify_cancel_request,
    classify_continuation_correction,
    classify_explicit_start,
    classify_pause_request,
    classify_resume_request,
)
from test_chat_execution_projection import install_stream_gate
from test_stage8_chat_submission import drain, new_session
from test_stage8_core_api import ServerFixture, publish_pipeline, wait_for_run
from test_workflow_task_planning import request, spec

_CONTROL_CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "control_text_corpus.json").read_text(encoding="utf-8")
)


def _plan_scripts(*nodes):
    return [[[json.dumps(spec(*nodes))]], [["node complete"]]]


@pytest.mark.parametrize(
    "text",
    [
        "按当前计划开始",
        "开始执行",
        "开始吧",
        "按这个方案开始运行",
        "就按它开始",
        "start",
        "run it",
        "重新开始",
    ],
)
def test_explicit_start_affirmatives(text):
    assert classify_explicit_start(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "先别开始",
        "不要开始",
        "如果完成再开始",
        "等我改完再开始",
        "方案可以吗",
        "可以吗？",
        "能开始吗",
        "他说可以开始",
        "资料显示任务开始于周一",
        "不错",
        "挺好，可以",
        "> 引用：开始执行",
        "先修复第二个节点再开始",
    ],
)
def test_negated_conditional_quoted_or_praise_never_start(text):
    assert classify_explicit_start(text) is False


@pytest.mark.parametrize("text", _CONTROL_CORPUS["pause"]["positive"])
def test_pause_corpus_positive(text):
    assert classify_pause_request(text) is True


@pytest.mark.parametrize("text", _CONTROL_CORPUS["pause"]["negative"])
def test_pause_corpus_negative(text):
    assert classify_pause_request(text) is False


def test_pause_tightening_keeps_generation_and_caller_semantics():
    # Generation-scoped wording must survive the new guards: classify() checks
    # it first, and it must never be silently consumed as a run pause either.
    assert classify_pause_request("暂停生成") is True
    assert classify_pause_request("暂停规划") is True
    # Shared-guard callers keep their existing semantics.
    assert classify_explicit_start("按当前计划开始") is True
    assert classify_explicit_start("他说可以开始") is False
    assert classify_resume_request("继续") is True
    assert classify_continuation_correction("把第二步改成只修保存功能") is True
    assert classify_cancel_request("取消") is True


async def _started_direct_run(tmp_path):
    """A published-workflow run (explicit_workflow) with no planning binding,
    parked on its first node model call until ``release``."""

    fx = ServerFixture(tmp_path)
    revision = await publish_pipeline(fx)
    sid, root = await new_session(fx)
    install, wait_parked, release = install_stream_gate(fx, expected=1)
    await fx.on_core(install)
    sent = await fx.client.post(
        root + "/interactions",
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
    execution = (await fx.client.get(root + "/snapshot")).json()["execution"]
    assert execution["owner"] == "workflow" and execution["state"] == "running"
    run_id = execution["workflow_run_id"]
    # No planning binding, yet the control projection resolves the active run.
    view = (await fx.client.get(root + "/task-plan")).json()
    assert view["binding"] is None
    assert view["control"]["state"] == "running"
    assert view["control"]["target"]["workflow_run_id"] == run_id
    return fx, sid, root, run_id, release


async def test_direct_run_non_control_text_stays_ordinary_without_model_call(tmp_path):
    fx, sid, root, run_id, release = await _started_direct_run(tmp_path)
    try:
        providers_before = len(fx.bank.providers)
        chat = await _control_call(fx, root, sid, "顺便帮我看看日志", key="cmd_ctrl_chat")
        assert chat.status == 200, chat.body
        body = chat.json()
        assert body["disposition"] == "unresolved" and body["intent"] == "none", body
        # Deterministic-only: no provider was spent on a proposal that could
        # not apply to a binding-less run.
        assert len(fx.bank.providers) == providers_before

        # A pure pause word is still resolved deterministically and executed.
        paused = await _control_call(fx, root, sid, "暂停", key="cmd_ctrl_pause")
        assert paused.status == 200, paused.body
        body = paused.json()
        assert body["disposition"] == "executed" and body["intent"] == "pause_run", body
        assert body["deterministic"] is True
        assert body["run"]["pause_requested"] is True

        # draining + no binding: ordinary text stays model-free too.
        providers_before = len(fx.bank.providers)
        draining_chat = await _control_call(fx, root, sid, "现在进度如何", key="cmd_ctrl_chat2")
        assert draining_chat.status == 200, draining_chat.body
        assert draining_chat.json()["intent"] == "none"
        assert len(fx.bank.providers) == providers_before
    finally:
        release()
        await wait_for_run(fx.client, run_id, "paused", "completed")
        await drain(fx, sid)
        fx.close()


async def _planned(tmp_path, *nodes):
    fx = ServerFixture(tmp_path, scripts=_plan_scripts(*nodes))
    sid, root = await new_session(fx)
    reply = await fx.client.post(root + "/task-plan", request(sid, command_id="cmd_plan"))
    assert reply.status == 200 and reply.json()["status"] == "succeeded", reply.body
    return fx, sid, root


def _control(text, *, key="cmd_ctrl"):
    return {"command_id": key, "session_id": "SESSION", "text": text}


async def _control_call(fx, root, sid, text, *, key="cmd_ctrl"):
    return await fx.client.post(
        root + "/task-plan/control", _control(text, key=key) | {"session_id": sid}
    )


def _intent_script(intent, answer=""):
    return [[json.dumps({"intent": intent, "answer": answer})]]


async def test_explicit_chat_start_runs_once_and_reports_unresolved_for_negatives(tmp_path):
    fx, sid, root = await _planned(tmp_path, "work")
    try:
        # The scripted model proposes start for every message; the server gate
        # decides which texts actually start. Negatives are checked first,
        # while the plan is still in the draft state.
        fx.bank.scripts = [_intent_script("start") for _ in range(6)] + [["node complete"]]
        negatives = [
            ("先别开始", "cmd_ctrl_neg1"),
            ("如果完成再开始", "cmd_ctrl_neg2"),
            ("方案可以吗", "cmd_ctrl_neg3"),
            ("不错", "cmd_ctrl_neg4"),
            ("他说开始执行", "cmd_ctrl_neg5"),
        ]
        for text, key in negatives:
            result = await _control_call(fx, root, sid, text, key=key)
            assert result.status == 200, result.body
            body = result.json()
            assert body["disposition"] == "unresolved", (text, body)
            assert "明确的开始指令" in body["message"], (text, body)

        started = await _control_call(fx, root, sid, "按当前计划开始", key="cmd_ctrl_go")
        assert started.status == 200, started.body
        assert started.json()["disposition"] == "executed", started.json()
        run_id = started.json()["run"]["workflow_run_id"]

        from test_stage8_core_api import wait_for_run

        finished = await wait_for_run(fx.client, run_id, "completed", "failed", "blocked")
        assert finished["run"]["status"] == "completed"
        await drain(fx, sid)

        # The projection distinguishes generation usage from run usage and
        # carries the acceptance outcome for the transcript notice.
        projection = (await fx.client.get(root + "/task-plan")).json()
        assert projection["run"]["agent_generation_request_count"] >= 1
        assert projection["run"]["outcome"]["summary"]
        assert projection["run"]["outcome"]["completion_basis"]
        generation_usage = projection["operations"][-1]["usage"]
        assert generation_usage["attempts"] >= 1
        # Scripted providers carry no token accounting; the wire must say so
        # instead of fabricating zeros.
        assert generation_usage["availability"] == "unavailable"
        assert generation_usage["total_tokens"] is None

        # After completion the same wording is ordinary chat territory: no
        # second run may start from a stale plan.
        after = await _control_call(fx, root, sid, "按当前计划开始", key="cmd_ctrl_after")
        assert after.status == 200, after.body
        assert after.json()["disposition"] == "unresolved", after.json()

        def counts():
            backend = fx.host.context.journal._backend
            return backend.read_one("SELECT count(*) FROM workflow_runs", ())[0]

        assert await fx.on_core(counts) == 1
    finally:
        fx.close()


async def test_control_revise_generates_without_business_runs(tmp_path):
    fx, sid, root = await _planned(tmp_path, "work")
    try:
        fx.bank.scripts = [_intent_script("revise"), [[json.dumps(spec("build", "check"))]]]
        result = await _control_call(fx, root, sid, "加一个最终检查节点", key="cmd_ctrl_revise")
        assert result.status == 200, result.body
        body = result.json()
        assert body["disposition"] == "executed" and body["intent"] == "revise", body
        assert body["operation"]["status"] == "succeeded"
        view = (await fx.client.get(root + "/task-plan")).json()
        assert len(view["draft"]["draft"]["source"]["nodes"]) == 2

        def counts():
            backend = fx.host.context.journal._backend
            return {
                "runs": backend.read_one("SELECT count(*) FROM workflow_runs", ())[0],
                "turns": backend.read_one("SELECT count(*) FROM agent_runs", ())[0],
            }

        facts = await fx.on_core(counts)
        assert facts["runs"] == 0
    finally:
        fx.close()


async def test_unresolved_or_failed_classification_never_runs_business(tmp_path):
    fx, sid, root = await _planned(tmp_path, "work")
    try:
        # No scripted chunk parses as a control proposal: classification fails
        # and the endpoint must NOT fall back to ordinary agent execution.
        fx.bank.scripts = [["unparsable control text"]]
        result = await _control_call(fx, root, sid, "帮我看看", key="cmd_ctrl_fail")
        assert result.status == 200, result.body
        body = result.json()
        assert body["disposition"] == "unresolved", body
        assert body["intent"] == "none"

        def counts():
            backend = fx.host.context.journal._backend
            return backend.read_one("SELECT count(*) FROM agent_runs", ())[0]

        assert await fx.on_core(counts) == 0
    finally:
        fx.close()


async def test_start_api_rejects_missing_user_action_source(tmp_path):
    """A bypassing client cannot start a task plan without a real action source."""
    fx, sid, root = await _planned(tmp_path, "work")
    try:
        view = (await fx.client.get(root + "/task-plan")).json()
        execution = view["execution"]
        assert execution["allowed"] is True
        body = {
            "command_id": "cmd_start_bypass",
            "session_id": sid,
            "draft_id": execution["draft_id"],
            "draft_version": execution["draft_version"],
            "execution_digest": execution["digest"],
            "action_source": "button",
            "interaction_id": "",
        }
        assert (await fx.client.post(root + "/task-plan/start", body)).status == 400
        no_source = {key: value for key, value in body.items() if key != "action_source"}
        no_source["interaction_id"] = "message1"
        assert (await fx.client.post(root + "/task-plan/start", no_source)).status in {400, 422}

        def counts():
            backend = fx.host.context.journal._backend
            return backend.read_one("SELECT count(*) FROM workflow_runs", ())[0]

        assert await fx.on_core(counts) == 0
    finally:
        fx.close()
