"""Isolated GUI acceptance fixture: closure signals, review and automatic history.

All Providers are scripted. Use a fresh directory; open
http://127.0.0.1:8809/#token=replan-smoke-token .
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import uvicorn  # noqa: E402

from morrow.application.workflows.replan import revision_source  # noqa: E402
from morrow.core.models import AssistantMessage, FunctionToolCall  # noqa: E402
from morrow.core.orchestration import OrchestrationPolicy  # noqa: E402
from morrow.core.workflows.replan import ReplanRequest  # noqa: E402
from morrow.server.app import create_asgi_app  # noqa: E402
from test_stage8_core_api import (  # noqa: E402
    ServerFixture,
    create_session_and_task,
    publish_pipeline,
    start_run,
    wait_for_run,
)


def script():
    request = ReplanRequest(
        target_node_id="alpha",
        task_contract={"objective": "Summarize verified evidence and remaining gaps"},
    )
    return [
        AssistantMessage(
            tool_calls=(
                FunctionToolCall(
                    id="call_replan",
                    name="submit_node_result",
                    arguments='{"outputs": {}, "replan":' + request.model_dump_json() + "}",
                ),
            )
        ),
        "Evidence gathered; node completed normally.",
    ]


async def main(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    fixture = ServerFixture(
        directory,
        scripts=[
            script(),
            script(),
            script(),
            ["Automatic continuation complete"],
            ["Approved continuation complete"],
        ],
    )
    try:
        base = await publish_pipeline(fixture)
        run_ids = []
        for index in range(3):
            if index == 2:
                await fixture.on_core(
                    lambda: fixture.host.context.products.orchestration_policies.put(
                        OrchestrationPolicy(scope="workspace", auto_replan_mode="allow_low_risk"),
                        expected_revision=0,
                    )
                )
            sid, tid, version = await create_session_and_task(fixture.client)
            response = await start_run(fixture.client, base.workflow_revision_id, sid, tid, version)
            run_id = response.json()["result"]["run"]["workflow_run_id"]
            await wait_for_run(fixture.client, run_id, "superseded" if index == 2 else "paused")
            run_ids.append(run_id)
        await fixture.on_core(
            lambda: fixture.host.context.products.orchestration_policies.put(
                OrchestrationPolicy(scope="workspace"), expected_revision=1
            )
        )

        def high_risk():
            source = revision_source(base)
            source = source.model_copy(
                update={
                    "default_budget": source.default_budget.model_copy(
                        update={"max_agent_generation_requests": 999}
                    )
                }
            )
            return fixture.host.context.runtime.replan.propose(run_ids[0], source)

        await fixture.on_core(high_risk)
        print("Manual review, rejection, automatic history runs:", *run_ids, flush=True)
        app = create_asgi_app(
            fixture.host,
            auth_token="replan-smoke-token",
            gui_static_dir=ROOT / "src/morrow/gui_static",
        )
        await uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=8809, log_level="warning")
        ).serve()
    finally:
        fixture.close()


if __name__ == "__main__":
    try:
        asyncio.run(main(Path(sys.argv[1])))
    except KeyboardInterrupt:
        pass
