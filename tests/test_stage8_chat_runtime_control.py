"""Real Core scripted provider barriers for queue and explicit Stop behavior."""

import asyncio

import pytest

from test_stage8_chat_submission import drain, new_session
from test_stage8_core_api import ServerFixture


async def held_fixture(tmp_path):
    fixture = ServerFixture(tmp_path)
    started = asyncio.Event()
    loop = asyncio.get_running_loop()
    cell = {}

    def hook(count):
        if count == 3:
            provider = fixture.bank.providers[-1]
            original = provider.stream

            async def held(*args, **kwargs):
                cell["gate"] = asyncio.Event()
                loop.call_soon_threadsafe(started.set)
                await cell["gate"].wait()
                async for event in original(*args, **kwargs):
                    yield event

            provider.stream = held

    fixture.bank.on_create = hook
    sid, path = await new_session(fixture)
    first = await fixture.client.post(
        path + "/interactions", {"client_message_id": "first", "text": "first question"}
    )
    assert first.status == 202
    await asyncio.wait_for(started.wait(), 5)
    receipt = (await fixture.client.get(path + "/interactions/first")).json()["receipt"]
    return fixture, sid, path, cell, receipt["agent_run_id"]


async def test_follow_up_steering_withdraw_and_other_session_queue(tmp_path):
    fixture, sid, path, cell, run_id = await held_fixture(tmp_path)
    try:
        for key, intent in [("later", "follow_up"), ("remove", "follow_up"), ("correct", "steer")]:
            response = await fixture.client.post(
                path + "/interactions",
                {
                    "client_message_id": key,
                    "text": key,
                    "intent": intent,
                    "target_agent_run_id": run_id,
                },
            )
            assert response.status == 202, response.body
        withdrawn = await fixture.client.post(
            path + "/interactions/remove/withdraw",
            {"command_id": "cmd_withdraw", "expected_revision": 1},
        )
        assert withdrawn.status == 200, withdrawn.body
        assert (
            await fixture.client.post(
                path + "/interactions/remove/withdraw",
                {"command_id": "cmd_withdraw", "expected_revision": 1},
            )
        ).status == 200
        sid_b, path_b = await new_session(fixture, "cmd_b")
        assert (
            await fixture.client.post(
                path_b + "/interactions", {"client_message_id": "b", "text": "other session"}
            )
        ).status == 202
        assert (await fixture.client.get(path_b + "/interactions/b")).json()["receipt"][
            "turn_id"
        ] is None
        await fixture.on_core(lambda: cell["gate"].set())
        await drain(fixture, sid)
        await drain(fixture, sid_b)

        def verify():
            journal = fixture.host.context.journal
            texts = [
                r.payload["content"]
                for r in journal.load_records(fixture.workspace_id, sid)
                if r.payload.get("role") == "user"
            ]
            assert texts == ["first question", "correct", "later"]
            assert len(journal.list_session_turns(fixture.workspace_id, sid_b)) == 1

        await fixture.on_core(verify)
        assert (await fixture.client.get(path + "/interactions/remove")).json()["receipt"][
            "status"
        ] == "withdrawn"
        assert (
            await fixture.client.post(
                path + "/interactions",
                {
                    "client_message_id": "stale",
                    "text": "late steer",
                    "intent": "steer",
                    "target_agent_run_id": run_id,
                },
            )
        ).status == 409
    finally:
        fixture.close()


async def test_stop_during_model_wait_pauses_queue_and_has_explicit_continue(tmp_path):
    fixture, sid, path, cell, run_id = await held_fixture(tmp_path)
    try:
        assert (
            await fixture.client.post(
                path + "/interactions",
                {
                    "client_message_id": "later",
                    "text": "later",
                    "intent": "follow_up",
                    "target_agent_run_id": run_id,
                },
            )
        ).status == 202
        queue = (await fixture.client.get(path + "/queue")).json()
        request = {
            "command_id": "cmd_stop",
            "action": "stop",
            "target_agent_run_id": run_id,
            "expected_revision": queue["revision"],
        }
        response = await fixture.client.post(path + "/control", request)
        assert response.status == 200, response.body
        await drain(fixture, sid)
        assert (await fixture.client.post(path + "/control", request)).status == 200
        receipt = (await fixture.client.get(path + "/interactions/first")).json()["receipt"]
        assert receipt["run_status"] == "cancelled"
        assert (await fixture.client.get(path + "/interactions/later")).json()["receipt"][
            "turn_id"
        ] is None
        queue = (await fixture.client.get(path + "/queue")).json()
        assert queue["paused"]
        response = await fixture.client.post(
            path + "/control",
            {
                "command_id": "cmd_continue",
                "action": "continue_queue",
                "expected_revision": queue["revision"],
            },
        )
        assert response.status == 200, response.body
        await drain(fixture, sid)
        assert (await fixture.client.get(path + "/interactions/later")).json()["receipt"][
            "status"
        ] == "settled"
    finally:
        fixture.close()


