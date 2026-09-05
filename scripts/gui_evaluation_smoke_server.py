"""Isolated loopback evaluation acceptance, using scripted completed Direct/Multi runs."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import uvicorn  # noqa: E402

from morrow.server.app import create_asgi_app  # noqa: E402
from test_stage8_core_api import ServerFixture  # noqa: E402
from test_stage8_feedback_evaluation import completed  # noqa: E402
from test_stage8_graph_planner import publish_roles  # noqa: E402


async def main(directory):
    directory.mkdir(parents=True, exist_ok=True)
    fixture = ServerFixture(directory, scripts=[["Verified result; no unresolved findings."]] * 16)
    try:
        await publish_roles(fixture)
        for number in range(2):
            direct, _ = await completed(fixture, f"direct_{number}", multi=False)
            multi, _ = await completed(fixture, f"multi_{number}", multi=True)
            response = await fixture.client.post(
                "/v1/management/workflow-feedback",
                {
                    "command_id": f"cmd_smoke_feedback_{number}",
                    "workflow_run_id": multi,
                    "kind": "too_complex",
                },
            )
            assert response.status == 200
            response = await fixture.client.post(
                "/v1/management/workflow-evaluation",
                {
                    "command_id": f"cmd_smoke_pair_{number}",
                    "multi_run_id": multi,
                    "direct_run_id": direct,
                    "direct_quality": 2,
                    "multi_quality": 4,
                },
            )
            assert response.status == 200
        app = create_asgi_app(
            fixture.host,
            auth_token="evaluation-smoke-token",
            gui_static_dir=ROOT / "src/morrow/gui_static",
        )
        await uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=8811, log_level="warning")
        ).serve()
    finally:
        fixture.close()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
