"""Chat GUI command/settings and token-free local GUI access, real Core."""

from morrow.server.app import create_asgi_app
from test_stage8_chat_submission import new_session
from test_stage8_core_api import SERVE_TOKEN, ServerFixture


async def test_chat_settings_and_explicit_task_compact_commands(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        caps = (await fixture.client.get("/v1/capabilities")).json()
        assert caps["effective_settings"] == {
            "model": "fake-provider/m1",
            "permission": "workspace / manual / host",
        }
        assert caps["features"]["compact"]["available"]
        assert caps["features"]["generation_settings"]["available"]
        sid, path = await new_session(fixture)
        compact = {"action": "compact", "command_id": "cmd_compact"}
        response = await fixture.client.post(path + "/commands", compact)
        assert response.status == 200, response.body
        assert "上下文" in response.json()["message"]
        response = await fixture.client.post(path + "/commands", compact)
        assert "原请求回执" in response.json()["message"]
        assert not fixture.host.context.chat.drivers
        task = {"action": "task", "command_id": "cmd_task"}
        response = await fixture.client.post(path + "/commands", task)
        assert response.status == 200, response.body
        again = await fixture.client.post(path + "/commands", task)
        assert again.status == 200
        assert (
            await fixture.client.post(path + "/commands", {"action": "unknown", "command_id": "x"})
        ).status == 400
        assert (
            await fixture.client.post(
                path.replace(fixture.workspace_id, "other") + "/commands", task
            )
        ).status == 403
    finally:
        fixture.close()


async def test_gui_without_token_keeps_exact_origin_for_mutations(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        static = tmp_path / "static"
        static.mkdir()
        (static / "index.html").write_text("<html></html>")
        app = create_asgi_app(fixture.host, auth_token=SERVE_TOKEN, gui_static_dir=static)
        from fixtures.core_api_client import CoreApiVerificationClient

        client = CoreApiVerificationClient(app, token="")
        response = await client.get("/v1/meta")
        assert response.status == 200, response.body
        response = await client.post(
            "/v1/sessions",
            {},
        )
        assert response.status == 403
        response = await client.post(
            "/v1/sessions",
            {},
            origin="http://127.0.0.1",
        )
        assert response.status == 200, response.body
        response = await client.post(
            "/v1/sessions",
            {},
            origin="http://127.0.0.1:9999",
        )
        assert response.status == 403
    finally:
        fixture.close()


async def test_chat_artifact_preview_is_scoped_bounded_and_inert(tmp_path):
    from morrow.core.artifacts import ArtifactKind

    fixture = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fixture)
        _, other = await new_session(fixture, "cmd_other")
        artifact = await fixture.on_core(
            lambda: fixture.host.context.api.artifacts.publish_bytes(
                b"<script>inert</script>\n" + b"x" * 70000,
                kind=ArtifactKind.DIFF,
                session_id=sid,
            )
        )
        response = await fixture.client.get(path + f"/artifacts/{artifact.artifact_id}/content")
        assert response.status == 200, response.body
        assert response.json()["content"].startswith("<script>inert</script>")
        assert len(response.json()["content"].encode()) == 65536
        assert response.json()["truncated"]
        assert (
            await fixture.client.get(other + f"/artifacts/{artifact.artifact_id}/content")
        ).status == 404
    finally:
        fixture.close()


async def test_chat_artifact_content_raw_returns_original_bytes(tmp_path):
    """The ?raw=1 variant serves original bytes for binary consumers (GUI
    asset previews); the default JSON text shape stays compatible."""
    from morrow.core.artifacts import ArtifactKind

    fixture = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fixture)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        artifact = await fixture.on_core(
            lambda: fixture.host.context.api.artifacts.publish_bytes(
                png,
                kind=ArtifactKind.DIAGNOSTIC_REPORT,
                session_id=sid,
            )
        )
        raw = await fixture.client.get(path + f"/artifacts/{artifact.artifact_id}/content?raw=1")
        assert raw.status == 200, raw.body
        assert raw.body == png
        assert ("content-type", "image/png") in raw.headers
        as_json = await fixture.client.get(path + f"/artifacts/{artifact.artifact_id}/content")
        assert as_json.status == 200
        assert as_json.json()["byte_size"] == len(png)
        assert as_json.json()["truncated"] is False
    finally:
        fixture.close()


async def test_chat_artifact_file_read_does_not_stall_core_commands(tmp_path, monkeypatch):
    import asyncio
    import threading

    from morrow.core.artifacts import ArtifactKind

    fixture = ServerFixture(tmp_path)
    release = threading.Event()
    entered = asyncio.Event()
    caller = asyncio.get_running_loop()
    pending = None
    try:
        sid, path = await new_session(fixture)
        service = fixture.host.context.api.artifacts
        artifact = await fixture.on_core(
            lambda: service.publish_bytes(
                b"output",
                kind=ArtifactKind.DIFF,
                session_id=sid,
            )
        )
        original = service.filesystem.read

        def read(*args, **kwargs):
            caller.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return original(*args, **kwargs)

        monkeypatch.setattr(service.filesystem, "read", read)
        pending = asyncio.create_task(
            fixture.client.get(path + f"/artifacts/{artifact.artifact_id}/content")
        )
        await asyncio.wait_for(entered.wait(), 2)
        assert await asyncio.wait_for(fixture.host.execute_command(lambda: "alive"), 2) == "alive"
        release.set()
        response = await pending
        assert response.status == 200
        assert response.json()["content"] == "output"
    finally:
        release.set()
        if pending is not None:
            await pending
        fixture.close()