async def test_chat_approval_can_resolve_or_stop_while_driver_waits(tmp_path):
    from test_stage8_core_api import WRITE_CALL_SCRIPT

    fixture = ServerFixture(tmp_path)
    try:
        for stop in (False, True):
            sid, path = await new_session(fixture, f"cmd_approval_{stop}")
            await fixture.on_core(
                lambda: fixture.bank.scripts.extend([[["composition"]], WRITE_CALL_SCRIPT])
            )
            assert (
                await fixture.client.post(
                    path + "/interactions",
                    {"client_message_id": "approval", "text": "update configuration"},
                )
            ).status == 202
            pending = []
            for _ in range(4000):
                pending = (await fixture.client.get("/v1/approvals")).json()["approvals"]
                if pending:
                    break
                await asyncio.sleep(0)
            assert len(pending) == 1
            receipt = (await fixture.client.get(path + "/interactions/approval")).json()["receipt"]
            if stop:
                queue = (await fixture.client.get(path + "/queue")).json()
                response = await fixture.client.post(
                    path + "/control",
                    {
                        "command_id": "cmd_stop_approval",
                        "action": "stop",
                        "target_agent_run_id": receipt["agent_run_id"],
                        "expected_revision": queue["revision"],
                    },
                )
                assert response.status == 200
            else:
                response = await fixture.client.post(
                    "/v1/approvals/" + pending[0]["approval_id"] + "/resolve",
                    {"command_id": "cmd_accept_approval", "approved": True},
                )
                assert response.status == 200, response.body
            await drain(fixture, sid)
            result = (await fixture.client.get(path + "/interactions/approval")).json()["receipt"]
            assert result["run_status"] == ("cancelled" if stop else "stop")
            assert (
                await fixture.on_core(lambda: len(fixture.host.context.approval_waiters._waiters))
                == 0
            )
    finally:
        fixture.close()


@pytest.mark.parametrize("changed_defaults", [False, True])
async def test_core_restart_preserves_input_and_resumes_original_turn(tmp_path, changed_defaults):
    from fixtures.core_api_client import CoreApiVerificationClient
    from morrow.server.app import create_asgi_app
    from morrow.server.composition import make_context_builder
    from morrow.server.host import CoreHost
    from test_stage8_core_api import SERVE_TOKEN

    fixture, sid, path, cell, run_id = await held_fixture(tmp_path)
    before = (await fixture.client.get(path + "/snapshot")).json()
    original = (await fixture.client.get(path + "/interactions/first")).json()["receipt"]
    fixture.close()
    fixture.bank.on_create = None
    fixture.host = CoreHost(make_context_builder(fixture.app, fixture.identity))
    fixture.host.start()
    fixture.client = CoreApiVerificationClient(
        create_asgi_app(fixture.host, auth_token=SERVE_TOKEN), token=SERVE_TOKEN
    )
    try:
        after = (await fixture.client.get(path + "/snapshot")).json()
        assert after["stream_epoch"] != before["stream_epoch"]
        assert after["queue"]["paused"]
        receipt = (await fixture.client.get(path + "/interactions/first")).json()["receipt"]
        assert receipt["turn_id"] == original["turn_id"]
        assert receipt["run_status"] == "needs_recovery"
        assert not await fixture.on_core(lambda: fixture.host.context.chat.drivers)
        status = await fixture.client.get(path + "/recovery/status")
        assert status.status == 200, status.body
        status_body = status.json()
        assert status_body["display_state"] == "resumable"
        assert status_body["owner"] == "chat"
        assert status_body["opaque_target_kind"] == "agent_run"
        assert status_body["opaque_target"] == run_id
        response = await fixture.client.post(
            path + "/recovery", {"command_id": "cmd_discover", "action": "discover"}
        )
        assert response.status == 200, response.body
        assert response.json()["reports"] == []
        assert response.json()["pending_resume"]
        admissions = []

        def observe_resume():
            from morrow.core.capabilities import PermissionPreset, PermissionProfile

            context = fixture.host.context
            if changed_defaults:
                context.chat.permission_profile = PermissionProfile.from_preset(
                    PermissionPreset.AUTO_SANDBOXED
                )
            orchestrator = context.chat.runtimes[sid].orchestrator
            original_resume = orchestrator.resume_recovery

            async def observed():
                admissions.extend(a.confined for a in context.workspaces.coordinator.active)
                async for event in original_resume():
                    yield event

            orchestrator.resume_recovery = observed

        await fixture.on_core(observe_resume)
        response = await fixture.client.post(
            path + "/recovery",
            {
                "command_id": "cmd_recovery_resume",
                "action": "resume",
                "target_agent_run_id": run_id,
            },
        )
        assert response.status == 200, response.body
        await drain(fixture, sid)
        assert admissions == [False]  # The consumed Run keeps its original host capability.
        final = (await fixture.client.get(path + "/interactions/first")).json()["receipt"]
        assert final["turn_id"] == original["turn_id"]
        assert final["user_record_id"] == original["user_record_id"]
        assert final["run_status"] == "stop", final
        assert final["agent_run_id"] == run_id

        def verify():
            journal = fixture.host.context.journal
            assert len(journal.list_session_turns(fixture.workspace_id, sid)) == 1
            assert (
                len(
                    [
                        r
                        for r in journal.load_records(fixture.workspace_id, sid)
                        if r.payload.get("role") == "user"
                    ]
                )
                == 1
            )

        await fixture.on_core(verify)
    finally:
        fixture.close()


