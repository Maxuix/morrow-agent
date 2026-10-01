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


async def test_local_window_picker_is_session_scoped_and_does_not_create_grants(tmp_path):
    import pytest

    from morrow.application.computer_use import ComputerUseLifecycle
    from morrow.core.application import ApplicationError
    from test_computer_use_candidates import Driver, owner_for

    fx = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fx)
        other, other_path = await new_session(fx, "cmd_other")
        enabled = (await fx.client.get(path + "/computer-use/settings")).json()
        assert (
            await fx.client.post(
                path + "/computer-use/settings",
                {"expected_revision": enabled["revision"], "settings": {"enabled": True}},
            )
        ).status == 200
        # Permission is checked before attempting any native diagnostic/read.
        assert (await fx.client.post(path + "/computer-use/candidates", {})).status == 400
        for url in (path, other_path):
            view = (await fx.client.get(url + "/settings")).json()
            assert (
                await fx.client.post(
                    url + "/settings",
                    {
                        "expected_revision": view["documents"]["session"]["revision"],
                        "settings": {"permission": "full-access-manual"},
                    },
                )
            ).status == 200

        before_driver = computer_use.DRIVER_CONSTRUCTION_COUNT
        unavailable = await fx.client.post(path + "/computer-use/candidates", {})
        assert unavailable.status == 503
        assert computer_use.DRIVER_CONSTRUCTION_COUNT == before_driver
        driver = Driver()
        resources = {}

        def install():
            context = fx.host.context
            owner, lease, clock, sessions = owner_for(driver)
            lifecycle = ComputerUseLifecycle(
                lambda: owner,
                lambda: ComputerUsePreflight(status="unavailable", reason="native_unverified"),
                native_verified=True,
            )  # Fake SDK only.
            context.computer_use = lifecycle
            context.chat.computer_selection.lifecycle = lifecycle
            context.chat.computer_selection.clock = clock
            context.chat.computer_settings.preflight = lambda _: ComputerUsePreflight(
                status="unavailable", reason="native_unverified"
            )
            resources.update(owner=owner, lease=lease, clock=clock, sessions=sessions)

        await fx.on_core(install)
        catalog = await fx.client.post(path + "/computer-use/candidates", {})
        assert catalog.status == 200, catalog.body
        catalog = catalog.json()
        assert len(catalog["candidates"]) == 1
        candidate_id = catalog["candidates"][0]["candidate_id"]
        assert "4242" not in str(catalog) and "9001" not in str(catalog)
        request = {
            "candidate_ids": [candidate_id],
            "allow_action": True,
            "share_images": False,
            "delivery": "foreground",
        }
        # Another Session cannot use this catalog, even with the same permission preset.
        assert (await fx.client.post(other_path + "/computer-use/selection", request)).status == 409
        selected = await fx.client.post(path + "/computer-use/selection", request)
        assert selected.status == 200, selected.body
        selected = selected.json()
        assert selected["operations"] == ["observe", "action"]
        assert selected["image_share"] == "none" and selected["applies_to"] == "one_future_run"
        assert resources["sessions"] == [] and not resources["lease"].held
        assert {name for name, _ in driver.calls} == {"list_apps", "list_windows"}
        assert (
            await fx.on_core(
                lambda: fx.host.context.journal.list_capability_grants(fx.workspace_id)
            )
            == ()
        )
        selection = await fx.on_core(
            lambda: fx.host.context.chat.computer_selection.consume(sid, selected["selection_id"])
        )
        scope = selection.bind(
            workspace_id=fx.workspace_id,
            task_run_id="task_test",
            agent_run_id="arun_test",
            generation=1,
        )
        assert scope.schema_version == 2 and len(scope.windows) == 1
        with pytest.raises(ApplicationError, match="重新绑定"):
            await fx.on_core(
                lambda: fx.host.context.chat.computer_selection.consume(
                    sid, selected["selection_id"]
                )
            )
        assert (
            await fx.client.post(
                path + "/computer-use/selection", request | {"allow_action": "true"}
            )
        ).status == 400
        assert (
            await fx.client.post(path + "/computer-use/selection", request | {"share_images": True})
        ).status == 400
        assert (
            await fx.client.post(path + "/computer-use/candidates", {"scope": "desktop"})
        ).status == 400
        assert (await fx.client.get(path + "/computer-use/candidates")).status == 405
        assert (
            await fx.client.post(path.replace(sid, "ses_missing") + "/computer-use/candidates", {})
        ).status == 404
        resources["clock"].value = resources["clock"].value.replace(year=2027)
        assert (await fx.client.post(path + "/computer-use/selection", request)).status == 409
    finally:
        fx.close()


async def test_candidate_preparation_allows_permission_commands_and_rechecks_before_commit(
    tmp_path,
):
    import asyncio

    from morrow.application.computer_use import ComputerUseLifecycle
    from test_computer_use_candidates import Driver, owner_for

    fx = ServerFixture(tmp_path)
    driver = None
    try:
        sid, path = await new_session(fx)
        initial = (await fx.client.get(path + "/computer-use/settings")).json()
        assert (
            await fx.client.post(
                path + "/computer-use/settings",
                {"expected_revision": initial["revision"], "settings": {"enabled": True}},
            )
        ).status == 200
        initial = (await fx.client.get(path + "/settings")).json()
        permission = await fx.client.post(
            path + "/settings",
            {
                "expected_revision": initial["documents"]["session"]["revision"],
                "settings": {"permission": "full-access-manual"},
            },
        )
        assert permission.status == 200
        permission_revision = permission.json()["documents"]["session"]["revision"]

        class BlockingDriver(Driver):
            def __init__(self):
                super().__init__()
                self.entered, self.release = asyncio.Event(), asyncio.Event()

            async def list_apps(self, payload):
                self.entered.set()
                await self.release.wait()
                return await super().list_apps(payload)

        def install():
            context = fx.host.context
            driver = BlockingDriver()
            owner, lease, clock, sessions = owner_for(driver)
            lifecycle = ComputerUseLifecycle(
                lambda: owner,
                lambda: ComputerUsePreflight(status="unavailable", reason="native_unverified"),
                native_verified=True,
            )
            context.computer_use = lifecycle
            context.chat.computer_selection.lifecycle = lifecycle
            context.chat.computer_selection.clock = clock
            return driver

        driver = await fx.on_core(install)
        pending = asyncio.create_task(fx.client.post(path + "/computer-use/candidates", {}))
        await fx.host.execute_preparation(driver.entered.wait)
        # This command succeeds while SDK read preparation is awaiting its Event.
        changed = await fx.client.post(
            path + "/settings",
            {"expected_revision": permission_revision, "settings": {"permission": "manual"}},
        )
        assert changed.status == 200
        await fx.on_core(driver.release.set)
        refused = await pending
        assert refused.status == 400
        assert await fx.on_core(
            lambda: sid not in fx.host.context.chat.computer_selection._catalogs
        )
        assert (
            await fx.on_core(
                lambda: fx.host.context.journal.list_capability_grants(fx.workspace_id)
            )
            == ()
        )
    finally:
        if driver is not None:
            await fx.on_core(driver.release.set)
        fx.close()
