"""Core HTTP stop/revoke while a fake native desktop action is still in flight."""

import asyncio
from types import SimpleNamespace

import pytest

from morrow.adapters.computer_use.owner import ComputerDriverOwner
from morrow.adapters.computer_use.process_identity import ProcessBirth
from morrow.application.computer_use import ComputerUseLifecycle
from morrow.core.computer_use import ComputerUsePreflight
from morrow.core.execution import ToolExecutionDisposition, ToolExecutionState
from morrow.testing import FixedClock, FixedIdSource
from test_computer_use_candidates import Driver
from test_computer_use_driver import NOW, _sdk
from test_computer_use_lifecycle import _Lease
from test_computer_use_loop import tool
from test_stage8_chat_permissions import pending_approval
from test_stage8_chat_submission import drain, new_session
from test_stage8_core_api import ServerFixture


@pytest.mark.parametrize("control", ["stop", "revoke"])
async def test_http_control_retains_native_lease_until_effect_settles(tmp_path, control):
    script = [
        tool("discover", "computer_observe", {"operation": "discover"}),
        tool("observe", "computer_observe", {"operation": "window", "target_ref": "ctarget_1"}),
        tool(
            "act",
            "computer_action",
            {"observation_id": "cobs_1", "action": {"type": "click", "element_ref": "celem_1"}},
        ),
        ["finished"],
    ]
    fx = ServerFixture(tmp_path, scripts=[script, script])
    resources = {}
    try:
        sid, path = await new_session(fx)
        settings = (await fx.client.get(path + "/settings")).json()
        assert (
            await fx.client.post(
                path + "/settings",
                {
                    "expected_revision": settings["documents"]["session"]["revision"],
                    "settings": {"permission": "full-access-manual"},
                },
            )
        ).status == 200
        settings = (await fx.client.get(path + "/computer-use/settings")).json()
        assert (
            await fx.client.post(
                path + "/computer-use/settings",
                {
                    "expected_revision": settings["revision"],
                    "settings": {"enabled": True},
                },
            )
        ).status == 200

        def install():
            entered, release, quarantined = asyncio.Event(), asyncio.Event(), asyncio.Event()
            effects = []

            class Native(Driver):
                async def click(self, payload):
                    self.calls.append(("click", None))
                    entered.set()
                    await release.wait()
                    effects.append("effect")
                    return SimpleNamespace(
                        effect=SimpleNamespace(name="CONFIRMED"),
                        delivery=SimpleNamespace(mode=SimpleNamespace(name="FOREGROUND")),
                        error=None,
                        verified=True,
                    )

            class Owner(ComputerDriverOwner):
                async def close_run_session(self, request):
                    quarantined.set()
                    await super().close_run_session(request)

            driver, lease, clock = Native(), _Lease(), FixedClock(NOW)
            clock.value = fx.host.context.journal.now()
            owner = Owner(
                _sdk(),
                FixedIdSource(),
                clock,
                driver_factory=lambda _: driver,
                session_factory=lambda _driver, name: driver,
                lease=lease,
                process_reader=lambda pid: ProcessBirth(1, 0),
            )
            lifecycle = ComputerUseLifecycle(
                lambda: owner,
                lambda: ComputerUsePreflight(status="unavailable", reason="native_unverified"),
                native_verified=True,
            )
            ctx = fx.host.context
            ctx.computer_use = lifecycle
            ctx.chat.computer_selection.lifecycle = lifecycle
            ctx.chat.computer_selection.clock = clock
            resources.update(
                owner=owner,
                lease=lease,
                driver=driver,
                entered=entered,
                release=release,
                quarantined=quarantined,
                effects=effects,
            )

        await fx.on_core(install)
        candidates = (await fx.client.post(path + "/computer-use/candidates", {})).json()
        selected = await fx.client.post(
            path + "/computer-use/selection",
            {
                "candidate_ids": [candidates["candidates"][0]["candidate_id"]],
                "allow_action": True,
                "delivery": "foreground",
            },
        )
        assert selected.status == 200, selected.body
        assert (
            await fx.client.post(
                path + "/interactions",
                {
                    "client_message_id": "desktop.control",
                    "text": "Click my selected window",
                    "computer_selection_id": selected.json()["selection_id"],
                },
            )
        ).status == 202
        approval = await pending_approval(fx)
        assert approval["tool_name"] == "computer_action"
        assert not approval["session_scope_allowed"]
        assert "投递：前台" in "\n".join(approval["preview"])
        assert (
            await fx.client.post(
                "/v1/approvals/" + approval["approval_id"] + "/resolve",
                {
                    "command_id": "cmd_approve_desktop",
                    "approved": True,
                },
            )
        ).status == 200

        async def wait_entered():
            await asyncio.wait_for(resources["entered"].wait(), timeout=5)

        await fx.host.execute_preparation(wait_entered)
        before = (await fx.client.get(path + "/permissions")).json()
        grant = before["grants"][0]
        if control == "stop":
            receipt = (await fx.client.get(path + "/interactions/desktop.control")).json()[
                "receipt"
            ]
            queue = (await fx.client.get(path + "/queue")).json()
            request = {
                "command_id": "cmd_stop_desktop",
                "action": "stop",
                "target_agent_run_id": receipt["agent_run_id"],
                "expected_revision": queue["revision"],
            }
            response = await fx.client.post(path + "/control", request)
        else:
            request = {
                "command_id": "cmd_revoke_desktop",
                "kind": "grant",
                "subject_id": grant["grant_id"],
                "expected_revision": grant["row_version"],
            }
            response = await fx.client.post(path + "/permissions", request)
        assert response.status == 200, response.body

        async def wait_quarantine():
            await asyncio.wait_for(resources["quarantined"].wait(), timeout=5)

        await fx.host.execute_preparation(wait_quarantine)
        assert resources["owner"].quarantined
        assert resources["lease"].held and resources["effects"] == []
        refused = await fx.client.post(path + "/computer-use/candidates", {})
        assert refused.status == 503
        during = (await fx.client.get(path + "/permissions")).json()
        assert during["snapshot"] == before["snapshot"]
        assert during["desktop_runtime"] == {
            "scope": "local_host",
            "state": "quarantined",
            "native_pending": True,
            "unknown_actions": 1,
        }
        if control == "revoke":
            assert during["grants"][0]["status"] == "revoked"
        await fx.on_core(lambda: resources["release"].set())
        await drain(fx, sid)
        assert resources["effects"] == ["effect"] and not resources["lease"].held
        after = (await fx.client.get(path + "/permissions")).json()
        assert after["desktop_runtime"] == {
            "scope": "local_host",
            "state": "idle",
            "native_pending": False,
            "unknown_actions": 1,
        }
        assert after["snapshot"] == before["snapshot"]
        rows = await fx.on_core(
            lambda: fx.host.context.journal.list_session_executions(fx.workspace_id, sid)
        )
        action = next(row for row in rows if row.tool_name == "computer_action")
        assert action.state is ToolExecutionState.CLOSED
        assert action.disposition is ToolExecutionDisposition.UNKNOWN
        assert not action.result_envelope.visual_refs
        assert [name for name, _ in resources["driver"].calls].count("click") == 1
        assert [name for name, _ in resources["driver"].calls].count("end_session") == 1
        assert (await fx.client.get(path + "/interactions/desktop.control")).json()["receipt"][
            "run_status"
        ] == ("cancelled" if control == "stop" else "stop")
    finally:
        if resources:
            await fx.on_core(lambda: resources["release"].set())
            await drain(fx, sid)
        fx.close()