@pytest.mark.parametrize("management", [False, True])
async def test_unknown_tool_effect_requires_recovery_decision(tmp_path, management):
    from morrow.core.faults import FaultPoint, OnceFaultInjector
    from test_stage8_core_api import WRITE_CALL_SCRIPT

    fixture = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fixture)
        await fixture.on_core(
            lambda: fixture.bank.scripts.extend([[["composition"]], WRITE_CALL_SCRIPT])
        )
        await fixture.client.post(
            path + "/interactions", {"client_message_id": "effect", "text": "perform update"}
        )
        pending = []
        for _ in range(4000):
            pending = (await fixture.client.get("/v1/approvals")).json()["approvals"]
            if pending:
                break
            await asyncio.sleep(0)
        assert pending

        def inject():
            products = fixture.host.context.chat.runtimes[sid]
            products.persistence.faults = OnceFaultInjector(FaultPoint.HANDLER_AFTER_RETURN)

        await fixture.on_core(inject)
        await fixture.client.post(
            "/v1/approvals/" + pending[0]["approval_id"] + "/resolve",
            {"command_id": "cmd_approve_fault", "approved": True},
        )
        await drain(fixture, sid)
        response = await fixture.client.post(
            path + "/recovery", {"command_id": "cmd_discover_fault", "action": "discover"}
        )
        assert response.status == 200, response.body
        report = response.json()["reports"][-1]
        assert any(item["classification"] == "outcome_unknown" for item in report["items"])
        assert all(
            "retry" not in item["allowed_resolutions"]
            for item in report["items"]
            if item["classification"] == "outcome_unknown"
        )
        status = await fixture.client.get(path + "/recovery/status")
        assert status.status == 200, status.body
        status_body = status.json()
        assert status_body["display_state"] == "unknown_side_effect"
        assert status_body["owner"] == "chat"
        assert status_body["decision_required"]
        assert status_body["checks"]
        resume = await fixture.client.post(
            path + "/recovery",
            {
                "command_id": "cmd_unsafe_resume",
                "action": "resolve",
                "report_id": report["report_id"],
                "resolution": "resume",
            },
        )
        assert resume.status in {400, 409}
        abort = await fixture.client.post(
            "/v1/recovery-management" if management else path + "/recovery",
            {
                "command_id": "cmd_abort_fault",
                **({"session_id": sid, "confirmed": True} if management else {"action": "resolve"}),
                "report_id": report["report_id"],
                "resolution": "abort",
            },
        )
        assert abort.status == 200, abort.body
        assert abort.json()["report"]["status"] == "resolved"
        assert (
            len(
                await fixture.on_core(
                    lambda: fixture.host.context.journal.list_session_turns(
                        fixture.workspace_id, sid
                    )
                )
            )
            == 1
        )
    finally:
        fixture.close()
