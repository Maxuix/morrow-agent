"""Task retention and recovery management preserve Core state and Session ownership."""

from morrow.core.artifacts import ArtifactKind
from morrow.core.recovery import RecoveryReport, RecoveryReportStatus
from test_stage8_core_api import ServerFixture, create_session_and_task


async def test_task_abandon_and_artifact_retention_use_original_receipts(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, tid, version = await create_session_and_task(fx.client)
        body = {
            "action": "abandon",
            "command_id": "cmd_abandon",
            "expected_row_version": version,
            "confirmed": True,
        }
        result = await fx.client.post("/v1/task-actions/" + tid, body)
        assert result.status == 200, result.body
        assert result.json()["task"]["status"] == "abandoned"
        assert (await fx.client.post("/v1/task-actions/" + tid, body)).json()[
            "disposition"
        ] == "replay"
        assert (
            await fx.client.post(
                "/v1/task-actions/" + tid, {**body, "command_id": "cmd_stale", "action": "cancel"}
            )
        ).status == 409

        def publish():
            return fx.host.context.api.artifacts.publish_bytes(
                b"Offline management artifact",
                kind=ArtifactKind.TASK_SUMMARY,
                session_id=sid,
                task_run_id=tid,
            )

        artifact = await fx.on_core(publish)
        route = "/v1/artifacts/" + artifact.artifact_id + "/retention"
        body = {
            "action": "pin",
            "command_id": "cmd_pin",
            "expected_row_version": artifact.row_version,
            "confirmed": True,
        }
        pinned = await fx.client.post(route, body)
        assert pinned.status == 200, pinned.body
        assert pinned.json()["artifact"]["retention"] == "pinned"
        assert (await fx.client.post(route, body)).json()["disposition"] == "replay"
        stale = {**body, "action": "release", "command_id": "cmd_release"}
        assert (await fx.client.post(route, stale)).status == 409
        release = await fx.client.post(
            route, {**stale, "expected_row_version": pinned.json()["artifact"]["row_version"]}
        )
        assert release.status == 200 and release.json()["artifact"]["retention"] == "standard"
        assert (await fx.client.post("/v1/workspaces/ws_unknown" + route[3:], body)).status == 403
    finally:
        fx.close()


async def test_recovery_pages_scope_and_decision_without_starting_provider(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, _, _ = await create_session_and_task(fx.client)
        other, _, _ = await create_session_and_task(fx.client)

        def seed():
            for i in range(21):
                fx.host.context.journal.put_report(
                    fx.workspace_id,
                    RecoveryReport(
                        report_id=f"rrp_page_{i}",
                        status=RecoveryReportStatus.OPEN
                        if i == 0
                        else RecoveryReportStatus.RESOLVED,
                        workspace_id=fx.workspace_id,
                        session_id=sid,
                    ),
                )

        await fx.on_core(seed)
        first = (await fx.client.get("/v1/recovery-management?session_id=" + sid)).json()
        second = (
            await fx.client.get("/v1/recovery-management?session_id=" + sid + "&page=1")
        ).json()
        assert len(first["reports"]) == 20 and len(second["reports"]) == 1
        assert len({r["report_id"] for r in first["reports"] + second["reports"]}) == 21
        before = len(fx.bank.providers)
        body = {
            "session_id": sid,
            "report_id": "rrp_page_0",
            "resolution": "quarantine",
            "command_id": "cmd_quarantine",
            "confirmed": True,
        }
        result = await fx.client.post("/v1/recovery-management", body)
        assert result.status == 200, result.body
        assert (
            result.json()["report"]["status"] == "quarantined"
            and not result.json()["execution_started"]
        )
        assert len(fx.bank.providers) == before
        assert (await fx.client.post("/v1/recovery-management", body)).json()[
            "disposition"
        ] == "replay"
        assert (
            await fx.client.get(
                "/v1/recovery-management?session_id=" + other + "&identity=rrp_page_0"
            )
        ).status == 404
        assert (
            await fx.client.post(
                "/v1/recovery-management",
                {**body, "command_id": "cmd_wrong_session", "session_id": other},
            )
        ).status == 404
        assert (
            await fx.client.post(
                "/v1/recovery-management",
                {**body, "command_id": "cmd_retry", "resolution": "retry"},
            )
        ).status == 400
    finally:
        fx.close()


async def test_grant_create_inspect_revoke_matches_cli_and_warning_binding(tmp_path):
    from datetime import timedelta

    from morrow.core.domain import AgentRunSnapshot, DurableAgentRun, DurableTurn, sha256_digest
    from morrow.core.models import ModelRef
    from test_stage4_permissions import FULL_ACCESS_PROFILE_DIGEST

    fx = ServerFixture(tmp_path)
    try:
        sid, tid, _ = await create_session_and_task(fx.client)

        def seed():
            c = fx.host.context
            c.journal.create_turn(
                fx.workspace_id,
                DurableTurn(
                    turn_id="turn_grant_gui",
                    session_id=sid,
                    task_run_id=tid,
                    client_message_id="grant-fixture",
                ),
            )
            c.journal.create_agent_run(
                fx.workspace_id,
                DurableAgentRun(
                    agent_run_id="arun_grant_gui",
                    turn_id="turn_grant_gui",
                    session_id=sid,
                    snapshot=AgentRunSnapshot(
                        model=ModelRef(provider_id="fake-provider", model_id="m1"),
                        provider_id="fake-provider",
                        run_policy_digest=sha256_digest("policy"),
                        tool_schema_digest=sha256_digest("tools"),
                        permission_profile_digest=FULL_ACCESS_PROFILE_DIGEST,
                        runtime_instance_id="fixture",
                    ),
                ),
            )
            return (c.journal.now() + timedelta(minutes=15)).isoformat()

        expiry = await fx.on_core(seed)
        preview = (await fx.client.get("/v1/grant-management")).json()
        body = {
            "command_id": "cmd_grant_gui",
            "action": "create",
            "task_run_id": tid,
            "agent_run_id": "arun_grant_gui",
            "reason": "Explicit validation of one foreground run",
            "preview_digest": preview["preview_digest"],
            "expires_at": expiry,
            "confirmed": True,
        }
        wrong = await fx.client.post("/v1/grant-management", {**body, "preview_digest": "a" * 64})
        assert wrong.status == 400
        created = await fx.client.post("/v1/grant-management", body)
        assert created.status == 200, created.body
        grant = created.json()["grant"]
        assert grant["capabilities"] == ["unconfined_host_process"]
        assert (await fx.client.post("/v1/grant-management", body)).json()[
            "disposition"
        ] == "replay"
        assert (
            await fx.client.post(
                "/v1/grant-management", {**body, "command_id": "cmd_duplicate_grant"}
            )
        ).status == 409
        shown = (await fx.client.get("/v1/grant-management?identity=" + grant["grant_id"])).json()[
            "grant"
        ]
        assert shown["grant_id"] == grant["grant_id"] and shown["reason"] == body["reason"]
        assert (
            len(
                (await fx.client.get("/v1/grant-management?agent_run_id=arun_grant_gui")).json()[
                    "grants"
                ]
            )
            == 1
        )
        revoke = {
            "command_id": "cmd_revoke_gui",
            "action": "revoke",
            "grant_id": grant["grant_id"],
            "expected_row_version": 1,
            "reason": "Validation complete",
            "confirmed": True,
        }
        assert (
            await fx.client.post("/v1/grant-management", {**revoke, "expected_row_version": 9})
        ).status == 409
        result = await fx.client.post("/v1/grant-management", revoke)
        assert result.status == 200 and result.json()["grant"]["revoked_at"]
        assert (await fx.client.post("/v1/grant-management", revoke)).json()[
            "disposition"
        ] == "replay"
        assert (await fx.client.get("/v1/workspaces/ws_unknown/grant-management")).status == 403
    finally:
        fx.close()
