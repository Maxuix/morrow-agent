"""Real image artifacts and ASGI preview route; no native device or network."""

import io
import random
from types import SimpleNamespace

import pytest
from PIL import Image
from starlette.applications import Starlette
from starlette.responses import JSONResponse

from fixtures.core_api_client import CoreApiVerificationClient
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.computer_use import CoordinateFrame, TransientCapture
from morrow.core.domain import DurableSession, sha256_digest
from morrow.server.chat import chat_routes
from test_computer_visual_service import complete, publish
from test_computer_visual_service import environment as _environment


@pytest.fixture
def environment(tmp_path):
    yield from _environment.__wrapped__(tmp_path)


async def test_preview_route_reads_full_image_and_checks_visible_source_before_bytes(environment):
    service, journal, scope, _, observation, execution = environment
    buffer = io.BytesIO()
    image = Image.frombytes("RGB", (256, 256), random.Random(7).randbytes(256 * 256 * 3))
    image.save(buffer, format="PNG")
    data = buffer.getvalue()
    assert len(data) > 65536
    capture = TransientCapture(data, "image/png", 256, 256)
    updated = (
        service,
        journal,
        scope,
        capture,
        observation.model_copy(
            update={
                "capture_digest": sha256_digest(data),
                "frame": CoordinateFrame(width=256, height=256),
            }
        ),
        execution,
    )
    reference = publish(updated)
    journal.create_session(DurableSession(session_id="ses_other", workspace_id="ws_a"))

    class Host:
        def __init__(self):
            api = SimpleNamespace(artifacts=service.artifacts, journal=journal)
            manager = SimpleNamespace(api=api, require_session=self.require_session)
            self.context = SimpleNamespace(workspace_id="ws_a", workspaces=None, chat=manager)

        def require_session(self, sid):
            if journal.get_session("ws_a", sid) is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Session is missing")

        async def execute_preparation(self, query):
            return await query()

    async def errors(request, exc):
        status = 404 if exc.code is ApplicationErrorCode.NOT_FOUND else 503
        return JSONResponse({"code": exc.code.value}, status_code=status)

    app = Starlette(routes=chat_routes(Host(), None), exception_handlers={ApplicationError: errors})
    client = CoreApiVerificationClient(app, token="test")
    path = f"/v1/workspaces/ws_a/sessions/ses_1/artifacts/{reference.artifact_id}/content"
    assert (await client.get(path + "?raw=1")).status == 404  # Not yet in visible history.
    complete(updated, reference)
    response = await client.get(path + "?raw=1")
    assert response.status == 200
    assert response.body == data
    headers = dict(response.headers)
    assert headers["content-type"] == "image/png"
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"
    metadata = (await client.get(path)).json()
    assert metadata == {
        "kind": "computer_observation",
        "mime": "image/png",
        "width": 256,
        "height": 256,
        "byte_size": len(data),
    }
    assert (await client.get(path.replace("ses_1", "ses_other") + "?raw=1")).status == 404
    file_path = service.artifacts.filesystem.final_path(reference.artifact_id)
    file_path.write_bytes(b"broken image")
    assert (await client.get(path + "?raw=1")).status == 503
