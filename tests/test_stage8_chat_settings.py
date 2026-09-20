"""OCC defaults are isolated and resolve per field without opening a Provider."""

import pytest

from morrow.core.models import ChatSettings, ModelRef
from test_stage8_chat_submission import new_session
from test_stage8_core_api import ServerFixture


async def test_scoped_settings_precedence_occ_and_restart(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fx)
        other, other_path = await new_session(fx, "cmd_other")
        initial = (await fx.client.get(path + "/settings")).json()
        assert initial["effective"]["model"]["model_id"] == "m1"
        assert initial["sources"]["model"]["scope"] == "global"
        global_before = initial["documents"]["global"]
        request = {
            "scope": "workspace",
            "expected_revision": initial["documents"]["workspace"]["revision"],
            "settings": {
                "model": {"provider_id": "fake-provider", "model_id": "m2"},
                "permission": "auto-safe",
            },
        }
        saved = await fx.client.post(path + "/settings", request)
        assert saved.status == 200, saved.body
        assert saved.json()["effective"]["model"]["model_id"] == "m2"
        assert saved.json()["documents"]["global"] == global_before
        assert (await fx.client.post(path + "/settings", request)).status == 409
        request = {
            "expected_revision": 0,
            "settings": {"model": {"provider_id": "fake-provider", "model_id": "m1"}},
        }
        saved = await fx.client.post(path + "/settings", request)
        assert saved.status == 200, saved.body
        view = saved.json()
        assert view["effective"]["permission"] == "auto-safe"
        assert view["sources"]["model"]["scope"] == "session"
        assert view["sources"]["permission"]["scope"] == "workspace"
        assert (await fx.client.get(other_path + "/settings")).json()["effective"]["model"][
            "model_id"
        ] == "m2"
        resolved = await fx.on_core(
            lambda: fx.host.context.chat.settings.resolve(
                sid, ChatSettings(model=ModelRef(provider_id="fake-provider", model_id="m2"))
            )
        )
        assert resolved[0].model.model_id == "m2"
        assert resolved[1]["model"]["scope"] == "explicit"
        invalid = await fx.client.post(
            path + "/settings",
            {
                "expected_revision": 1,
                "settings": {"model": {"provider_id": "missing", "model_id": "m"}},
            },
        )
        assert invalid.status == 400
        assert (await fx.client.get(path + "/settings")).json() == view
        assert (
            await fx.client.post(
                path + "/settings",
                {"expected_revision": 1, "settings": {"generation": {"arbitrary_secret": "x"}}},
            )
        ).status == 400
        # Durable Session settings survive runtime cache eviction/recomposition.
        await fx.on_core(lambda: fx.host.context.chat.runtimes.clear())
        assert (await fx.client.get(path + "/settings")).json() == view
    finally:
        fx.close()


async def test_permission_selection_freezes_each_run_and_rehydrates_original_profile(tmp_path):
    from morrow.core.capabilities import PermissionPreset, PermissionProfile
    from morrow.core.domain import canonical_json_bytes, sha256_digest
    from test_stage8_chat_submission import drain

    fx = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fx)
        original_log = None
        runs = []
        for i, preset in enumerate(("auto-safe", "full-access-manual", "manual")):
            view = (await fx.client.get(path + "/settings")).json()
            response = await fx.client.post(
                path + "/settings",
                {
                    "expected_revision": view["documents"]["session"]["revision"],
                    "settings": {"permission": preset},
                },
            )
            assert response.status == 200, response.body
            sent = await fx.client.post(
                path + "/interactions", {"client_message_id": f"permission.{i}", "text": "hello"}
            )
            assert sent.status == 202, sent.body
            await drain(fx, sid)
            receipt = (await fx.client.get(path + f"/interactions/permission.{i}")).json()[
                "receipt"
            ]
            assert receipt["status"] == "settled", receipt
            run = await fx.on_core(
                lambda receipt=receipt: fx.host.context.journal.get_agent_run(
                    fx.workspace_id, receipt["agent_run_id"]
                )
            )
            profile = PermissionProfile.from_preset(PermissionPreset(preset))
            assert run.snapshot.permission_profile_digest == sha256_digest(
                canonical_json_bytes(profile.model_dump(mode="json"))
            )
            assert run.snapshot.provider_runtime.permission_preset == preset
            assert run.snapshot.provider_runtime.settings_sources["permission"].revision == i + 1
            log = fx.host.context.chat.runtimes[sid].session.log
            assert original_log is None or original_log is log
            original_log = log
            runs.append(run)

        def restore():
            products = fx.host.context.chat.runtimes[sid]
            hydrated = products.orchestrator.preparation.rehydrate(runs[0].snapshot)
            assert products.session.permission_profile.approval_mode.value == "auto_safe"
            assert (
                hydrated.tool_executor.capability_policy.profile
                == products.session.permission_profile
            )

        await fx.on_core(restore)
    finally:
        fx.close()


