"""Production Chat routes: scoped revocation, immutable snapshots and explicit Host consent."""

import asyncio

import pytest

from test_stage8_chat_submission import drain, new_session
from test_stage8_core_api import WRITE_CALL_SCRIPT, ServerFixture


async def pending_approval(fx):
    for _ in range(4000):
        pending = (await fx.client.get("/v1/approvals")).json()["approvals"]
        if pending:
            return pending[0]
        await asyncio.sleep(0)
    raise AssertionError("no pending approval")


async def test_failed_preparation_does_not_leave_host_consent_armed(tmp_path):
    from morrow.core.models import ModelRef

    fx = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fx)
        assert (
            await fx.client.post(
                path + "/settings",
                {
                    "expected_revision": 0,
                    "settings": {"permission": "full-access-manual"},
                },
            )
        ).status == 200

        def reject():
            runtime = fx.host.context.chat.runtime(
                sid, ModelRef(provider_id="fake-provider", model_id="m1")
            )

            def fail(**_):
                assert runtime.session.pending_full_access_grant
                raise ValueError("synthetic preparation failure")

            runtime.orchestrator.preparation.prepare_new = fail
            return runtime

        runtime = await fx.on_core(reject)
        sent = await fx.client.post(
            path + "/interactions",
            {
                "client_message_id": "host-preparation-failure",
                "text": "hello",
                "allow_unconfined_host": True,
            },
        )
        assert sent.status == 202, sent.body
        await drain(fx, sid)
        assert not runtime.session.pending_full_access_grant
        assert not (await fx.client.get(path + "/permissions")).json()["grants"]
    finally:
        fx.close()


async def test_host_consent_is_per_input_and_grant_revoke_is_scoped_occ_idempotent(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fx)
        _, other = await new_session(fx, "cmd_other")
        response = await fx.client.post(
            path + "/interactions",
            {
                "client_message_id": "bad-host",
                "text": "hello",
                "allow_unconfined_host": True,
            },
        )
        assert response.status == 400
        assert (
            await fx.client.post(
                path + "/settings",
                {
                    "expected_revision": 0,
                    "settings": {"permission": "full-access-manual"},
                },
            )
        ).status == 200
        for i, allowed in enumerate((True, False)):
            response = await fx.client.post(
                path + "/interactions",
                {
                    "client_message_id": f"host-{i}",
                    "text": "hello",
                    "allow_unconfined_host": allowed,
                },
            )
            assert response.status == 202, response.body
            await drain(fx, sid)
            response = await fx.client.get(path + "/permissions")
            assert response.status == 200, response.body
            view = response.json()
            assert view["snapshot"]["permission_preset"] == "full-access-manual"
            assert len(view["grants"]) == int(allowed)
            if allowed:
                grant = view["grants"][0]
                run_id = view["snapshot"]["agent_run_id"]
        body = {
            "kind": "grant",
            "subject_id": grant["grant_id"],
            "expected_revision": grant["row_version"],
            "command_id": "cmd_revoke_host",
        }
        assert (await fx.client.post(other + "/permissions", body)).status == 404
        first, duplicate = await asyncio.gather(
            fx.client.post(path + "/permissions", body), fx.client.post(path + "/permissions", body)
        )
        assert first.status == duplicate.status == 200, (first.body, duplicate.body)
        assert {first.json()["disposition"], duplicate.json()["disposition"]} == {
            "accepted",
            "replay",
        }
        stale = await fx.client.post(
            path + "/permissions", {**body, "command_id": "cmd_stale_host"}
        )
        assert stale.status == 409, stale.body
        assert (await fx.client.get(path + f"/permissions?run_id={run_id}")).json()["grants"][0][
            "status"
        ] == "revoked"
        assert (await fx.client.get(other + f"/permissions?run_id={run_id}")).status == 404
        assert "credential_ref" not in response.body.decode()
    finally:
        fx.close()


