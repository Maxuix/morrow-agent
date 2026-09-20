"""Real offline data-root maintenance, observable waits and exact cleanup previews."""

import asyncio
import io
import zipfile

from morrow.core.artifacts import ArtifactKind
from morrow.core.store import SUPPORTED_SCHEMA_VERSION
from test_stage8_core_api import ServerFixture, create_session_and_task


async def finish(fx, response):
    assert response.status == 202, response.body
    cid = response.json()["command_id"]

    async def wait():
        task = fx.host.context.workspaces.maintenance_task
        if task:
            await asyncio.shield(task)

    await fx.host.execute_preparation(wait)
    job = (await fx.client.get("/v1/state-jobs/" + cid)).json()
    assert job["status"] == "completed", job
    return job["result"]


async def test_backup_download_verify_doctor_events_and_cleanup_preview(tmp_path):
    fx = ServerFixture(tmp_path)
    ws = None
    try:
        sid, tid, _ = await create_session_and_task(fx.client)

        def seed():
            a = fx.host.context.api.artifacts
            value = a.publish_bytes(
                b"must survive maintenance",
                kind=ArtifactKind.TASK_SUMMARY,
                session_id=sid,
                task_run_id=tid,
            )
            orphan = a.filesystem.artifacts_dir / "art_orphan_gui.artifact"
            orphan.write_bytes(b"unreferenced")
            orphan.chmod(0o600)
            return value, orphan

        artifact, orphan = await fx.on_core(seed)
        ws = fx.client.websocket()
        await ws.accept()
        body = {
            "command_id": "cmd_backup",
            "action": "backup",
            "name": "gui-offline",
            "confirmed": True,
        }
        result = await finish(fx, await fx.client.post("/v1/state-actions", body))
        assert result["integrity_ok"] and result["artifact_count"] == 1
        assert (await fx.client.post("/v1/state-actions", body)).json()["disposition"] == "replay"
        downloaded = await fx.client.get("/v1/state-downloads/" + result["download"])
        assert downloaded.status == 200
        with zipfile.ZipFile(io.BytesIO(downloaded.body)) as archive:
            assert "gui-offline.bundle/manifest.json" in archive.namelist()
            assert any(
                name.endswith(artifact.artifact_id + ".artifact") for name in archive.namelist()
            )
            assert all("credentials" not in name for name in archive.namelist())
        verified = await finish(
            fx,
            await fx.client.post(
                "/v1/state-actions",
                {"command_id": "cmd_verify", "action": "verify", "bundle": result["bundle_name"]},
            ),
        )
        assert verified["ok"]
        doctor = await finish(
            fx,
            await fx.client.post(
                "/v1/state-actions", {"command_id": "cmd_doctor", "action": "doctor"}
            ),
        )
        assert doctor["schema_version"] == SUPPORTED_SCHEMA_VERSION
        events = (await fx.client.get("/v1/state/events?limit=1")).json()
        assert len(events["events"]) == 1 and events["next_cursor"]
        assert "payload" not in events["events"][0]
        preview = await finish(
            fx,
            await fx.client.post(
                "/v1/state-actions", {"command_id": "cmd_preview", "action": "cleanup_preview"}
            ),
        )
        assert preview["eligible"] == 1 and orphan.exists()
        cleaned = await finish(
            fx,
            await fx.client.post(
                "/v1/state-actions",
                {
                    "command_id": "cmd_cleanup",
                    "action": "cleanup",
                    "preview_digest": preview["preview_digest"],
                    "confirmed": True,
                },
            ),
        )
        assert cleaned["quarantined"] == 1 and not orphan.exists()
        assert (await fx.client.get("/v1/artifacts/" + artifact.artifact_id)).status == 200
    finally:
        if ws:
            await ws.close()
        fx.close()


async def test_changed_cleanup_preview_is_rejected_and_new_writes_block_during_worker(
    tmp_path, monkeypatch
):
    import threading

    import morrow.server.state_routes as routes

    fx = ServerFixture(tmp_path)
    release = threading.Event()
    try:
        preview = await finish(
            fx,
            await fx.client.post(
                "/v1/state-actions", {"command_id": "cmd_preview", "action": "cleanup_preview"}
            ),
        )
        orphan = fx.state_root / "artifacts" / "art_later.artifact"
        orphan.write_bytes(b"late candidate")
        orphan.chmod(0o600)
        started = await fx.client.post(
            "/v1/state-actions",
            {
                "command_id": "cmd_changed",
                "action": "cleanup",
                "confirmed": True,
                "preview_digest": preview["preview_digest"],
            },
        )
        assert started.status == 202

        async def wait():
            task = fx.host.context.workspaces.maintenance_task
            if task:
                await asyncio.shield(task)

        await fx.host.execute_preparation(wait)
        job = (await fx.client.get("/v1/state-jobs/cmd_changed")).json()
        assert job["error"] == "preview_changed" and orphan.exists()
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()
        original = routes.OperationalDoctor.inspect

        def held(self, wid):
            loop.call_soon_threadsafe(entered.set)
            release.wait(10)
            return original(self, wid)

        monkeypatch.setattr(routes.OperationalDoctor, "inspect", held)
        started = await fx.client.post(
            "/v1/state-actions", {"command_id": "cmd_held", "action": "doctor"}
        )
        await asyncio.wait_for(entered.wait(), 5)
        assert (await fx.client.get("/v1/state/status")).json()["maintaining"]
        assert (await fx.client.get("/v1/meta")).status == 200
        assert (await fx.client.post("/v1/sessions", {})).status == 503
        release.set()
        await finish(fx, started)
        assert not (await fx.client.get("/v1/state/status")).json()["maintaining"]
        assert (await fx.client.post("/v1/sessions", {})).status == 200
    finally:
        release.set()
        fx.close()


async def test_maintenance_from_b_refuses_a_driver_and_pending_input(tmp_path):
    from morrow.core.interactions import InteractionRequest
    from test_stage8_chat_runtime_control import held_fixture

    fx, sid, _path, cell, _run = await held_fixture(tmp_path)
    try:
        folder = tmp_path / "other-workspace"
        folder.mkdir()

        def register():
            service = fx.app.workspace_service
            identity = service.confirm(service.resolve(folder))
            fx.host.context.workspaces.get(identity.workspace_id)
            return identity.workspace_id

        other = await fx.on_core(register)
        route = "/v1/workspaces/" + other + "/state-actions"
        body = {"command_id": "cmd_from_b", "action": "backup", "confirmed": True}
        assert (await fx.client.post(route, body)).status == 503
        assert not (await fx.client.get("/v1/state/status")).json()["maintaining"]

        async def queue_and_finish():
            manager = fx.host.context.chat
            # Keep a durable paused input after the active driver finishes.
            manager.interactions.records.pause(sid)
            manager.interactions.submit(
                sid,
                InteractionRequest(
                    client_message_id="queued-maintenance",
                    text="later",
                    intent="follow_up",
                    target_agent_run_id=_run,
                ),
            )
            cell["gate"].set()
            await asyncio.gather(*manager.drivers.values(), return_exceptions=True)

        await fx.host.execute_preparation(queue_and_finish)
        assert (await fx.client.post(route, body)).status == 503
        assert (
            fx.workspace_id
            in (await fx.client.get("/v1/workspaces/" + other + "/state/status")).json()["blockers"]
        )
    finally:
        await fx.on_core(cell["gate"].set)
        fx.close()
