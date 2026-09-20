"""Real Core, one SQLite/journal owner across bounded workspace runtimes."""

import pytest

from morrow.core.application import ApplicationError
from morrow.server.workspaces import MAX_WORKSPACE_RUNTIMES
from morrow.services.workspace import WorkspaceError, WorkspaceWriterLock
from test_stage8_core_api import ServerFixture


async def test_registry_shared_owner_eviction_reload_and_writer_lock(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:

        def exercise():
            registry = fixture.host.context.workspaces
            identities = []
            for i in range(MAX_WORKSPACE_RUNTIMES + 2):
                folder = tmp_path / f"project-{i}"
                folder.mkdir()
                service = fixture.app.workspace_service
                identity = service.confirm(service.resolve(folder))
                identities.append(identity)
                context = registry.get(identity.workspace_id)
                assert context.journal is registry.default.journal
                assert context.store_handle is registry.default.store_handle
                assert context.chat is not registry.default.chat
                context.api.create_session(session_id=f"ses_{i}", command_id=f"cmd_{i}")
                assert len(registry.contexts) <= MAX_WORKSPACE_RUNTIMES
            first = identities[0]
            assert first.workspace_id not in registry.contexts
            with WorkspaceWriterLock(fixture.app.data_root, first.workspace_id):
                with pytest.raises(ApplicationError, match="another writer"):
                    registry.get(first.workspace_id)
            restored = registry.get(first.workspace_id)
            assert restored.api.get_session("ses_0") is not None
            assert restored.api.get_session("ses_1") is None
            with pytest.raises(WorkspaceError):
                with WorkspaceWriterLock(fixture.app.data_root, first.workspace_id):
                    pass
            return identities

        identities = await fixture.on_core(exercise)
    finally:
        fixture.close()
    for identity in identities:
        with WorkspaceWriterLock(fixture.app.data_root, identity.workspace_id):
            pass


async def test_registry_active_subscription_prevents_unload(tmp_path):
    import asyncio

    fixture = ServerFixture(tmp_path)
    try:

        def load():
            folder = tmp_path / "second"
            folder.mkdir()
            service = fixture.app.workspace_service
            identity = service.confirm(service.resolve(folder))
            registry = fixture.host.context.workspaces
            context = registry.get(identity.workspace_id)
            context.api.create_session(session_id="ses_sub", command_id="cmd_sub")
            token, _ = context.chat.streams.subscribe("ses_sub", asyncio.get_running_loop())
            with pytest.raises(ApplicationError, match="in use"):
                registry.unload(identity.workspace_id)
            context.chat.streams.unsubscribe("ses_sub", token)
            registry.unload(identity.workspace_id)
            assert registry.get(identity.workspace_id).api.get_session("ses_sub") is not None

        await fixture.on_core(load)
    finally:
        fixture.close()


async def test_two_workspaces_have_scoped_history_and_default_compatibility(tmp_path):
    from test_stage7_serial_scheduler import wait_for

    fixture = ServerFixture(tmp_path, scripts=(["reply A"], ["reply B"]))
    try:

        def register():
            folder = tmp_path / "second"
            folder.mkdir()
            service = fixture.app.workspace_service
            return service.confirm(service.resolve(folder)).workspace_id

        other = await fixture.on_core(register)
        paths = []
        for wid, label in ((fixture.workspace_id, "a"), (other, "b")):
            root = f"/v1/workspaces/{wid}/sessions"
            response = await fixture.client.post(root, {"command_id": f"cmd_{label}"})
            assert response.status == 200, response.body
            sid = response.json()["result"]["session"]["session_id"]
            path = f"{root}/{sid}"
            paths.append(path)
            result = await fixture.client.post(
                path + "/interactions", {"client_message_id": "same-key", "text": label}
            )
            assert result.status == 202, result.body
        await wait_for(
            lambda: all(
                not c.chat.drivers for c in fixture.host.context.workspaces.contexts.values()
            )
        )
        for path in paths:
            response = await fixture.client.get(path + "/snapshot")
            assert response.status == 200, response.body
            response = await fixture.client.get(path + "/interactions/same-key")
            assert response.json()["receipt"]["status"] == "settled"
            assert response.json()["receipt"]["workspace_id"] in path
        foreign = paths[0].replace(fixture.workspace_id, other)
        assert (await fixture.client.get(foreign + "/snapshot")).status == 404
        default = (await fixture.client.get("/v1/sessions")).json()
        assert paths[0].rsplit("/", 1)[1] in {s["session_id"] for s in default["sessions"]}
        assert paths[1].rsplit("/", 1)[1] not in {s["session_id"] for s in default["sessions"]}
        caps = (await fixture.client.get(f"/v1/workspaces/{other}/capabilities")).json()
        assert caps["workspace_id"] == other
    finally:
        fixture.close()