@pytest.mark.parametrize("kind", ["session_scope", "grant"])
async def test_revoke_after_approval_consumption_blocks_handler_without_mutating_snapshot(
    tmp_path, kind
):
    import json

    from morrow.application.chat_permissions import revoke_permission
    from morrow.core.faults import FaultPoint, OnceFaultInjector
    from morrow.core.models import AssistantMessage, FunctionToolCall
    from morrow.server.permission_routes import PermissionRevokeRequest

    fx = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fx)
        script = WRITE_CALL_SCRIPT
        if kind == "grant":
            script = [
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(
                            id="host-call",
                            name="bash",
                            arguments=json.dumps({"command": "touch host-must-not-run"}),
                        ),
                    )
                ),
                ["finished"],
            ]
            assert (
                await fx.client.post(
                    path + "/settings",
                    {
                        "expected_revision": 0,
                        "settings": {"permission": "full-access-manual"},
                    },
                )
            ).status == 200
        fx.bank.on_create = lambda _: setattr(fx.bank.providers[-1], "responses", script)
        sent = await fx.client.post(
            path + "/interactions",
            {
                "client_message_id": "safety-boundary",
                "text": "perform operation",
                "allow_unconfined_host": kind == "grant",
            },
        )
        assert sent.status == 202, sent.body
        approval = await pending_approval(fx)
        before = (await fx.client.get(path + "/permissions")).json()["snapshot"]

        def install():
            context = fx.host.context

            def revoke(_):
                if kind == "grant":
                    grant = context.api.get_grant(before["permission"]["grant_id"])
                    subject, revision = grant.grant_id, grant.row_version
                else:
                    subject, revision = approval["approval_id"], 0
                revoke_permission(
                    context,
                    sid,
                    PermissionRevokeRequest(
                        kind=kind,
                        subject_id=subject,
                        expected_revision=revision,
                        command_id="cmd_boundary_revoke",
                    ),
                )

            injector = OnceFaultInjector(FaultPoint.HANDLER_BEFORE_ENTER, action=revoke)
            context.chat.runtimes[sid].persistence.faults = injector
            return injector

        injector = await fx.on_core(install)
        resolved = await fx.client.post(
            f"/v1/approvals/{approval['approval_id']}/resolve",
            {
                "approved": True,
                "decision": "allow_once" if kind == "grant" else "allow_session",
                "command_id": "cmd_boundary_allow",
            },
        )
        assert resolved.status == 200, resolved.body
        await drain(fx, sid)
        assert injector.fired
        assert (await fx.client.get(path + "/permissions")).json()["snapshot"] == before
        executions = await fx.on_core(
            lambda: fx.host.context.journal.list_session_executions(fx.workspace_id, sid)
        )
        assert len(executions) == 1
        assert executions[0].disposition.value == "denied"
        assert not (fx.workspace_dir / "host-must-not-run").exists()
    finally:
        fx.close()


async def test_session_scope_revocation_preserves_decisions_and_requires_a_new_approval(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fx)
        # Force each new runtime Provider to offer the same exact tool scope.
        fx.bank.on_create = lambda _: setattr(fx.bank.providers[-1], "responses", WRITE_CALL_SCRIPT)
        sent = await fx.client.post(
            path + "/interactions", {"client_message_id": "scope-1", "text": "configure"}
        )
        assert sent.status == 202, sent.body
        approval = await pending_approval(fx)
        decision = {"approved": True, "decision": "allow_session", "command_id": "cmd_allow_scope"}
        url = f"/v1/approvals/{approval['approval_id']}/resolve"
        one, two = await asyncio.gather(
            fx.client.post(url, decision), fx.client.post(url, decision)
        )
        assert one.status == two.status == 200
        await drain(fx, sid)
        view = (await fx.client.get(path + "/permissions")).json()
        scope = view["session_scopes"]["items"][0]
        assert scope["scope"].startswith("session:")
        body = {
            "kind": "session_scope",
            "subject_id": scope["approval_id"],
            "expected_revision": scope["revision"],
            "command_id": "cmd_revoke_scope",
        }
        one, two = await asyncio.gather(
            fx.client.post(path + "/permissions", body), fx.client.post(path + "/permissions", body)
        )
        assert one.status == two.status == 200, (one.body, two.body)
        assert (
            await fx.client.post(path + "/permissions", {**body, "command_id": "cmd_scope_stale"})
        ).status == 409
        assert not (await fx.client.get(path + "/permissions")).json()["session_scopes"]["items"]

        def evidence():
            j = fx.host.context.journal
            original = j.get_approval(fx.workspace_id, approval["approval_id"])
            assert original.resolution.value == "approved"
            assert original.consumed_at is not None
            assert not j.session_scopes.active(fx.workspace_id, sid, original)
            assert (
                j.find_session_scope_approval(
                    fx.workspace_id, session_id=sid, granted_scope=scope["scope"]
                )
                is None
            )

        await fx.on_core(evidence)
        sent = await fx.client.post(
            path + "/interactions", {"client_message_id": "scope-2", "text": "configure again"}
        )
        assert sent.status == 202, sent.body
        again = await pending_approval(fx)
        assert again["approval_id"] != approval["approval_id"]
        assert (
            await fx.client.post(
                f"/v1/approvals/{again['approval_id']}/resolve",
                {**decision, "command_id": "cmd_new_scope"},
            )
        ).status == 200
        await drain(fx, sid)
        renewed = (await fx.client.get(path + "/permissions")).json()["session_scopes"]["items"]
        assert renewed[0]["revision"] == 1
    finally:
        fx.close()
