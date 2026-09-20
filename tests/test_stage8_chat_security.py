"""Exact authority/origin checks cover JSON, content, static and WS adapters."""

import pytest

from fixtures.core_api_client import CoreApiVerificationClient, WSSession
from test_stage8_chat_submission import drain, new_session
from test_stage8_core_api import SERVE_TOKEN, ServerFixture


@pytest.mark.parametrize(
    "headers",
    [
        [(b"host", b"evil.example")],
        [(b"host", b"127.0.0.1:8080")],
        [(b"host", b"127.0.0.1:0")],
        [(b"host", b"127.0.0.1:")],
        [(b"host", b"127.0.0.1"), (b"host", b"localhost")],
        [(b"origin", b"null")],
        [(b"origin", b"")],
        [(b"origin", b"https://127.0.0.1")],
        [(b"origin", b"http://127.0.0.1:8080")],
        [(b"origin", b"http://localhost")],
        [(b"origin", b"http://127.0.0.1"), (b"origin", b"http://127.0.0.1")],
        [(b"referer", b"http://127.0.0.1:8080/page")],
        [(b"origin", b"http://user@127.0.0.1")],
    ],
)
async def test_illegal_authority_rejected_before_body(tmp_path, headers):
    fixture = ServerFixture(tmp_path)
    read = False
    try:

        async def modified(scope, receive, send):
            names = {k for k, _ in headers}
            scope = {
                **scope,
                "headers": [(k, v) for k, v in scope["headers"] if k not in names] + headers,
            }

            async def watched():
                nonlocal read
                read = True
                return await receive()

            await fixture.client.app(scope, watched, send)

        client = CoreApiVerificationClient(modified, token=SERVE_TOKEN)
        response = await client.post("/v1/sessions", {})
        assert response.status == 403
        assert not read
    finally:
        fixture.close()


async def test_ws_wrong_host_and_exact_origin_and_scoped_content(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        ws = WSSession(
            fixture.client.app,
            "/v1/events/stream?token=" + SERVE_TOKEN,
            headers=[(b"host", b"127.0.0.1:99")],
        )
        await ws.accept()
        assert ws.closed_code == 4403
        await ws.close()
        sid, path = await new_session(fixture)
        await fixture.client.post(
            path + "/interactions", {"client_message_id": "content", "text": "test"}
        )
        await drain(fixture, sid)
        page = (await fixture.client.get(path + "/timeline")).json()
        rid = page["items"][0]["source"]["record_id"]
        assert (
            await fixture.client.get(path + "/content/" + rid, origin="http://127.0.0.1:81")
        ).status == 403
        assert (
            await fixture.client.get(path + "/content/" + rid, origin="http://127.0.0.1")
        ).status == 200
        assert (
            await fixture.client.get(f"/v1/workspaces/{fixture.workspace_id}/approvals")
        ).status == 200
        assert (await fixture.client.get("/v1/workspaces/ws_other/approvals")).status == 403
    finally:
        fixture.close()


async def test_body_limit_counts_bytes_not_claimed_length(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:

        async def modified(scope, receive, send):
            scope = {
                **scope,
                "headers": [
                    (k, b"1" if k == b"content-length" else v) for k, v in scope["headers"]
                ],
            }
            await fixture.client.app(scope, receive, send)

        client = CoreApiVerificationClient(modified, token=SERVE_TOKEN)
        response = await client.post("/v1/sessions", {"padding": "x" * 1048576})
        assert response.status == 413
        response = await fixture.client.post("/v1/sessions", {"secret-field-" + "x" * 10000: True})
        assert response.status == 400
        assert len(response.body) < 256 and b"secret-field" not in response.body
    finally:
        fixture.close()
