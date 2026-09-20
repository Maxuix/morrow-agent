"""Chat-facing preset preference and quick-save endpoints over production Core."""

from morrow.core.models import ModelRef
from test_stage8_chat_submission import new_session
from test_stage8_core_api import ServerFixture

MODEL = ModelRef(provider_id="fake-provider", model_id="m1")


def presets_root(fx):
    return f"/v1/workspaces/{fx.workspace_id}/agent-presets"


async def test_preset_catalog_lists_fixed_roles_and_availability(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, root = await new_session(fx)
        assert sid
        page = await fx.client.get(presets_root(fx))
        assert page.status == 200, page.body
        body = page.json()
        assert [row["role"] for row in body["presets"]] == ["general", "explore", "review"]
        assert all(row["preference"] is None for row in body["presets"])
        assert all(row["preference_source"] == "inherit_session" for row in body["presets"])
        assert body["revision"] == 0
        capabilities = await fx.client.get("/v1/capabilities")
        features = capabilities.json()["features"]
        assert features["agent_presets"]["available"] is True
        assert features["agent_quick_save"]["available"] is True
    finally:
        fx.close()


async def test_preference_put_occ_and_save_time_validation(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, _root = await new_session(fx)
        assert sid
        first = await fx.client.request(
            "PUT",
            presets_root(fx) + "/builtin_general",
            body={
                "command_id": "cmd_pref_1",
                "definition_id": "builtin_general",
                "preference": {
                    "definition_id": "builtin_general",
                    "model": {"provider_id": "fake-provider", "model_id": "m1"},
                },
                "expected_revision": 0,
            },
        )
        assert first.status == 200, first.body
        assert first.json()["revision"] == 1
        page = await fx.client.get(presets_root(fx))
        general = next(row for row in page.json()["presets"] if row["role"] == "general")
        assert general["preference_source"] == "preset_preference"
        assert general["preference"]["model"] == {"provider_id": "fake-provider", "model_id": "m1"}

        stale = await fx.client.request(
            "PUT",
            presets_root(fx) + "/builtin_general",
            body={
                "command_id": "cmd_pref_2",
                "definition_id": "builtin_general",
                "preference": {
                    "definition_id": "builtin_general",
                    "model": {"provider_id": "fake-provider", "model_id": "m2"},
                },
                "expected_revision": 0,
            },
        )
        assert stale.status == 409, stale.body

        # An unsupported explicit effort is rejected at save time, not at task start.
        unsupported = await fx.client.request(
            "PUT",
            presets_root(fx) + "/builtin_explore",
            body={
                "command_id": "cmd_pref_3",
                "definition_id": "builtin_explore",
                "preference": {
                    "definition_id": "builtin_explore",
                    "model": {"provider_id": "fake-provider", "model_id": "m1"},
                    "generation": {"mode": "explicit", "value": "high"},
                },
                "expected_revision": 1,
            },
        )
        assert unsupported.status == 400, unsupported.body

        wrong_target = await fx.client.request(
            "PUT",
            presets_root(fx) + "/builtin_review",
            body={
                "command_id": "cmd_pref_4",
                "definition_id": "builtin_general",
                "preference": {"definition_id": "builtin_general"},
                "expected_revision": 1,
            },
        )
        assert wrong_target.status == 400, wrong_target.body
    finally:
        fx.close()


async def test_quick_save_is_available_and_replays_one_receipt(tmp_path):
    fx = ServerFixture(tmp_path, scripts=[[]])
    try:
        sid, _root = await new_session(fx)
        assert sid
        body = {
            "command_id": "cmd_quick_1",
            "name": "Helper Agent",
            "purpose": "Inspect recovery tests",
            "prompt": "Inspect the assigned files and report findings.",
            "tools": ["read", "grep"],
        }
        saved = await fx.client.post("/v1/agent-definitions/quick-save", body)
        assert saved.status == 200, saved.body
        result = saved.json()
        assert result["definition_id"] == "helper-agent"
        assert result["enabled"] is True
        assert (
            result["available_version_id"]
            == result["agent_definition"]["published_version"]["version_id"]
        )

        replay = await fx.client.post("/v1/agent-definitions/quick-save", body)
        assert replay.status == 200, replay.body
        assert (
            replay.json()["agent_definition"]["published_version"]["version_id"]
            == result["available_version_id"]
        )
        assert replay.json()["receipt"]["disposition"] == "replay"

        def facts():
            backend = fx.host.context.journal._backend
            return backend.read_one(
                "SELECT count(*) FROM agent_definition_versions WHERE workspace_id=? AND definition_id='helper-agent'",
                (fx.workspace_id,),
            )[0]

        assert await fx.on_core(facts) == 1

        conflict = await fx.client.post(
            "/v1/agent-definitions/quick-save", {**body, "prompt": "A different prompt entirely."}
        )
        assert conflict.status == 409, conflict.body

        listing = await fx.client.get("/v1/catalog/agent-definitions")
        assert any(
            item["definition_id"] == "helper-agent" for item in listing.json()["agent_definitions"]
        )
    finally:
        fx.close()
