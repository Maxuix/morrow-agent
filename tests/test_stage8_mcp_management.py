"""Offline MCP workbench integration, scope isolation and write-only credentials."""

import base64

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent

from morrow.adapters.mcp.stdio_client import McpAdapterError, McpStdioClient
from morrow.application.mcp.credentials import credential_bindings, resolve_environment
from test_mcp_control import _definition
from test_stage8_core_api import ServerFixture


async def _command(
    fx, action, *, scope="workspace", server_id="mcp_fake", command_id=None, **kwargs
):
    current = (await fx.client.get("/v1/mcp-management?scope=" + scope)).json()
    body = {
        "action": action,
        "scope": scope,
        "server_id": server_id,
        "command_id": command_id or "cmd_" + action,
        "expected_revision": current["revision"],
        "expected_digest": current["digest"],
        "confirmed": True,
        **kwargs,
    }
    return await fx.client.post("/v1/mcp-actions", body), body


async def test_mcp_crud_refresh_scope_occ_replay_and_runtime_resolution(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        definition = _definition().model_dump(mode="json")
        added, body = await _command(fx, "add", definition=definition)
        assert added.status == 200, added.body
        assert (await fx.client.post("/v1/mcp-actions", body)).status == 200
        assert (
            await fx.client.post("/v1/mcp-actions", {**body, "command_id": "cmd_add_stale"})
        ).status == 409
        local = (await fx.client.get("/v1/mcp-management?identity=mcp_fake")).json()
        assert not local["server"]["enabled"]
        assert "argv" not in local["server"]
        assert (await fx.client.get("/v1/mcp-management?scope=global")).json()["servers"] == []
        failed, _ = await _command(fx, "enable", command_id="cmd_enable_before_catalog")
        assert failed.status == 400
        refreshed, refresh_body = await _command(fx, "refresh")
        assert refreshed.status == 200, refreshed.body
        await fx.host.execute_preparation(
            lambda: fx.host.context.supervisor.wait_driver("mcp_cmd_refresh")
        )
        job = (await fx.client.get("/v1/mcp-jobs/cmd_refresh")).json()
        assert job["status"] == "completed", job
        assert (await fx.client.post("/v1/mcp-actions", refresh_body)).status == 200
        enabled, enable_body = await _command(fx, "enable", expected_server_revision=1)
        assert enabled.status == 200, enabled.body
        inspect = (await fx.client.get("/v1/mcp-management?identity=mcp_fake")).json()
        assert inspect["server"]["enabled"]
        assert any(t["remote_name"] == "echo" and t["status"] == "ready" for t in inspect["tools"])

        # Production composition sees the same YAML/catalog when freezing an AgentRun.
        def state():
            from morrow.adapters.state.extension_yaml import ExtensionYamlStore
            from morrow.application.mcp.definitions import McpDefinitionService

            defs = McpDefinitionService(ExtensionYamlStore(fx.app.data_root.root), fx.workspace_id)
            current = defs.show("mcp_fake", "workspace", scope_id=fx.workspace_id)
            return current, fx.host.context.journal.get_mcp_catalog(
                "workspace", "mcp_fake", scope_id=fx.workspace_id
            )

        actual, catalog = await fx.on_core(state)
        assert actual.enabled and catalog.status.value == "ready"
        assert (await fx.client.get("/v1/workspaces/ws_other/mcp-management")).status == 403
        disabled, _ = await _command(fx, "disable", expected_server_revision=2)
        assert disabled.status == 200
        removed, remove_body = await _command(fx, "remove")
        assert removed.status == 200, removed.body
        assert (await fx.client.post("/v1/mcp-actions", remove_body)).status == 200
        assert (await fx.client.get("/v1/mcp-management")).json()["servers"] == []
    finally:
        fx.close()


async def test_mcp_credentials_are_scoped_write_only_and_actually_resolved(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        definition = _definition().model_dump(mode="json")
        for scope in ("workspace", "global"):
            added, _ = await _command(
                fx,
                "add",
                scope=scope,
                command_id="cmd_add_" + scope,
                definition=definition,
                environment_names=["SERVICE_TOKEN"],
            )
            assert added.status == 200, added.body
        secret = "opaque-fixture-credential-value"
        written = await fx.client.post(
            "/v1/mcp-credentials",
            {
                "server_id": "mcp_fake",
                "scope": "workspace",
                "environment_name": "SERVICE_TOKEN",
                "expected_server_revision": 1,
                "secret": secret,
            },
        )
        assert written.status == 200, written.body
        assert secret not in written.body.decode()
        local = (await fx.client.get("/v1/mcp-management?identity=mcp_fake")).json()
        global_ = (await fx.client.get("/v1/mcp-management?scope=global&identity=mcp_fake")).json()
        assert local["credentials"] == [{"name": "SERVICE_TOKEN", "available": True}]
        assert global_["credentials"] == [{"name": "SERVICE_TOKEN", "available": False}]
        assert secret not in str(local)

        def read():
            from morrow.adapters.state.extension_yaml import ExtensionYamlStore
            from morrow.application.mcp.definitions import McpDefinitionService

            definition = McpDefinitionService(
                ExtensionYamlStore(fx.app.data_root.root), fx.workspace_id
            ).show("mcp_fake", "workspace", scope_id=fx.workspace_id)
            return definition, resolve_environment(definition, fx.app.credentials)

        selected, environment = await fx.on_core(read)
        assert environment == {"SERVICE_TOKEN": secret}
        assert all("mcp:" in v for v in credential_bindings(selected).values())
        assert secret not in "".join(p.read_text() for p in fx.state_root.rglob("*.yaml"))
        assert all(
            secret.encode() not in p.read_bytes() for p in fx.state_root.rglob("*") if p.is_file()
        )
        bad, _ = await _command(
            fx,
            "add",
            server_id="mcp_bad",
            command_id="cmd_bad_env",
            definition={**definition, "server_id": "mcp_bad"},
            environment_names=["PYTHONPATH"],
        )
        assert bad.status == 400
        # Refresh receives only this server's resolved values. No real network is used.
        refreshed, _ = await _command(fx, "refresh", command_id="cmd_credential_refresh")
        assert refreshed.status == 200
        await fx.host.execute_preparation(
            lambda: fx.host.context.supervisor.wait_driver("mcp_cmd_credential_refresh")
        )
        assert (await fx.client.get("/v1/mcp-jobs/cmd_credential_refresh")).json()[
            "status"
        ] == "completed"
    finally:
        fx.close()


async def test_mcp_directory_pages_include_every_scoped_server(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        definition = _definition().model_dump(mode="json")
        for index in range(21):
            identity = f"mcp_page_{index:02}"
            result, _ = await _command(
                fx,
                "add",
                server_id=identity,
                command_id=f"cmd_page_{index}",
                definition={**definition, "server_id": identity},
            )
            assert result.status == 200, result.body
        first = (await fx.client.get("/v1/mcp-management")).json()
        second = (await fx.client.get("/v1/mcp-management?page=1")).json()
        assert len(first["servers"]) == 20 and first["next_cursor"]
        assert len(second["servers"]) == 1 and second["next_cursor"] is None
        assert len({r["server_id"] for r in first["servers"] + second["servers"]}) == 21
        assert (await fx.client.get("/v1/mcp-management?page=101")).status == 400
        assert (await fx.client.get("/v1/mcp-management?scope=global")).json()["servers"] == []
    finally:
        fx.close()


@pytest.mark.parametrize("binary", [False, True])
async def test_adapter_rejects_exact_credential_echo_before_normalization(binary):
    secret = "opaque-secret-only-in-child-env"
    client = McpStdioClient(_definition(), environment={"SERVICE_TOKEN": secret}, secrets=(secret,))

    class Session:
        async def call_tool(self, *args):
            content = (
                ImageContent(
                    type="image",
                    data=base64.b64encode(("prefix " + secret).encode()).decode(),
                    mimeType="image/png",
                )
                if binary
                else TextContent(type="text", text="prefix " + secret)
            )
            return CallToolResult(content=[content])

    client._session = Session()
    with pytest.raises(McpAdapterError) as caught:
        await client.call_tool("echo", {})
    assert caught.value.code == "credential_echo"
    assert secret not in str(caught.value)


async def test_mcp_write_intent_recovers_after_yaml_before_receipt(tmp_path, monkeypatch):
    from morrow.application.mcp.definitions import McpDefinitionService

    fx = ServerFixture(tmp_path)
    try:
        original = McpDefinitionService.apply_change

        def interrupted(self, change):
            original(self, change)
            raise RuntimeError("fixture interruption after YAML")

        monkeypatch.setattr(McpDefinitionService, "apply_change", interrupted)
        failed, body = await _command(
            fx,
            "add",
            definition=_definition().model_dump(mode="json"),
            command_id="cmd_interrupted_mcp",
        )
        assert failed.status == 503
        monkeypatch.setattr(McpDefinitionService, "apply_change", original)
        recovered = await fx.client.post("/v1/mcp-actions", body)
        assert recovered.status == 200, recovered.body
        assert (await fx.client.get("/v1/mcp-management")).json()["revision"] == 1
        assert (
            await fx.client.post(
                "/v1/mcp-actions",
                {**body, "definition": {**body["definition"], "display_name": "Changed"}},
            )
        ).status == 409
        # Editing keeps immutable prior catalog versions; it requires a new refresh.
        first = (await fx.client.get("/v1/mcp-management?identity=mcp_fake")).json()
        updated, update_body = await _command(
            fx,
            "update",
            command_id="cmd_update_mcp",
            expected_server_revision=1,
            definition={**body["definition"], "display_name": "Renamed"},
            keep_arguments=True,
        )
        assert updated.status == 200, updated.body
        second = (await fx.client.get("/v1/mcp-management?identity=mcp_fake")).json()
        assert second["server"]["catalog_revision"] > first["server"]["catalog_revision"]
        assert second["server"]["display_name"] == "Renamed" and not second["server"]["enabled"]
        assert (await fx.client.post("/v1/mcp-actions", update_body)).status == 200
    finally:
        fx.close()


async def test_catalog_refresh_does_not_hold_bus_and_discards_changed_configuration(
    tmp_path, monkeypatch
):
    import asyncio

    import morrow.server.mcp_routes as routes
    from morrow.application.mcp.catalog import degraded_catalog_snapshot

    fx = ServerFixture(tmp_path)
    try:
        added, _ = await _command(fx, "add", definition=_definition().model_dump(mode="json"))
        assert added.status == 200

        def events():
            return asyncio.Event(), asyncio.Event()

        entered, release = await fx.on_core(events)
        calls = []

        class HeldCatalog:
            def __init__(self, **kwargs):
                pass

            async def refresh(self, definition, **kwargs):
                calls.append(definition)
                entered.set()
                await release.wait()
                return degraded_catalog_snapshot(definition, revision=2, reason="fixture")

        monkeypatch.setattr(routes, "McpCatalogService", HeldCatalog)
        started, body = await _command(fx, "refresh")
        assert started.status == 200
        await fx.host.execute_preparation(entered.wait)
        assert (await fx.client.post("/v1/mcp-actions", body)).status == 200
        # A foreground change completes while discovery is held; its result wins.
        changed, _ = await _command(fx, "disable", expected_server_revision=1)
        assert changed.status == 200
        await fx.on_core(release.set)
        await fx.host.execute_preparation(
            lambda: fx.host.context.supervisor.wait_driver("mcp_cmd_refresh")
        )
        job = (await fx.client.get("/v1/mcp-jobs/cmd_refresh")).json()
        assert job["status"] == "failed" and job["error"] == "configuration_changed"
        assert len(calls) == 1
    finally:
        fx.close()
