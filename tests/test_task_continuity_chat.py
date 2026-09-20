"""Real Core journey: a closed model failure continues the same business task."""

import json

import pytest

from morrow.core.models import (
    AssistantMessage,
    FunctionToolCall,
    ModelErrorCode,
    ModelEvent,
    ModelFailure,
    ModelFailureOrigin,
    ModelFinishReason,
)
from test_stage8_chat_submission import drain, new_session
from test_stage8_core_api import ServerFixture


def failing_chat(tmp_path, requests=None):
    requests = [] if requests is None else requests

    def hook(_):
        provider = fixture.bank.providers[-1]

        async def stream(model, messages, tools=(), **kwargs):
            requests.append(messages)
            if len(requests) == 1:
                yield ModelEvent(
                    kind="completed",
                    finish_reason=ModelFinishReason.TOOL_CALLS,
                    message=AssistantMessage(
                        tool_calls=(
                            FunctionToolCall(
                                id="call_once",
                                name="read",
                                arguments=json.dumps({"path": "notes.txt"}),
                            ),
                        )
                    ),
                )
            elif len(requests) == 2:
                yield ModelEvent(
                    kind="error",
                    failure=ModelFailure(
                        code=ModelErrorCode.INTERNAL,
                        origin=ModelFailureOrigin.ADAPTER,
                        retryable=False,
                        message="模型连接中断",
                    ),
                )
            else:
                yield ModelEvent(
                    kind="completed",
                    finish_reason=ModelFinishReason.STOP,
                    message=AssistantMessage(content="continued successfully"),
                )

        provider.stream = stream

    fixture = ServerFixture(tmp_path)
    fixture.bank.on_create = hook
    return fixture, requests


@pytest.mark.parametrize("restart", [True, False])
@pytest.mark.parametrize("button", [True, False])
async def test_model_failure_keeps_task_and_continues_once(tmp_path, button, restart):
    fx, requests = failing_chat(tmp_path)
    try:
        sid, path = await new_session(fx)
        sent = await fx.client.post(
            path + "/interactions",
            {
                "client_message_id": "first",
                "text": "read notes and finish the task",
            },
        )
        assert sent.status == 202, sent.body
        await drain(fx, sid)

        def before():
            j = fx.host.context.journal
            task = j.get_session(fx.workspace_id, sid).current_task_run_id
            point = j.workflows.execution_pause.latest_pause_point(
                fx.workspace_id,
                owner="chat_turn",
                owner_id=sid,
            )
            assert point is not None
            assert point.fact.reason == "provider_failure"
            assert point.fact.lifecycle == "suspended"
            assert j.get_task_run(fx.workspace_id, task).status.value == "open"
            run = j.get_agent_run(fx.workspace_id, point.safety.interrupted_agent_run_id)
            terminal = j.get_agent_run_terminal_metrics(
                fx.workspace_id, point.safety.interrupted_agent_run_id
            )
            assert run is not None
            assert terminal is not None
            assert terminal.finish_reason.value == "interrupted"
            assert terminal.stop_code.value == "internal"
            return task

        task = await fx.on_core(before)
        if restart:
            fx.close()
            fx, _ = failing_chat(tmp_path, requests)
            assert len(requests) == 2
        if button:
            queue = (await fx.client.get(path + "/queue")).json()
            body = {
                "command_id": "cmd_continue",
                "action": "continue_queue",
                "expected_revision": queue["revision"],
            }
            response = await fx.client.post(path + "/control", body)
        else:
            response = await fx.client.post(
                path + "/interactions",
                {
                    "client_message_id": "continue_text",
                    "text": "继续，说明结果。",
                },
            )
        assert response.status in (200, 202), response.body
        await drain(fx, sid)

        def after():
            j = fx.host.context.journal
            assert j.get_session(fx.workspace_id, sid).current_task_run_id == task
            assert j.get_task_run(fx.workspace_id, task).status.value == "ready_for_acceptance"
            turns = j.list_session_turns(fx.workspace_id, sid)
            assert len(turns) == 2
            rows = j._backend.read_all(
                "SELECT resume_of_agent_run_id FROM agent_runs WHERE session_id=?", (sid,)
            )
            assert sum(row[0] is not None for row in rows) == 1
            assert (
                j._backend.read_one(
                    "SELECT count(*) FROM tool_executions WHERE session_id=?", (sid,)
                )[0]
                == 1
            )

        await fx.on_core(after)
        assert len(requests) == 3
        assert sum(message.role == "tool" for message in requests[-1]) == 1
        assert any(getattr(message, "tool_calls", ()) for message in requests[-1])
        if button:
            replay = await fx.client.post(path + "/control", body)
            assert replay.status == 200, replay.body
            await drain(fx, sid)
            assert len(requests) == 3
    finally:
        fx.close()
