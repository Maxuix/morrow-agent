"""Desktop defaults and status through the shared Core loop; no native run or grant."""

from morrow.adapters import computer_use
from morrow.core.computer_use import ComputerUsePreflight
from test_stage8_chat_submission import new_session
from test_stage8_core_api import ServerFixture


async def test_desktop_settings_occ_status_and_session_model_without_grant(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fx)
        url = path + "/computer-use/settings"
        before_driver = computer_use.DRIVER_CONSTRUCTION_COUNT
        probes = []

        def probe(settings):
            probes.append(settings)
            return ComputerUsePreflight(status="unavailable", reason="native_unverified")

        await fx.on_core(
            lambda: setattr(fx.host.context.chat.computer_settings, "preflight", probe)
        )
        initial = await fx.client.get(url)
        assert initial.status == 200
        initial = initial.json()
        assert initial["settings"]["enabled"] is False
        assert initial["host"] == {"status": "unavailable", "reason": "disabled"}
        assert initial["model"]["model_id"] == "m1"
        assert initial["required_permission"] == "full-access-manual"
        assert probes == []
        original_config = await fx.on_core(lambda: fx.app.global_store.load().value)
        request = {
            "expected_revision": initial["revision"],
            "settings": {"enabled": True, "mode": "hybrid", "max_operations": 20},
        }
        saved = await fx.client.post(url, request)
        assert saved.status == 200, saved.body
        saved = saved.json()
        changed_config = await fx.on_core(lambda: fx.app.global_store.load().value)
        assert changed_config.providers == original_config.providers
        assert changed_config.active_model == original_config.active_model
        assert changed_config.chat_settings == original_config.chat_settings
        assert saved["applies_to"] == "future_runs"
        assert saved["settings"]["mode"] == "hybrid"
        assert saved["settings"]["max_operations"] == 20
        assert saved["host"] == {"status": "unavailable", "reason": "native_unverified"}
        assert saved["model_capabilities"] == {"function_tools": True, "images": False}
        assert saved["model_error"] == "images_not_supported"
        assert (await fx.client.post(url, request)).status == 409
        assert (await fx.client.get(url)).json() == saved
        chat = (await fx.client.get(path + "/settings")).json()
        assert chat["effective"]["model"]["model_id"] == "m1"
        assert chat["effective"]["permission"] == "manual"
        assert computer_use.DRIVER_CONSTRUCTION_COUNT == before_driver
        assert (
            await fx.on_core(
                lambda: fx.host.context.journal.list_capability_grants(fx.workspace_id)
            )
            == ()
        )
        invalid = await fx.client.post(
            url,
            {
                "expected_revision": saved["revision"],
                "settings": {"enabled": "true", "mode": "hybrid"},
            },
        )
        assert invalid.status == 400
        assert (await fx.client.get(url)).json() == saved
        request = {"expected_revision": saved["revision"], "settings": {"enabled": False}}
        disabled = (await fx.client.post(url, request)).json()
        count = len(probes)
        assert disabled["host"]["reason"] == "disabled"
        assert (await fx.client.get(url)).json() == disabled
        assert len(probes) == count
        missing = path.replace(sid, "ses_missing") + "/computer-use/settings"
        assert (await fx.client.get(missing)).status == 404
    finally:
        fx.close()
