"""Repeat interruptions of the same workflow node stay continuable.

Regression for the 1.2 repair: the auto-interrupt pause cycle used
node-admission-constant command ids, so a second interruption of the same node
found the first (already resumed) pause point, the suspend rejected its
lifecycle, and the run settled as failed with the segment left active. The
tests below pin the repaired behavior: every interruption opens its own pause
cycle, the run parks paused each time, and a cold-start continuation finishes
the same WorkflowRun/NodeRun/TaskRun/Session. They also pin the segment
terminal settle on node completion (no active leftovers) and the
model-output-limit interrupt exit.
"""

from __future__ import annotations

import asyncio
import json

from fixtures.cross_process import BackendWorker, require_clean_exit
from morrow.core.execution_pause import LocalTurnPauseControl
from morrow.core.models import (
    ModelErrorCode,
    ModelEvent,
    ModelFailure,
    ModelFailureOrigin,
    ModelFinishReason,
    ModelRef,
)
from morrow.core.workflows.definitions import WorkflowBudget
from morrow.runtime.agent import AgentLoop
from morrow.runtime.session import Session
from morrow.testing import make_context_builder
from test_stage7_serial_scheduler import pair_source, reader_agent
from test_stage8_core_api import (
    ServerFixture,
    create_session_and_task,
    publish_pipeline,
    start_run,
)
from test_workflow_interrupt_continuation import READ_CALL_MSG


def _net(retryable: bool) -> ModelEvent:
    return ModelEvent(
        kind="error",
        failure=ModelFailure(
            code=ModelErrorCode.NETWORK,
            origin=ModelFailureOrigin.PROVIDER,
            retryable=retryable,
            message="scripted upstream network down",
        ),
    )


#: One scripted provider per agent-run preparation: gamma drive #1 commits a
#: read tool call then hits a non-retryable provider failure (fast interrupt);
#: gamma drive #2 (after the first resume) fails again on the same node; the
#: cold-start worker's gamma drive #3 and the alpha drive finish the run.
GAMMA_DRIVE1 = [READ_CALL_MSG, _net(retryable=False)]
GAMMA_DRIVE2 = [_net(retryable=False)]
GAMMA_DRIVE3 = [["gamma done"]]
ALPHA_SCRIPT = [["alpha done"]]

BUDGET = WorkflowBudget(
    max_agent_generation_requests=40,
    default_node_max_agent_generation_requests=15,
    admission_timeout_seconds=300,
    max_concurrency=1,
)

#: A lineage budget that the three scripted gamma requests (tool call + two
#: interrupted failures) exhaust on the fourth, so the fourth continuation
#: admission must be rejected instead of silently resetting the counter.
BUDGET_TIGHT = WorkflowBudget(
    max_agent_generation_requests=4,
    default_node_max_agent_generation_requests=8,
    admission_timeout_seconds=300,
    max_concurrency=1,
)

RESUME_SNIPPET = """
import asyncio
import json


async def go():
    resumed = await client.post(
        f"/v1/workflow-runs/{settings['run_id']}/resume",
        {"command_id": "cmd_resume_cold"},
    )
    assert resumed.status == 200, resumed.body
    for _ in range(60000):
        await asyncio.sleep(0)
        resp = await client.get(f"/v1/workflow-runs/{settings['run_id']}")
        assert resp.status == 200, resp.body
        view = resp.json()["view"]
        if view["run"]["status"] in {"completed", "failed"}:
            break
    provider_requests = []
    for provider in bank.providers:
        if provider.stream_calls:
            provider_requests.append(
                [
                    {"role": str(getattr(m, "role", None)),
                     "preview": str(getattr(m, "content", "") or "")[:60]}
                    for m in provider.stream_calls[-1]
                ]
            )
    with open(settings["result_file"], "w", encoding="utf-8") as handle:
        json.dump(
            {
                "final_status": view["run"]["status"],
                "continuation_request_messages": provider_requests,
            },
            handle,
        )


asyncio.run(go())
"""


def _truncated() -> ModelEvent:
    """A failure event the adapter tags with a length finish reason."""
    return ModelEvent(
        kind="error",
        failure=ModelFailure(
            code=ModelErrorCode.INVALID_RESPONSE,
            origin=ModelFailureOrigin.PROVIDER,
            retryable=False,
            message="response truncated by max_output_tokens",
        ),
        finish_reason=ModelFinishReason.LENGTH,
    )


async def _wait_status(fx, run_id, *statuses, limit=40000):
    view = None
    for _ in range(limit):
        await asyncio.sleep(0)
        response = await fx.client.get(f"/v1/workflow-runs/{run_id}")
        assert response.status == 200, response.body
        view = response.json()["view"]
        if view["run"]["status"] in statuses:
            return view
    raise AssertionError(f"run {run_id} never reached {statuses}")


async def _start(fx, budget=BUDGET):
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


async def _request_count(fx, run_id):
    def query():
        return fx.host.context.journal.count_workflow_agent_requests(fx.workspace_id, run_id)

    return await fx.on_core(query)