@pytest.mark.parametrize("change", ["endpoint", "credential", "sandbox"])
async def test_queued_provider_changes_revalidate_after_execution_gate(tmp_path, change):
    import asyncio

    from test_stage7_serial_scheduler import wait_for
    from test_stage8_chat_submission import drain

    fx = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fx)

        if change == "sandbox":
            await fx.on_core(
                lambda: setattr(fx.host.context.chat.settings, "sandbox_probe", lambda: True)
            )
            assert (
                await fx.client.post(
                    path + "/settings",
                    {
                        "expected_revision": 0,
                        "settings": {"permission": "auto-sandboxed"},
                    },
                )
            ).status == 200

        async def hold():
            release, entered = asyncio.Event(), asyncio.Event()

            async def holder():
                async with fx.host.context.supervisor.execution_lock:
                    entered.set()
                    await release.wait()

            task = asyncio.create_task(holder())
            await entered.wait()
            return release, task

        release, holder = await fx.host.execute_preparation(hold)
        response = await fx.client.post(
            path + "/interactions", {"client_message_id": "stale.queued", "text": "hello"}
        )
        assert response.status == 202

        async def driver_waiting():
            await wait_for(
                lambda: (
                    sid in fx.host.context.chat.drivers
                    and bool(fx.host.context.workspaces.coordinator.waiting)
                )
            )

        await fx.host.execute_preparation(driver_waiting)

        def invalidate():
            service = fx.app.provider_service
            if change == "endpoint":
                service.configure_saved("fake-provider", base_url="https://changed.example.test")
            elif change == "credential":
                provider = service.list().providers["fake-provider"]
                service.credentials.delete(provider.credential_ref.ref)
            else:
                fx.host.context.chat.settings.sandbox_probe = lambda: False

        await fx.on_core(invalidate)
        await fx.on_core(release.set)
        await drain(fx, sid)
        receipt = (await fx.client.get(path + "/interactions/stale.queued")).json()["receipt"]
        assert receipt["status"] == "blocked", receipt
        assert receipt["reason"] == "settings_unavailable"
        assert receipt["turn_id"] is None and receipt["user_record_id"] is None
        assert not any(p.stream_calls for p in fx.bank.providers)
    finally:
        if "release" in locals():
            await fx.on_core(release.set)
        fx.close()


async def test_unrelated_provider_catalog_change_preserves_queued_binding(tmp_path):
    from morrow.core.interactions import InteractionRequest

    fx = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fx)

        def verify():
            service = fx.host.context.chat.interactions
            binding = service._binding(sid, ChatSettings())
            fx.app.provider_service.add_model("fake-provider", "unrelated")
            after = service._binding(sid, ChatSettings())
            assert binding["provider_digest"] == after["provider_digest"]
            # Observation/test metadata does not invalidate execution identity.
            from morrow.core.models import LastTestResult

            current = fx.app.provider_service.list()
            fx.app.provider_service._commit(
                current,
                lambda value: value.model_copy(
                    update={
                        "providers": {
                            **value.providers,
                            "fake-provider": value.providers["fake-provider"].model_copy(
                                update={"last_test": LastTestResult(ok=True)}
                            ),
                        }
                    }
                ),
            )
            assert (
                service._binding(sid, ChatSettings())["provider_digest"]
                == binding["provider_digest"]
            )
            # Current requests keep their canonical serialized payload/digest.
            assert (
                InteractionRequest(client_message_id="old", text="hello").model_dump(mode="json")[
                    "settings"
                ]
                == {}
            )

        await fx.on_core(verify)
    finally:
        fx.close()


async def test_four_presets_and_unavailable_sandbox_fail_closed(tmp_path):
    from test_stage8_chat_submission import drain

    fx = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fx)
        await fx.on_core(
            lambda: setattr(fx.host.context.chat.settings, "sandbox_probe", lambda: False)
        )
        view = (await fx.client.get(path + "/settings")).json()
        assert [p["preset"] for p in view["permission_presets"]] == [
            "manual",
            "auto-safe",
            "auto-sandboxed",
            "full-access-manual",
        ]
        assert [p["preset"] for p in view["permission_presets"] if not p["available"]] == [
            "auto-sandboxed"
        ]
        body = {"expected_revision": 0, "settings": {"permission": "auto-sandboxed"}}
        assert (await fx.client.post(path + "/settings", body)).status == 503
        sent = await fx.client.post(
            path + "/interactions",
            {
                "client_message_id": "no.native",
                "text": "hello",
                "settings": {"permission": "auto-sandboxed"},
            },
        )
        assert sent.status == 503
        await drain(fx, sid)
        assert not any(p.stream_calls for p in fx.bank.providers)
        assert (await fx.client.get(path + "/settings")).json()["documents"]["session"][
            "revision"
        ] == 0
    finally:
        fx.close()
