"""Real API control operations, explicit probes and secret-safe projections."""

import asyncio
import json

from test_stage8_core_api import ServerFixture


async def test_provider_onboarding_is_offline_occ_and_credentials_write_only(tmp_path):
    fx = ServerFixture(tmp_path)
    calls = []

    class Fake:
        async def complete(self, model, messages):
            calls.append(model)
            return "ok"

    try:
        await fx.on_core(lambda: fx.app.registry.register("settings-fake", lambda c, k: Fake()))
        view = (await fx.client.get("/v1/providers")).json()
        revision = view["revision"]
        body = {
            "expected_revision": revision,
            "provider_id": "new",
            "adapter_id": "settings-fake",
            "base_url": "https://example.test/v1",
        }
        added = await fx.client.post("/v1/providers", body)
        assert added.status == 200, added.body
        assert (await fx.client.post("/v1/providers", body)).status == 409
        view = added.json()
        response = await fx.client.post(
            "/v1/providers/new/credentials",
            {
                "expected_revision": view["revision"],
                "secret": "write-only-fake-credential",
            },
        )
        assert response.status == 200, response.body
        assert response.json() == {"saved": True}
        view = (await fx.client.get("/v1/providers")).json()
        response = await fx.client.post(
            "/v1/providers/new/models",
            {
                "expected_revision": view["revision"],
                "model_id": "local",
                "api_model_id": "remote",
            },
        )
        assert response.status == 200, response.body
        view = response.json()
        response = await fx.client.post(
            "/v1/providers/new/model",
            {
                "expected_revision": view["revision"],
                "action": "use",
                "model_id": "local",
            },
        )
        assert response.status == 200, response.body
        assert not calls
        view = response.json()
        assert view["active_model"] == {"provider_id": "new", "model_id": "local"}
        response = await fx.client.post(
            "/v1/providers/new/test", {"expected_revision": view["revision"]}
        )
        assert response.status == 200, response.body
        assert len(calls) == 1
        public = json.dumps(response.json()) + (await fx.client.get("/v1/events")).body.decode()
        assert "write-only-fake-credential" not in public
        assert "credential_ref" not in public
        assert "write-only-fake-credential" not in fx.app.data_root.config_path.read_text()
        view = response.json()
        assert (
            await fx.client.post(
                "/v1/providers/new/remove", {"expected_revision": view["revision"]}
            )
        ).status == 400
        # No credentials are accepted by ordinary configuration mutations.
        assert (
            await fx.client.post(
                "/v1/providers/new/configure",
                {
                    "expected_revision": view["revision"],
                    "base_url": "https://example.test",
                    "secret": "bad",
                },
            )
        ).status == 400
        assert (
            await fx.client.post(
                "/v1/providers/new/unknown", {"expected_revision": view["revision"]}
            )
        ).status == 404
    finally:
        fx.close()


async def test_slow_discovery_leaves_core_responsive_and_rejects_stale_publish(tmp_path):
    fx = ServerFixture(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def discovery(config, credential):
        entered.set()
        await release.wait()
        return [{"model_id": "discovered", "api_model_id": "remote"}]

    try:
        await fx.on_core(
            lambda: fx.app.registry.register("fake-adapter", lambda c, k: None, discovery=discovery)
        )
        view = (await fx.client.get("/v1/providers")).json()
        task = asyncio.create_task(
            fx.client.post(
                "/v1/providers/fake-provider/discover", {"expected_revision": view["revision"]}
            )
        )
        await asyncio.wait_for(entered.wait(), 3)
        assert (await fx.client.get("/v1/meta")).status == 200
        changed = await fx.client.post(
            "/v1/providers/fake-provider/configure",
            {
                "expected_revision": view["revision"],
                "base_url": "https://new.example.test",
            },
        )
        assert changed.status == 200, changed.body
        release.set()
        result = await task
        assert result.status == 409, result.body
        current = (await fx.client.get("/v1/providers")).json()
        assert all(m["model_id"] != "discovered" for m in current["providers"][0]["models"])
    finally:
        release.set()
        fx.close()
