"""Management startup must not need model credentials or an active Session."""

from fixtures.core_api_client import CoreApiVerificationClient
from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.bootstrap import build_application
from morrow.server.app import create_asgi_app
from morrow.server.composition import make_context_builder
from morrow.server.host import CoreHost


async def test_empty_configuration_starts_management(tmp_path):
    application = build_application(
        state_root=tmp_path / "state", credentials=MemoryCredentialStore()
    )
    root = tmp_path / "workspace"
    root.mkdir()
    identity = application.workspace_service.confirm(application.workspace_service.resolve(root))
    host = CoreHost(make_context_builder(application, identity))
    host.start()
    try:
        client = CoreApiVerificationClient(create_asgi_app(host, auth_token="test"), token="test")
        for path in ["/v1/meta", "/v1/snapshot", "/v1/sessions", "/v1/workflow-runs"]:
            response = await client.get(path)
            assert response.status == 200, (path, response.body)
        assert (await client.get("/v1/sessions")).json()["sessions"] == []
        response = await client.post("/v1/sessions", {})
        assert response.status == 200
    finally:
        host.stop()


async def test_scoped_sessions_idempotent_and_isolated_runtimes(tmp_path):
    from test_stage8_core_api import ServerFixture

    fixture = ServerFixture(tmp_path)
    try:
        path = f"/v1/workspaces/{fixture.workspace_id}/sessions"
        body = {"command_id": "cmd_create_a"}
        a = (await fixture.client.post(path, body)).json()["result"]["session"]["session_id"]
        assert (await fixture.client.post(path, body)).json()["result"]["session"][
            "session_id"
        ] == a
        b = (await fixture.client.post(path, {"command_id": "cmd_create_b"})).json()["result"][
            "session"
        ]["session_id"]

        def check():
            first = fixture.host.context.chat.runtime(a)
            second = fixture.host.context.chat.runtime(b)
            assert first.session is not second.session
            assert first.session.log is not second.session.log
            assert first.api.journal is second.api.journal is fixture.host.context.journal
            assert first.persistence.store_session is second.persistence.store_session
            assert fixture.host.context.chat.runtime(a) is first

        await fixture.on_core(check)
        assert (await fixture.client.get(path + "/" + a)).status == 200
        assert (await fixture.client.get("/v1/workspaces/ws_unknown/sessions/" + a)).status == 403
    finally:
        fixture.close()


async def test_chat_and_workflow_share_top_level_gate(tmp_path):
    import asyncio

    from test_stage8_core_api import ServerFixture

    fixture = ServerFixture(tmp_path)
    try:
        path = f"/v1/workspaces/{fixture.workspace_id}/sessions"
        sid = (await fixture.client.post(path, {"command_id": "cmd_gate"})).json()["result"][
            "session"
        ]["session_id"]

        async def journey():
            chat = fixture.host.context.chat
            workflow = fixture.host.context.supervisor
            started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
            seen = []

            async def first():
                seen.append("chat_started")
                started.set()
                await release.wait()
                seen.append("chat_finished")

            async def second():
                seen.append("workflow")
                finished.set()

            assert chat.ensure_driver(sid, first)
            assert not chat.ensure_driver(sid, first)
            await started.wait()
            workflow.ensure_driver("wrun_test_gate", second)
            assert seen == ["chat_started"]
            release.set()
            await finished.wait()
            assert seen == ["chat_started", "chat_finished", "workflow"]
            assert not chat.drivers
            assert not workflow._drivers

        await fixture.host.execute_preparation(journey)
    finally:
        fixture.close()


