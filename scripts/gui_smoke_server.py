"""Browser-smoke server for the Stage 8 GUI (acceptance aid, not a test).

Boots the real serve composition with scripted providers, seeds a two-node
workflow that parks at a deterministic approval, then serves the prebuilt GUI
bundle on http://127.0.0.1:8799 with a fixed smoke token so the reconnect
banner can be observed across a server restart. Loopback only; no real
provider or network is involved. Run: `uv run python scripts/gui_smoke_server.py
/tmp/morrow-gui-smoke` and Ctrl+C to stop.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import uvicorn  # noqa: E402

from morrow.core.agent_definitions import ToolRequirement  # noqa: E402
from morrow.server.app import create_asgi_app  # noqa: E402
from test_stage7_serial_scheduler import ScriptBank, agent_source  # noqa: E402
from test_stage8_core_api import (  # noqa: E402
    WRITE_CALL_SCRIPT,
    ServerFixture,
    create_session_and_task,
    publish_pipeline,
    start_run,
)

TOKEN = "gui-smoke-token"
HOST = "127.0.0.1"
PORT = 8799


async def wait_approval_pending(fixture, run_id: str) -> str:
    for _ in range(4000):
        await asyncio.sleep(0)
        response = await fixture.client.get(f"/v1/workflow-runs/{run_id}")
        view = response.json()["view"]
        if any(node["approval_pending"] for node in view["nodes"]):
            approvals = await fixture.client.get("/v1/approvals")
            return approvals.json()["approvals"][0]["approval_id"]
    raise AssertionError("approval never became pending")


async def main(state_dir: Path, *, serve_only: bool) -> None:
    if serve_only:
        # Restart path for the reconnect smoke: same state, same fixed token,
        # no reseeding. The seeded run is already terminal, so the scripted
        # provider is never consulted.
        from morrow.bootstrap import build_application
        from morrow.core.agent_runs import ProviderCapabilities
        from morrow.core.capabilities import PermissionProfile
        from morrow.server.composition import make_context_builder
        from morrow.server.host import CoreHost

        application = build_application(state_root=state_dir / "state-root")
        application.credentials.set("provider:fake-provider:test", "topsecret-value")
        application.registry.register(
            "fake-adapter",
            ScriptBank([["session composition"]]),
            capabilities=ProviderCapabilities(
                tool_protocol="openai_function", multiple_tool_calls=True
            ),
        )
        resolution = application.workspace_service.resolve(state_dir / "workspace")
        identity = resolution.identity
        host = CoreHost(
            make_context_builder(application, identity, permission_profile=PermissionProfile())
        )
        host.start()
        try:
            app = create_asgi_app(
                host,
                auth_token=TOKEN,
                ws_ping_seconds=5,
                gui_static_dir=ROOT / "src" / "morrow" / "gui_static",
            )
            print(f"GUI_SMOKE_SERVE_ONLY http://{HOST}:{PORT}", flush=True)
            config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
            await uvicorn.Server(config).serve()
        finally:
            host.stop()
        return

    from test_stage8_core_api import write_pair_source

    state_dir.mkdir(parents=True, exist_ok=True)
    fixture = ServerFixture(state_dir)
    try:
        agent = agent_source(
            access_mode_ceiling="write",
            tool_requirements=(
                ToolRequirement(name="update_configuration", requirement="required"),
            ),
        )
        revision = await publish_pipeline(fixture, agent=agent, make_source=write_pair_source)
        fixture.bank.scripts.extend([WRITE_CALL_SCRIPT, ["phase three"]])
        session_id, task_id, task_version = await create_session_and_task(fixture.client)
        started = await start_run(
            fixture.client, revision.workflow_revision_id, session_id, task_id, task_version
        )
        assert started.status == 200, started.body
        run_id = started.json()["result"]["run"]["workflow_run_id"]
        approval_id = await wait_approval_pending(fixture, run_id)

        app = create_asgi_app(
            fixture.host,
            auth_token=TOKEN,
            ws_ping_seconds=5,
            gui_static_dir=ROOT / "src" / "morrow" / "gui_static",
        )
        facts = {
            "url": f"http://{HOST}:{PORT}/#token={TOKEN}",
            "server_pid": os.getpid(),
            "run_id": run_id,
            "approval_id": approval_id,
            "session_id": session_id,
            "task_id": task_id,
            "workspace_id": fixture.workspace_id,
            "workspace_dir": str(fixture.workspace_dir),
            "state_root": str(fixture.state_root),
            "resolve_curl": (
                f"curl -s -X POST http://{HOST}:{PORT}/v1/approvals/{approval_id}/resolve "
                f'-H "Authorization: Bearer {TOKEN}" -H "Content-Type: application/json" '
                "-d '{\"approved\": true}'"
            ),
        }
        print("GUI_SMOKE_FACTS " + json.dumps(facts, ensure_ascii=False), flush=True)
        config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning", access_log=False)
        await uvicorn.Server(config).serve()
    finally:
        fixture.close()


if __name__ == "__main__":
    serve_only = "--serve-only" in sys.argv
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    try:
        asyncio.run(main(Path(args[0] if args else "/tmp/morrow-gui-smoke"), serve_only=serve_only))
    except KeyboardInterrupt:
        pass
