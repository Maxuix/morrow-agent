"""Isolated loopback GraphPlanner acceptance fixture; all Providers are scripted.

Run with a fresh directory: uv run python scripts/gui_planner_smoke_server.py /tmp/planner-smoke
Open http://127.0.0.1:8808/#token=planner-smoke-token and use the Workflow editor.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import uvicorn  # noqa: E402

from morrow.server.app import create_asgi_app  # noqa: E402
from test_stage8_core_api import ServerFixture  # noqa: E402
from test_stage8_graph_planner import publish_roles  # noqa: E402


async def main(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    fixture = ServerFixture(directory)
    try:
        await publish_roles(fixture)
        app = create_asgi_app(
            fixture.host,
            auth_token="planner-smoke-token",
            gui_static_dir=ROOT / "src/morrow/gui_static",
        )
        await uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=8808, log_level="warning")
        ).serve()
    finally:
        fixture.close()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