async def test_missing_saved_credential_keeps_gui_repair_available(tmp_path):
    from morrow.core.models import CredentialRef, ModelRef, ProviderConfig, ProviderModelConfig

    application = build_application(
        state_root=tmp_path / "state", credentials=MemoryCredentialStore()
    )
    application.provider_service.add_provider(
        "saved", adapter_id="openai-compatible", base_url="https://api.example.test/v1"
    )
    application.provider_service.add_model("saved", "model")
    application.provider_service.use_model("saved", "model")
    config = application.global_store.load()
    application.global_store.update(
        lambda value: value.model_copy(
            update={
                "providers": {
                    "saved": ProviderConfig(
                        adapter="openai-compatible",
                        base_url="https://api.example.test/v1",
                        credential_ref=CredentialRef(ref="missing-key", version=1),
                        models={"model": ProviderModelConfig(api_model_id="model")},
                    )
                },
                "active_model": ModelRef(provider_id="saved", model_id="model"),
            }
        ),
        expected_revision=config.revision,
    )
    root = tmp_path / "workspace"
    root.mkdir()
    identity = application.workspace_service.confirm(application.workspace_service.resolve(root))
    host = CoreHost(make_context_builder(application, identity))
    try:
        host.start()
        client = CoreApiVerificationClient(create_asgi_app(host, auth_token="test"), token="test")
        settings = await client.get("/v1/providers")
        assert settings.status == 200, settings.body
        assert not settings.json()["providers"][0]["credential_configured"]
        saved = await client.post(
            "/v1/providers/saved/credentials",
            {"expected_revision": settings.json()["revision"], "secret": "offline-fixture-key"},
        )
        assert saved.status == 200, saved.body
        assert (await client.get("/v1/providers")).json()["providers"][0]["credential_configured"]
        assert (await client.get("/v1/sessions")).json()["sessions"] == []
    finally:
        host.stop()


async def test_configured_server_startup_does_not_create_phantom_session(tmp_path):
    from test_stage8_core_api import ServerFixture

    fixture = ServerFixture(tmp_path)
    try:
        assert (await fixture.client.get("/v1/sessions")).json()["sessions"] == []
        assert (await fixture.client.get(f"/v1/workspaces/{fixture.workspace_id}/sessions")).json()[
            "sessions"
        ] == []

        def check_journal():
            assert fixture.host.context.journal.list_sessions(fixture.workspace_id) == ()

        await fixture.on_core(check_journal)

        result = await fixture.client.post("/v1/sessions", {"command_id": "cmd_create_explicit"})
        assert result.status == 200
        created_id = result.json()["result"]["session"]["session_id"]

        sessions = (await fixture.client.get("/v1/sessions")).json()["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == created_id

        # Also test workspace-scoped chat creation endpoint (used by GUI sidebar)
        ws_result = await fixture.client.post(
            f"/v1/workspaces/{fixture.workspace_id}/sessions",
            {"command_id": "cmd_create_ws"},
        )
        assert ws_result.status == 200
        ws_created_id = ws_result.json()["result"]["session"]["session_id"]

        ws_sessions = (
            await fixture.client.get(f"/v1/workspaces/{fixture.workspace_id}/sessions")
        ).json()["sessions"]
        assert len(ws_sessions) == 2
        assert {s["session_id"] for s in ws_sessions} == {created_id, ws_created_id}
    finally:
        fixture.close()


async def test_switching_workspace_does_not_create_phantom_session(tmp_path):
    from test_stage8_core_api import ServerFixture

    fixture = ServerFixture(tmp_path)
    try:
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        resolution = fixture.app.workspace_service.resolve(other_dir)
        other_identity = fixture.app.workspace_service.confirm(resolution)

        def load_other():
            registry = fixture.host.context.workspaces
            ctx = registry.get(other_identity.workspace_id)
            assert ctx.journal.list_sessions(other_identity.workspace_id) == ()
            _ = ctx.products.orchestrator
            assert ctx.journal.list_sessions(other_identity.workspace_id) == ()

        await fixture.on_core(load_other)
        sessions = (
            await fixture.client.get(f"/v1/workspaces/{other_identity.workspace_id}/sessions")
        ).json()["sessions"]
        assert sessions == []
    finally:
        fixture.close()
