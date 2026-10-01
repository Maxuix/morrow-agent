"""HTTP selection -> durable input -> ordinary prepared AgentLoop, with fake SDK."""

import json
from datetime import timedelta

import pytest

from morrow.application.computer_use import ComputerUseLifecycle
from morrow.core.computer_use import ComputerUsePreflight
from morrow.core.models import AssistantMessage, FunctionToolCall
from morrow.core.permissions import CapabilityName
from test_computer_use_candidates import Driver, owner_for
from test_stage8_chat_submission import drain, new_session
from test_stage8_core_api import ServerFixture


@pytest.mark.parametrize(
    "mode",
    ["complete", "cli_revoke", "queued_expiry", "queued_claim", "queued_restart", "rollback"],
)
async def test_http_selection_is_bound_to_one_new_chat_run(tmp_path, mode):
    script = [
        AssistantMessage(
            tool_calls=(
                FunctionToolCall(
                    id="call_discover",
                    name="computer_observe",
                    arguments=json.dumps({"operation": "discover"}),
                ),
            )
        ),
        ["selected window found"],
    ]
    fx = ServerFixture(tmp_path, scripts=[script, script])
    resources = {}
    try:
        sid, path = await new_session(fx)
        setting = (await fx.client.get(path + "/settings")).json()
        assert (
            await fx.client.post(
                path + "/settings",
                {
                    "expected_revision": setting["documents"]["session"]["revision"],
                    "settings": {"permission": "full-access-manual"},
                },
            )
        ).status == 200
        setting = (await fx.client.get(path + "/computer-use/settings")).json()
        assert (
            await fx.client.post(
                path + "/computer-use/settings",
                {"expected_revision": setting["revision"], "settings": {"enabled": True}},
            )
        ).status == 200

        def install():
            ctx = fx.host.context
            driver = Driver()
            owner, lease, clock, sessions = owner_for(driver)
            clock.value = ctx.journal.now()
            lifecycle = ComputerUseLifecycle(
                lambda: owner,
                lambda: ComputerUsePreflight(status="unavailable", reason="native_unverified"),
                native_verified=True,
            )
            ctx.computer_use = lifecycle
            ctx.chat.computer_selection.lifecycle = lifecycle
            ctx.chat.computer_selection.clock = clock
            ctx.chat.computer_settings.preflight = lambda _: ComputerUsePreflight(
                status="unavailable", reason="native_unverified"
            )
            if mode.startswith("queued_"):
                ctx.chat.interactions.records.pause(sid, True)
            resources.update(
                owner=owner, driver=driver, lease=lease, clock=clock, sessions=sessions
            )

        await fx.on_core(install)
        catalog = (await fx.client.post(path + "/computer-use/candidates", {})).json()
        selected = await fx.client.post(
            path + "/computer-use/selection",
            {"candidate_ids": [catalog["candidates"][0]["candidate_id"]], "delivery": "foreground"},
        )
        assert selected.status == 200, selected.body
        selection_id = selected.json()["selection_id"]
        body = {
            "client_message_id": "desktop.1",
            "text": "Find my selected window",
            "computer_selection_id": selection_id,
        }
        if mode == "rollback":
            from morrow.core.application import ApplicationError, ApplicationErrorCode

            def inject():
                journal = fx.host.context.journal
                original = journal.transact

                def fail(operation):
                    raise ApplicationError(
                        ApplicationErrorCode.CONFLICT, "injected transaction failure"
                    )

                journal.transact = fail
                return original

            original = await fx.on_core(inject)
            failed = await fx.client.post(
                path + "/interactions", body | {"client_message_id": "failed.1"}
            )
            assert failed.status == 409
            await fx.on_core(lambda: setattr(fx.host.context.journal, "transact", original))
            assert (
                await fx.on_core(
                    lambda: fx.host.context.chat.computer_selection._selections[sid].claimed_key
                )
                is None
            )
        submitted = await fx.client.post(path + "/interactions", body)
        assert submitted.status == 202, submitted.body
        repeated = await fx.client.post(path + "/interactions", body)
        assert repeated.status == 202 and repeated.json()["receipt"]["disposition"] == "replay"
        assert (
            await fx.client.post(path + "/interactions", body | {"client_message_id": "desktop.2"})
        ).status == 409
        if mode == "queued_expiry":
            resources["clock"].value += timedelta(seconds=31)
        if mode == "queued_restart":
            fx.host.stop()
            fx.host.start()
            assert (
                await fx.client.post(
                    path + "/interactions", body | {"client_message_id": "restarted.1"}
                )
            ).status == 409
        if mode.startswith("queued_"):

            def resume():
                fx.host.context.chat.interactions.records.pause(sid, False)
                fx.host.context.chat.interactions.wake(sid)

            await fx.on_core(resume)
        await drain(fx, sid)
        receipt = (await fx.client.get(path + "/interactions/desktop.1")).json()["receipt"]
        grants = await fx.on_core(
            lambda: fx.host.context.journal.list_capability_grants(fx.workspace_id)
        )
        requests = [request for provider in fx.bank.providers for request in provider.stream_calls]
        if mode in {"queued_expiry", "queued_restart"}:
            assert not grants and not requests and resources["sessions"] == []
            assert receipt["status"] in {"blocked", "settled"}
            assert receipt["run_status"] != "stop"
        else:
            assert receipt["status"] == "settled", receipt
            assert receipt["run_status"] == "stop", receipt
            assert len(grants) == 1 and grants[0].capabilities == (
                CapabilityName.COMPUTER_USE_HOST,
            )
            assert grants[0].computer_use_scope.schema_version == 2
            assert len(grants[0].computer_use_scope.windows) == 1
            assert len(resources["sessions"]) == 1 and not resources["lease"].held
            executions = await fx.on_core(
                lambda: fx.host.context.journal.list_session_executions(fx.workspace_id, sid)
            )
            assert len(executions) == 1 and executions[0].tool_name == "computer_observe"
            assert executions[0].grant_id == grants[0].grant_id
            assert len(requests) == 2
            assert selection_id not in str(requests)
            assert "computer_observe" in str(requests[0])
            assert "computer_action" not in str(requests[0])
            assert "selected window found" not in str(requests[0])
            replay = await fx.client.post(path + "/interactions", body)
            assert replay.status == 202 and replay.json()["receipt"]["disposition"] == "replay"
            assert len(resources["sessions"]) == 1
            if mode in {"complete", "cli_revoke"}:
                from typer.testing import CliRunner

                from morrow.interfaces.cli import app as cli_app

                before = (await fx.client.get(path + "/permissions")).json()
                grant_view = before["grants"][0]
                summary = grant_view["computer_use"]
                assert summary == {
                    "apps": ["com.example.Notes"],
                    "window_scope": "selected_windows",
                    "window_count": 1,
                    "operations": ["observe"],
                    "delivery": "foreground",
                    "image_share": "none",
                }
                assert "cwin_" not in json.dumps(summary) and "4242" not in json.dumps(summary)
                common = ["--workspace-id", fx.workspace_id, "--state-root", str(fx.state_root)]
                runner = CliRunner()
                shown = runner.invoke(
                    cli_app, ["grant", "show", grants[0].grant_id, "--summary", *common]
                )
                assert shown.exit_code == 0, shown.output
                assert "1 个明确选中窗口" in shown.output and "仅观察" in shown.output
                assert "cwin_" not in shown.output and "ctarget_" not in shown.output
                _, other = await new_session(fx, "cmd_other_for_revoke")
                revoke = {
                    "kind": "grant",
                    "subject_id": grants[0].grant_id,
                    "expected_revision": grant_view["row_version"],
                    "command_id": "cmd_revoke_desktop",
                }
                assert (await fx.client.post(other + "/permissions", revoke)).status == 404
                if mode == "cli_revoke":
                    revoked = runner.invoke(
                        cli_app,
                        [
                            "grant",
                            "revoke",
                            grants[0].grant_id,
                            "--expected-row-version",
                            str(grant_view["row_version"]),
                            "--command-id",
                            "cmd_revoke_desktop",
                            *common,
                        ],
                    )
                    assert revoked.exit_code == 0, revoked.output
                else:
                    assert (await fx.client.post(path + "/permissions", revoke)).status == 200
                    assert (await fx.client.post(path + "/permissions", revoke)).json()[
                        "disposition"
                    ] == "replay"
                after = (await fx.client.get(path + "/permissions")).json()
                assert after["grants"][0]["status"] == "revoked"
                assert after["grants"][0]["computer_use"] == summary
                assert after["snapshot"] == before["snapshot"]
                assert len(resources["sessions"]) == 1 and len(requests) == 2
    finally:
        fx.close()


def test_chat_selection_wire_preserves_ordinary_input_and_rejects_wrong_intents():
    from pydantic import ValidationError

    from morrow.core.domain import canonical_json_bytes
    from morrow.core.interactions import InteractionRequest

    old = InteractionRequest(client_message_id="ordinary.1", text="hello")
    original_bytes = (
        b'{"allow_unconfined_host":false,"attachments":[],"client_message_id":"ordinary.1",'
        b'"intent":"send","settings":{},"target_agent_run_id":null,"text":"hello","workflow":null}'
    )
    assert canonical_json_bytes(old.model_dump(mode="json")) == original_bytes
    for fields in [
        {"computer_selection_id": "arun_wrong"},
        {"computer_selection_id": "cselection_one", "allow_unconfined_host": True},
        {
            "computer_selection_id": "cselection_one",
            "intent": "steer",
            "target_agent_run_id": "arun_target",
        },
    ]:
        with pytest.raises(ValidationError):
            InteractionRequest(client_message_id="invalid.1", text="hello", **fields)
