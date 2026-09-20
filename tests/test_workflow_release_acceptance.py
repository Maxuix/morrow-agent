"""Subplan 8 release-acceptance tests: freeze, upgrade, and T01–T25 gaps."""

import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from fixtures.core_api_client import CoreApiVerificationClient
from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.state.operational import OperationalStore
from morrow.application.backup import OperationalBackupService
from morrow.bootstrap import build_application
from morrow.core.agent_runs import ProviderCapabilities
from morrow.core.capabilities import PermissionProfile
from morrow.core.workflows.planning import PlanWorkflowRequest
from morrow.server.app import create_asgi_app
from morrow.server.composition import make_context_builder
from morrow.server.host import CoreHost
from morrow.testing import ScriptedModelProvider
from test_stage7_serial_scheduler import ScriptBank
from test_stage8_attachment_api import uploaded
from test_stage8_chat_submission import new_session
from test_stage8_core_api import SERVE_TOKEN, ServerFixture
from test_workflow_plan_admission import _planned
from test_workflow_task_planning import request, spec

FROZEN_TASK_WORKFLOW = {
    "schema": 1,
    "planning": True,
    "start": True,
    "control": True,
    "pause": True,
    "change": True,
    "apply_change": True,
    "resume": True,
    "repair": True,
}


@pytest.mark.asyncio
async def test_capabilities_advertise_frozen_task_workflow_shape(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        caps = (await fx.client.get("/v1/capabilities")).json()
        assert caps["protocol_version"] == 1
        assert caps["interaction_protocol_version"] == 1
        assert caps["task_workflow"] == FROZEN_TASK_WORKFLOW
        assert caps["features"]["explicit_workflow"]["available"] is True
        assert caps["features"]["agent_presets"]["available"] is True
        assert caps["features"]["agent_quick_save"]["available"] is True
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_start_is_refused_while_generation_is_in_flight(tmp_path):
    fx, sid, root, view = await _planned(tmp_path, "work")
    try:
        execution = view["execution"]
        assert "start" in view["allowed_actions"]

        def begin_revise():
            return fx.host.context.chat.planning.begin(
                PlanWorkflowRequest.model_validate(
                    request(
                        sid,
                        command_id="cmd_revise_inflight",
                        operation="revise",
                        base_draft_version=execution["draft_version"],
                    )
                )
            )

        await fx.host.execute_command(begin_revise)
        blocked = (await fx.client.get(root + "/task-plan")).json()
        assert "start" not in blocked["allowed_actions"]
        started = await fx.client.post(
            root + "/task-plan/start",
            {
                "command_id": "cmd_start_during_generate",
                "session_id": sid,
                "draft_id": execution["draft_id"],
                "draft_version": execution["draft_version"],
                "execution_digest": execution["digest"],
                "action_source": "button",
                "interaction_id": "message1",
            },
        )
        assert started.status == 503, started.body
        assert started.json()["error"]["code"] == "busy"
        assert "generation" in started.json()["error"]["message"].lower()

        def facts():
            return fx.host.context.journal._backend.read_one(
                "SELECT count(*) FROM workflow_runs", ()
            )[0]

        assert await fx.on_core(facts) == 0
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_image_attachment_fails_before_planning_provider_call(tmp_path):
    fx = ServerFixture(tmp_path, scripts=[[[json.dumps(spec("work"))]]])
    try:
        sid, root = await new_session(fx)
        output = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(output, format="PNG")
        row, _path = await uploaded(fx, sid, output.getvalue(), mime="image/png", key="img.1")
        providers_before = len(fx.bank.providers)
        result = await fx.client.post(
            root + "/task-plan",
            request(sid, attachments=[row["reference"]]),
        )
        assert result.status == 400, result.body
        assert result.json()["error"]["code"] == "invalid"
        assert "image" in result.json()["error"]["message"].lower()
        assert len(fx.bank.providers) == providers_before
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_task_plan_rejects_cross_workspace_and_cross_session(tmp_path):
    fx, sid, root, _view = await _planned(tmp_path, "work")
    try:
        other, _other_root = await new_session(fx, "cmd_other")
        foreign = await fx.client.post(
            root.replace(fx.workspace_id, "ws_other") + "/task-plan",
            request(sid, command_id="cmd_foreign_ws"),
        )
        assert foreign.status == 403, foreign.body
        mismatch = await fx.client.post(
            root + "/task-plan",
            request(other, command_id="cmd_session_mismatch"),
        )
        assert mismatch.status == 404, mismatch.body
        start = await fx.client.post(
            root + "/task-plan/start",
            {
                "command_id": "cmd_foreign_start",
                "session_id": other,
                "draft_id": "wdraft_missing",
                "draft_version": 1,
                "execution_digest": "e" * 64,
                "action_source": "button",
                "interaction_id": "message1",
            },
        )
        assert start.status == 404, start.body
    finally:
        fx.close()


class AlwaysPlanBank:
    """Recovered sessions on an upgraded store consume Providers before the test journey."""

    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.providers: list[ScriptedModelProvider] = []
        self.scripts: list = []

    def __call__(self, config, credential) -> ScriptedModelProvider:
        provider = ScriptedModelProvider([[self.payload]])
        self.providers.append(provider)
        return provider


def open_upgraded_host(state, tmp_path, scripts=(), bank=None, workspace_id="ws_1"):
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    app = build_application(state_root=state, credentials=MemoryCredentialStore())
    identity = app.workspace_service.relink(workspace_id, project)
    bank = bank or ScriptBank([["session composition"]] + list(scripts))
    app.registry.register(
        "fake-adapter",
        bank,
        capabilities=ProviderCapabilities(
            tool_protocol="openai_function", multiple_tool_calls=True
        ),
    )
    app.credentials.set("provider:fake-provider:test", "synthetic-fixture")
    host = CoreHost(make_context_builder(app, identity, permission_profile=PermissionProfile()))
    host.start()
    client = CoreApiVerificationClient(
        create_asgi_app(host, auth_token=SERVE_TOKEN, ws_ping_seconds=7200),
        token=SERVE_TOKEN,
    )
    fx = SimpleNamespace(
        app=app,
        host=host,
        client=client,
        workspace_id=identity.workspace_id,
        bank=bank,
        identity=identity,
    )
    fx.close = lambda: host.stop()
    fx.on_core = host.execute_query
    return fx


def _backup_restore(state_root, tmp_path, name):
    store = OperationalStore(state_root)
    backup = OperationalBackupService(store)
    created = backup.create(name)
    bundle = store.layout.backups_dir / created.bundle_name
    verified = backup.verify(bundle)
    assert verified.ok, verified.issues
    restored = tmp_path / f"restored-{name}"
    report = backup.restore(bundle, restored)
    assert report.restored, report.issues
    return restored


@pytest.mark.asyncio
async def test_backup_restore_keeps_drafts_and_does_not_auto_start(tmp_path):
    from test_stage8_attachments import upload

    fx = ServerFixture(tmp_path, scripts=[[[json.dumps(spec("work"))]]])
    try:
        sid, root = await new_session(fx)

        async def prepare():
            return await upload(
                fx.host.context.chat.attachments,
                b"Planning notes must survive backup and restore.",
                sid=sid,
            )

        row = await fx.host.execute_preparation(prepare)
        planned = await fx.client.post(
            root + "/task-plan",
            request(sid, attachments=[row["reference"]]),
        )
        assert planned.status == 200 and planned.json()["status"] == "succeeded", planned.body
        sid2, _root2 = await new_session(fx, "cmd_inflight")

        def begin_inflight():
            return fx.host.context.chat.planning.begin(
                PlanWorkflowRequest.model_validate(request(sid2, command_id="cmd_inflight_plan"))
            )

        inflight = await fx.host.execute_command(begin_inflight)
        assert inflight.operation.status in {"running", "queued"}

        def snapshot():
            journal = fx.host.context.journal
            backend = journal._backend
            view = fx.host.context.chat.admission.view(sid)
            return {
                "runs": backend.read_one("SELECT count(*) FROM workflow_runs", ())[0],
                "draft_id": view["draft"].draft.draft_id,
                "draft_version": view["draft"].draft.row_version,
                "operation_id": inflight.operation.planning_operation_id,
                "workspace_id": fx.workspace_id,
            }

        before = await fx.on_core(snapshot)
        assert before["runs"] == 0
        workspace_id = before["workspace_id"]
        state_root = fx.state_root
    finally:
        fx.close()

    restored = _backup_restore(state_root, tmp_path, "task-plan-matrix")
    fx2 = open_upgraded_host(
        restored,
        tmp_path / "restored-host",
        bank=AlwaysPlanBank(json.dumps(spec("work"))),
        workspace_id=workspace_id,
    )
    try:
        view = (
            await fx2.client.get(f"/v1/workspaces/{workspace_id}/sessions/{sid}/task-plan")
        ).json()
        assert view["draft"]["draft"]["draft_id"] == before["draft_id"]
        assert view["draft"]["draft"]["row_version"] == before["draft_version"]
        assert "start" in view["allowed_actions"]

        def after():
            journal = fx2.host.context.journal
            backend = journal._backend
            operation = journal.workflows.planning.operation(
                workspace_id, sid2, before["operation_id"]
            )
            return {
                "runs": backend.read_one("SELECT count(*) FROM workflow_runs", ())[0],
                "operation_status": operation.status,
                "operation_error": operation.error_code,
                "plan_refs": backend.read_one(
                    "SELECT COALESCE(SUM(json_array_length(artifact_ids_json)), 0) "
                    "FROM workflow_planning_bindings",
                    (),
                )[0],
            }

        restored_facts = await fx2.on_core(after)
        assert restored_facts["runs"] == 0
        assert restored_facts["operation_status"] == "failed"
        assert restored_facts["operation_error"] == "needs_recovery"
        assert restored_facts["plan_refs"] >= 1
    finally:
        fx2.close()
    fx3 = open_upgraded_host(
        restored,
        tmp_path / "restored-host-2",
        bank=AlwaysPlanBank(json.dumps(spec("work"))),
        workspace_id=workspace_id,
    )
    try:

        def again():
            return fx3.host.context.journal._backend.read_one(
                "SELECT count(*) FROM workflow_runs", ()
            )[0]

        assert await fx3.on_core(again) == 0
    finally:
        fx3.close()


PLANNING_TASK_SET = (
    "功能说明与文档产出",
    "纯调研与证据收集",
    "单模块缺陷定位与修复",
    "跨模块重构与回归验证",
    "需要独立 Review 交付的任务",
    "使用自定义 Agent 的比较类任务",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("index,objective", list(enumerate(PLANNING_TASK_SET)))
async def test_scripted_plans_cover_the_release_task_set_without_business_runs(
    tmp_path, index, objective
):
    graph = (
        spec("research", "build", "audit")
        if "Review" in objective or "调研" in objective
        else spec("work")
    )
    fx = ServerFixture(tmp_path, scripts=[[[json.dumps(graph)]]])
    try:
        sid, root = await new_session(fx)
        planned = await fx.client.post(
            root + "/task-plan",
            request(sid, task={"objective": objective}, command_id=f"cmd_plan_{index}"),
        )
        assert planned.status == 200 and planned.json()["status"] == "succeeded", planned.body
        view = (await fx.client.get(root + "/task-plan")).json()
        nodes = view["draft"]["draft"]["source"]["nodes"]
        assert 1 <= len(nodes) <= 16
        assert view["draft"]["draft"]["source"]["required_outputs"]
        if any(node["node_id"] == "audit" for node in nodes):
            assert any(
                node.get("responsibility") == "review" or "audit" in node["node_id"]
                for node in nodes
            )

        def facts():
            backend = fx.host.context.journal._backend
            return {
                "runs": backend.read_one("SELECT count(*) FROM workflow_runs", ())[0],
                "planning": backend.read_one("SELECT count(*) FROM workflow_planning_requests", ())[
                    0
                ],
            }

        counts = await fx.on_core(facts)
        assert counts["runs"] == 0
        assert counts["planning"] == 1
        assert len(fx.bank.providers[-1].stream_calls) == 1
        assert objective in fx.bank.providers[-1].stream_calls[0][1].content
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_adding_a_node_changes_execution_digest_and_keeps_frozen_model(tmp_path):
    fx, sid, root, view = await _planned(tmp_path, "work")
    try:
        execution = view["execution"]
        stale_digest = execution["digest"]
        added = await fx.client.post(
            root + "/task-plan/nodes",
            {
                "command_id": "cmd_add_node",
                "binding_id": view["binding"]["planning_binding_id"],
                "expected_version": execution["draft_version"],
                "action": "add",
                "node": {
                    "node_id": "docs",
                    "title": "docs",
                    "task": "Write the README",
                    "agent": "preset:general",
                    "responsibility": "implementation",
                    "depends_on": ["work"],
                    "completion": ["README exists"],
                },
            },
        )
        assert added.status == 200, added.body
        updated = (await fx.client.get(root + "/task-plan")).json()
        assert updated["execution"]["digest"] != stale_digest
        stale = await fx.client.post(
            root + "/task-plan/start",
            {
                "command_id": "cmd_stale_after_add",
                "session_id": sid,
                "draft_id": execution["draft_id"],
                "draft_version": execution["draft_version"],
                "execution_digest": stale_digest,
                "action_source": "button",
                "interaction_id": "message1",
            },
        )
        assert stale.status == 409, stale.body
        started = await fx.client.post(
            root + "/task-plan/start",
            {
                "command_id": "cmd_start_after_add",
                "session_id": sid,
                "draft_id": updated["execution"]["draft_id"],
                "draft_version": updated["execution"]["draft_version"],
                "execution_digest": updated["execution"]["digest"],
                "action_source": "button",
                "interaction_id": "message1",
            },
        )
        assert started.status == 200, started.body
        frozen = updated["execution"]["frozen_selections"]
        assert frozen and frozen[0]["resolved_model"] == {
            "provider_id": "fake-provider",
            "model_id": "m1",
        }
    finally:
        fx.close()