async def test_second_interruption_parks_again_and_cold_start_completes(tmp_path):
    fx = ServerFixture(tmp_path, scripts=[GAMMA_DRIVE1, GAMMA_DRIVE2, ALPHA_SCRIPT])
    run_id = await _start(fx)
    try:
        await _wait_status(fx, run_id, "paused")
        assert await _segment_statuses(fx, run_id) == ["interrupted"]
        assert await _tool_count(fx) == 1

        resumed = await fx.client.post(
            f"/v1/workflow-runs/{run_id}/resume", {"command_id": "cmd_resume_r1"}
        )
        assert resumed.status == 200, resumed.body

        # The second interruption of the SAME node admission must open its own
        # pause cycle and park the run again instead of failing it.
        await _wait_status(fx, run_id, "paused")
        assert await _segment_statuses(fx, run_id) == ["interrupted", "interrupted"]
        assert await _tool_count(fx) == 1, "the committed tool call must not replay"
    finally:
        fx.close()

    worker = BackendWorker(
        tmp_path / "state-root",
        tmp_path / "workspace",
        RESUME_SNIPPET.replace("settings['run_id']", repr(run_id)),
        exit_mode="clean",
        result_file=tmp_path / "worker-result.json",
        scripts=[GAMMA_DRIVE3, ALPHA_SCRIPT],
    )
    require_clean_exit(worker.wait_clean())
    result = json.loads((tmp_path / "worker-result.json").read_text(encoding="utf-8"))
    assert result["final_status"] == "completed", result

    reopened = ServerFixture(tmp_path)
    try:
        view = await reopened.client.get(f"/v1/workflow-runs/{run_id}")
        assert view.status == 200, view.body
        body = view.json()["view"]
        assert body["run"]["status"] == "completed"
        assert [node["node"]["status"] for node in body["nodes"]] == [
            "completed",
            "completed",
        ]
        # Every ending segment settles to a terminal state: two interrupted
        # boundaries then one completed segment per finished node.
        assert await _segment_statuses(reopened, run_id) == [
            "interrupted",
            "interrupted",
            "completed",
            "completed",
        ]
        assert await _tool_count(reopened) == 1

        # The cold-start continuation request must replay the committed
        # boundary: original goal, assistant tool call, committed tool result
        # and the continuation input — never a partial response.
        continuation_messages = result["continuation_request_messages"][0]
        roles = [m["role"] for m in continuation_messages]
        assert roles.count("tool") == 1, continuation_messages
        assert any("继续" in m["preview"] for m in continuation_messages), continuation_messages
        assert any("Phase one survey work" in m["preview"] for m in continuation_messages), (
            continuation_messages
        )
    finally:
        reopened.close()


async def test_lineage_budget_accumulates_across_continuations(tmp_path):
    """A continuation neither resets the request ledger nor reopens the cap."""

    fx = ServerFixture(
        tmp_path,
        scripts=[GAMMA_DRIVE1, GAMMA_DRIVE2, GAMMA_DRIVE2, ALPHA_SCRIPT],
    )
    run_id = await _start(fx, budget=BUDGET_TIGHT)
    try:
        # Drive #1: tool call + failure = 2 requests, then park.
        await _wait_status(fx, run_id, "paused")
        assert await _request_count(fx, run_id) == 2

        resumed = await fx.client.post(
            f"/v1/workflow-runs/{run_id}/resume", {"command_id": "cmd_budget_r1"}
        )
        assert resumed.status == 200, resumed.body
        # Drive #2: one more request (total 3), park again.
        await _wait_status(fx, run_id, "paused")
        assert await _request_count(fx, run_id) == 3

        resumed = await fx.client.post(
            f"/v1/workflow-runs/{run_id}/resume", {"command_id": "cmd_budget_r2"}
        )
        assert resumed.status == 200, resumed.body
        # Drive #3 spends the fourth request and parks a third time.
        await _wait_status(fx, run_id, "paused")
        assert await _request_count(fx, run_id) == 4

        resumed = await fx.client.post(
            f"/v1/workflow-runs/{run_id}/resume", {"command_id": "cmd_budget_r3"}
        )
        assert resumed.status == 200, resumed.body
        # The next admission must hit the accumulated lineage budget instead
        # of silently starting from zero; the run settles as failed.
        await _wait_status(fx, run_id, "failed")
        assert await _request_count(fx, run_id) == 4, "no request beyond the cap"
        # The interrupted boundaries keep their terminal evidence.
        assert await _segment_statuses(fx, run_id) == [
            "interrupted",
            "interrupted",
            "interrupted",
        ]
    finally:
        fx.close()


async def test_plain_completion_leaves_no_active_segments(tmp_path):
    fx = ServerFixture(tmp_path, scripts=[["gamma straight done"], ALPHA_SCRIPT])
    try:
        run_id = await _start(fx)
        await _wait_status(fx, run_id, "completed")
        assert await _segment_statuses(fx, run_id) == ["completed", "completed"]
    finally:
        fx.close()


async def test_model_output_limit_parks_a_durable_turn():
    class TruncatingProvider:
        def __init__(self):
            self.calls = 0

        async def stream(self, *args, **kwargs):
            self.calls += 1
            yield _truncated()

    provider = TruncatingProvider()
    loop = AgentLoop(
        provider,
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(max_retries=0),
        pause_control=LocalTurnPauseControl(),
    )
    session = Session(session_id="s")
    events = [event async for event in loop.run_task(session, "write a long report")]
    assert events[-1].payload["finish_reason"] == "interrupted"
    assert provider.calls == 1, "a non-retryable limit must not loop extra requests"
