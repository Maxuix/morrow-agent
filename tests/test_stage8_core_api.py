"""Stage 8 Subplan 3: Core API and local server contract tests.

A scripted in-process verification client (tests/fixtures/core_api_client.py)
exercises the full contract over raw ASGI: snapshot, ordered durable event
pull, WebSocket cursor hints, forced disconnect/reconnect, gap resync, command
idempotency under retry, CLI–API parity for the same WorkflowRun, Core restart
recovery observed through the API, and the approval round-trip. All Providers
are scripted; synchronization uses conditions, never wall-clock sleeps.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from typer.testing import CliRunner

from fixtures.core_api_client import CoreApiVerificationClient
from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.bootstrap import build_application
from morrow.core.agent_runs import AgentDefinitionRef, ProviderCapabilities
from morrow.core.capabilities import PermissionProfile
from morrow.core.models import (
    AssistantMessage,
    CredentialRef,
    FunctionToolCall,
    ProviderConfig,
    ProviderModelConfig,
)
from morrow.core.workflows.definitions import WorkflowBudget
from morrow.interfaces.cli import app as cli_app
from morrow.server.app import create_asgi_app
from morrow.server.composition import make_context_builder
from morrow.server.host import CommandBackpressureError, CoreHost
from test_stage7_serial_scheduler import (
    MODEL,
    ScriptBank,
    agent_source,
    pair_source,
    wait_for,
)

SERVE_TOKEN = "test-session-token"
ONE_REQUEST_BUDGET = WorkflowBudget(
    max_agent_generation_requests=1,
    default_node_max_agent_generation_requests=1,
    admission_timeout_seconds=300,
    max_concurrency=1,
)

# update_configuration carries static ToolApproval.REQUIRED, so the run
# deterministically parks at the approval gate under the manual profile.
WRITE_CALL_SCRIPT = [
    AssistantMessage(
        tool_calls=(
            FunctionToolCall(
                id="call_config",
                name="update_configuration",
                arguments=json.dumps(
                    {
                        "scope": "workspace",
                        "target": "profile",
                        "operation": "set",
                        "path": "summary",
                        "value": "updated by the workflow",
                    }
                ),
            ),
        )
    ),
    ["configuration finished"],
]


class ServerFixture:
    """The real serve composition: Core Host thread + ASGI app + client."""

    def __init__(self, tmp_path, scripts=(), *, queue_size: int = 128, on_create=None) -> None:
        self.workspace_dir = tmp_path / "workspace"
        self.workspace_dir.mkdir(exist_ok=True)
        self.state_root = tmp_path / "state-root"
        self.app = build_application(
            state_root=self.state_root, credentials=MemoryCredentialStore()
        )
        # The session composition resolves the active model once; node drives
        # consume the scripted providers after that leading dummy.
        self.bank = ScriptBank([["session composition"]] + list(scripts), on_create=on_create)
        self.app.registry.register(
            "fake-adapter",
            self.bank,
            capabilities=ProviderCapabilities(
                tool_protocol="openai_function", multiple_tool_calls=True
            ),
        )
        credential_ref = CredentialRef(ref="provider:fake-provider:test", version=3)
        self.app.credentials.set(credential_ref.ref, "topsecret-value")
        config = self.app.global_store.load()
        self.app.global_store.update(
            lambda value: value.model_copy(
                update={
                    "providers": {
                        "fake-provider": ProviderConfig(
                            adapter="fake-adapter",
                            base_url="https://api.example.test/v1",
                            credential_ref=credential_ref,
                            models={
                                "m1": ProviderModelConfig(api_model_id="api-m1"),
                                "m2": ProviderModelConfig(api_model_id="api-m2"),
                            },
                        )
                    },
                    "active_model": MODEL,
                }
            ),
            expected_revision=config.revision,
        )
        resolution = self.app.workspace_service.resolve(self.workspace_dir)
        self.identity = (
            self.app.workspace_service.confirm(resolution)
            if resolution.status == "candidate"
            else resolution.identity
        )
        self.workspace_id = self.identity.workspace_id
        # The configuration tool's preflight requires an existing Profile.
        self.app.workspace_state_service.onboard(
            self.workspace_id, display_name="Test Workspace", summary="core api tests"
        )
        self.host = CoreHost(
            make_context_builder(self.app, self.identity, permission_profile=PermissionProfile()),
            command_queue_size=queue_size,
        )
        self.host.start()
        self.client = CoreApiVerificationClient(
            create_asgi_app(self.host, auth_token=SERVE_TOKEN, ws_ping_seconds=7200),
            token=SERVE_TOKEN,
        )

    async def on_core(self, fn):
        """Run a fixture helper on the Core thread (its store owner)."""

        return await self.host.execute_query(fn)

    def close(self) -> None:
        self.host.stop()


@pytest.fixture
def fx(tmp_path):
    fixture = ServerFixture(tmp_path)
    yield fixture
    fixture.close()


def write_pair_source(ref, **kwargs):
    """Two-node chain whose nodes permit the write tool end to end."""

    source = pair_source(ref, **kwargs)
    nodes = tuple(node.model_copy(update={"access_mode": "write"}) for node in source.nodes)
    return source.model_copy(update={"nodes": nodes})


async def publish_pipeline(fx, *, agent=None, make_source=pair_source, **kwargs):
    def work():
        management = fx.host.context.management
        management.create_agent_source(agent or agent_source(), expected_source_revision=0)
        version = management.publish_agent(
            "helper", expected_head_revision=0, command_id="cmd_pub_agent"
        )
        ref = AgentDefinitionRef(
            definition_id=version.source.definition_id,
            version_id=version.version_id,
            content_hash=version.content_hash,
        )
        management.create_workflow_source(make_source(ref, **kwargs), expected_source_revision=0)
        publication = management.publish_workflow(
            "pipeline", expected_head_revision=0, command_id="cmd_pub_wf"
        )
        return publication.revision

    return await fx.host.execute_query(work)


async def create_session_and_task(client):
    session = await client.post("/v1/sessions", {})
    assert session.status == 200, session.body
    session_id = session.json()["result"]["session"]["session_id"]
    task = await client.post("/v1/tasks", {"session_id": session_id})
    assert task.status == 200, task.body
    created = task.json()["result"]["task"]
    return session_id, created["task_run_id"], created["row_version"]


async def start_run(client, revision_id, session_id, task_id, task_version, **extra):
    body = {
        "workflow_definition_id": "pipeline",
        "workflow_revision_id": revision_id,
        "session_id": session_id,
        "root_task_run_id": task_id,
        "expected_root_row_version": task_version,
        "objective": "Audit password validation and authorization tests",
        **extra,
    }
    return await client.post("/v1/workflow-runs", body)


async def wait_for_run(client, run_id, *statuses):
    for _ in range(4000):
        await asyncio.sleep(0)
        response = await client.get(f"/v1/workflow-runs/{run_id}")
        assert response.status == 200, response.body
        view = response.json()["view"]
        if view["run"]["status"] in statuses:
            return view
    raise AssertionError(f"run {run_id} never reached {statuses}")


# Meta, snapshot and event stream -----------------------------------------------


async def test_meta_snapshot_and_event_pull(fx):
    meta = await fx.client.get("/v1/meta")
    assert meta.status == 200
    body = meta.json()
    assert body["protocol_version"] == 1
    assert body["workspace_id"] == fx.workspace_id
    assert body["latest_cursor"] == 0

    snapshot = await fx.client.take_snapshot()
    assert snapshot["cursor"] == 0
    assert snapshot["workflow_runs"] == []
    assert snapshot["pending_approvals"] == []

    session_id, task_id, _version = await create_session_and_task(fx.client)
    page = await fx.client.get("/v1/events?after=0&limit=100")
    assert page.status == 200
    events = page.json()["events"]
    assert [event["event_type"] for event in events] == ["session.created", "task.created"]
    assert [event["cursor"] for event in events] == [1, 2]
    assert page.json()["latest_cursor"] == 2

    snapshot = await fx.client.take_snapshot()
    assert snapshot["cursor"] == 2


async def test_websocket_hints_disconnect_reconnect_and_gap_resync(fx):
    await fx.client.take_snapshot()
    ws = fx.client.websocket()
    await ws.accept()
    assert await fx.client.expect_cursor_hint(ws) == 0

    # Live hint: a command notifies the subscriber, the pull carries the fact.
    await create_session_and_task(fx.client)
    hinted = await fx.client.expect_cursor_hint(ws)
    assert hinted > fx.client.last_cursor
    fresh = await fx.client.pull_events()
    assert [event["event_type"] for event in fresh] == ["session.created", "task.created"]

    # Forced disconnect: hints are lost while offline, never the durable facts.
    await ws.close()
    await create_session_and_task(fx.client)

    ws2 = fx.client.websocket()
    await ws2.accept()
    hello = await ws2.receive_json()
    assert hello["type"] == "hello"
    assert hello["latest_cursor"] > fx.client.last_cursor
    gap = await fx.client.pull_events()
    assert [event["event_type"] for event in gap] == ["session.created", "task.created"]

    # Duplicate pull replays are deduplicated by the client contract.
    assert await fx.client.pull_events() == []
    cursors = [event["cursor"] for event in fx.client.applied]
    assert cursors == sorted(cursors)
    await ws2.close()


async def test_stale_snapshot_triggers_resync(fx):
    stale = await fx.client.take_snapshot()
    await create_session_and_task(fx.client)
    meta = await fx.client.get("/v1/meta")
    assert meta.json()["latest_cursor"] > stale["cursor"]
    fresh = await fx.client.resync()
    assert fresh["cursor"] == meta.json()["latest_cursor"]


# Command idempotency ------------------------------------------------------------


async def test_session_command_idempotency_and_conflict(fx):
    first = await fx.client.post("/v1/sessions", {"command_id": "cmd_sess_1"})
    assert first.status == 200
    assert first.json()["receipt"]["disposition"] == "accepted"
    session_id = first.json()["result"]["session"]["session_id"]

    retry = await fx.client.post("/v1/sessions", {"command_id": "cmd_sess_1"})
    assert retry.status == 200
    assert retry.json()["receipt"]["disposition"] == "replay"
    assert retry.json()["result"]["session"]["session_id"] == session_id

    conflict = await fx.client.post(
        "/v1/sessions", {"command_id": "cmd_sess_1", "session_id": "ses_other"}
    )
    assert conflict.status == 409

    sessions = await fx.client.get("/v1/sessions")
    ids = [item["session_id"] for item in sessions.json()["sessions"]]
    assert session_id in ids and len(ids) == len(set(ids))


# Workflow driving, events and CLI parity ----------------------------------------


async def test_workflow_run_happy_path_events_and_cli_parity(fx):
    revision = await publish_pipeline(fx)
    session_id, task_id, task_version = await create_session_and_task(fx.client)
    started = await start_run(
        fx.client, revision.workflow_revision_id, session_id, task_id, task_version
    )
    assert started.status == 200, started.body
    run_id = started.json()["result"]["run"]["workflow_run_id"]
    assert started.json()["result"]["driving"] is True

    view = await wait_for_run(fx.client, run_id, "completed")
    assert view["run"]["result_status"] == "succeeded"
    assert {node["node"]["status"] for node in view["nodes"]} == {"completed"}
    assert view["usage_availability"] in {"available", "unavailable"}

    event_types = [
        event["event_type"]
        for event in (await fx.client.get("/v1/events?after=0")).json()["events"]
    ]
    assert "workflow_run.created" in event_types
    assert "workflow_node.status_changed" in event_types
    assert event_types[-1] == "workflow_run.status_changed"

    # CLI and API read the same WorkflowRun state.
    completed = await fx.client.get(f"/v1/workflow-runs/{run_id}")
    api_run = completed.json()["view"]["run"]
    cli = CliRunner().invoke(
        cli_app,
        [
            "workflow",
            "status",
            run_id,
            "--workspace-id",
            fx.workspace_id,
            "--dir",
            str(fx.workspace_dir),
            "--state-root",
            str(fx.state_root),
        ],
    )
    assert cli.exit_code == 0, cli.output
    cli_run = json.loads(cli.output)["run"]
    assert cli_run["status"] == api_run["status"] == "completed"
    assert cli_run["result_status"] == api_run["result_status"] == "succeeded"
    assert cli_run["row_version"] == api_run["row_version"]


async def test_pause_resume_and_drain_through_the_api(fx):
    from morrow.core.agent_definitions import ToolRequirement

    agent = agent_source(
        access_mode_ceiling="write",
        tool_requirements=(ToolRequirement(name="update_configuration", requirement="required"),),
    )
    revision = await publish_pipeline(fx, agent=agent, make_source=write_pair_source)
    fx.bank.scripts.extend([WRITE_CALL_SCRIPT, ["phase three"]])
    session_id, task_id, task_version = await create_session_and_task(fx.client)
    started = await start_run(
        fx.client, revision.workflow_revision_id, session_id, task_id, task_version
    )
    run_id = started.json()["result"]["run"]["workflow_run_id"]

    # Gamma waits on the write approval; the run stays running.
    async def approval_pending():
        for _ in range(4000):
            await asyncio.sleep(0)
            view = (await fx.client.get(f"/v1/workflow-runs/{run_id}")).json()["view"]
            if any(node["approval_pending"] for node in view["nodes"]):
                return view
        raise AssertionError("approval never became pending")

    await approval_pending()
    approvals = await fx.client.get("/v1/approvals")
    pending = approvals.json()["approvals"]
    assert len(pending) == 1
    assert pending[0]["tool_name"] == "update_configuration"
    # The approval surface carries bounded previews, never full tool arguments.
    assert "arguments" not in pending[0]
    assert "intent" not in pending[0]
    assert len(pending[0]["preview"]) <= 16

    # Pause while the approval is in flight: draining, not blocked.
    paused = await fx.client.post(f"/v1/workflow-runs/{run_id}/pause", {})
    assert paused.status == 200, paused.body
    assert paused.json()["result"]["run"]["status"] == "draining"
    assert paused.json()["result"]["run"]["pause_requested"] is True

    # Pause replay is a no-op with the same Command ID.
    replayed = await fx.client.post(f"/v1/workflow-runs/{run_id}/pause", {})
    assert replayed.status == 200

    # Resolving the approval lets gamma finish; the drain then settles paused.
    resolved = await fx.client.post(
        f"/v1/approvals/{pending[0]['approval_id']}/resolve", {"approved": True}
    )
    assert resolved.status == 200, resolved.body
    assert resolved.json()["result"]["delivery"] == "live"
    view = await wait_for_run(fx.client, run_id, "paused")
    assert view["run"]["pause_requested"] is True
    gamma = next(node for node in view["nodes"] if node["node"]["node_id"] == "gamma")
    alpha = next(node for node in view["nodes"] if node["node"]["node_id"] == "alpha")
    assert gamma["node"]["status"] == "completed"
    assert alpha["node"]["status"] == "queued"

    resumed = await fx.client.post(f"/v1/workflow-runs/{run_id}/resume", {})
    assert resumed.status == 200, resumed.body
    assert resumed.json()["result"]["driving"] is True
    view = await wait_for_run(fx.client, run_id, "completed")
    assert view["run"]["result_status"] == "succeeded"

    statuses = [
        (event["event_type"], event["payload"].get("status"))
        for event in (await fx.client.get("/v1/events?after=0")).json()["events"]
        if event["aggregate_id"] == run_id
    ]
    assert ("workflow_run.status_changed", "draining") in statuses
    assert ("workflow_run.status_changed", "paused") in statuses
    assert ("workflow_run.status_changed", "completed") in statuses


async def test_approval_denial_and_dormant_resolution_reject_double_resolve(fx):
    from morrow.core.agent_definitions import ToolRequirement

    agent = agent_source(
        access_mode_ceiling="write",
        tool_requirements=(ToolRequirement(name="update_configuration", requirement="required"),),
    )
    revision = await publish_pipeline(fx, agent=agent, make_source=write_pair_source)
    fx.bank.scripts.extend([WRITE_CALL_SCRIPT, ["phase three"]])
    session_id, task_id, task_version = await create_session_and_task(fx.client)
    started = await start_run(
        fx.client, revision.workflow_revision_id, session_id, task_id, task_version
    )
    assert started.status == 200, started.body

    async def first_pending():
        for _ in range(4000):
            await asyncio.sleep(0)
            approvals = (await fx.client.get("/v1/approvals")).json()["approvals"]
            if approvals:
                return approvals[0]
        raise AssertionError("approval never became pending")

    pending = await first_pending()
    denied = await fx.client.post(
        f"/v1/approvals/{pending['approval_id']}/resolve", {"approved": False}
    )
    assert denied.status == 200
    again = await fx.client.post(
        f"/v1/approvals/{pending['approval_id']}/resolve", {"approved": True}
    )
    assert again.status == 409


# Rerun idempotency ---------------------------------------------------------------


async def test_rerun_retry_never_double_applies(fx):
    revision = await publish_pipeline(fx, budget=ONE_REQUEST_BUDGET)
    session_id, task_id, task_version = await create_session_and_task(fx.client)
    started = await start_run(
        fx.client, revision.workflow_revision_id, session_id, task_id, task_version
    )
    run_id = started.json()["result"]["run"]["workflow_run_id"]
    await wait_for_run(fx.client, run_id, "failed")

    # The failed root must be explicitly resumed before a rerun starts.
    resumed = await fx.client.post(f"/v1/tasks/{task_id}/resume", {})
    assert resumed.status == 200, resumed.body

    rerun = await fx.client.post(
        f"/v1/workflow-runs/{run_id}/rerun", {"full": True, "command_id": "cmd_rerun_1"}
    )
    assert rerun.status == 200, rerun.body
    child_id = rerun.json()["result"]["child"]["workflow_run_id"]
    assert rerun.json()["result"]["child"]["run_relation"] == "rerun"
    assert rerun.json()["result"]["child"]["lineage_budget_root_run_id"] == child_id

    retry = await fx.client.post(
        f"/v1/workflow-runs/{run_id}/rerun", {"full": True, "command_id": "cmd_rerun_1"}
    )
    assert retry.status == 200, retry.body
    assert retry.json()["receipt"]["disposition"] == "replay"
    assert retry.json()["result"]["child"]["workflow_run_id"] == child_id

    runs = (await fx.client.get("/v1/workflow-runs")).json()["workflow_runs"]
    assert len(runs) == 2
    # The rerun shares the revision's one-request budget, so it fails the same
    # way — the point here is idempotent creation, not a budget override.
    failed_child = await wait_for_run(fx.client, child_id, "failed")
    assert failed_child["terminal_outcome"] is not None


# Core restart recovery ---------------------------------------------------------


async def test_core_restart_recovers_through_the_api(fx, tmp_path):
    revision = await publish_pipeline(fx)
    session_id, task_id, task_version = await create_session_and_task(fx.client)

    from morrow.application.workflows.start import StartWorkflowCommand
    from morrow.core.workflows.contracts import TaskContract

    async def start_without_driver():
        return await fx.on_core(
            lambda: fx.host.context.management.start_foreground(
                StartWorkflowCommand(
                    workflow_definition_id="pipeline",
                    workflow_revision_id=revision.workflow_revision_id,
                    session_id=session_id,
                    root_task_run_id=task_id,
                    expected_root_row_version=task_version,
                    contract=TaskContract(objective="Audit recovery after restart"),
                    command_id="cmd_restart_start",
                )
            )
        )

    started = await start_without_driver()
    run_id = started.run.workflow_run_id
    fx.close()

    # A fresh Core Host over the same state observes and resumes the run.
    restarted = ServerFixture(tmp_path)
    try:
        observed = await restarted.client.get(f"/v1/workflow-runs/{run_id}")
        assert observed.status == 200, observed.body
        assert observed.json()["view"]["run"]["status"] == "queued"

        # The pre-restart durable events stay on the same cursor stream; the
        # run was created outside the API here, so no workflow_run.created.
        events = await restarted.client.get("/v1/events?after=0")
        assert events.status == 200
        before = events.json()["events"]
        assert [event["event_type"] for event in before] == ["session.created", "task.created"]

        resumed = await restarted.client.post(f"/v1/workflow-runs/{run_id}/resume", {})
        assert resumed.status == 200, resumed.body
        assert resumed.json()["result"]["driving"] is True
        view = await wait_for_run(restarted.client, run_id, "completed")
        assert view["run"]["result_status"] == "succeeded"

        after = (await restarted.client.get("/v1/events?after=0")).json()["events"]
        new_types = [event["event_type"] for event in after[len(before) :]]
        assert "workflow_node.status_changed" in new_types
        assert "workflow_run.status_changed" in new_types
        assert [event["cursor"] for event in after] == list(range(1, len(after) + 1))
    finally:
        restarted.close()


# Command bus backpressure --------------------------------------------------------


async def test_command_bus_backpressure_is_explicit(tmp_path):
    fx = ServerFixture(tmp_path, queue_size=1)
    try:
        gate = None

        async def blocking():
            nonlocal gate
            gate = asyncio.Event()
            await gate.wait()
            return "done"

        first = asyncio.create_task(fx.host.execute_command(blocking))
        await wait_for(lambda: gate is not None)
        second = asyncio.create_task(fx.host.execute_command(lambda: "second"))
        await wait_for(lambda: fx.host.queued_commands == 1)

        with pytest.raises(CommandBackpressureError):
            await fx.host.execute_command(lambda: "third")
        response = await fx.client.post("/v1/workflow-runs/wrun_missing/pause", {})
        assert response.status == 503
        assert response.json()["error"]["code"] == "busy"

        await fx.on_core(lambda: gate.set())
        assert await first == "done"
        assert await second == "second"
    finally:
        fx.close()


# Patch validate/save/apply through the API -------------------------------------


async def test_patch_validate_save_apply_and_replay(tmp_path):
    cell = {}

    def pause_at_alpha(count):
        # Composition consumes provider 1; gamma is 2; pausing at alpha's
        # preparation leaves the run drained with gamma completed.
        if count == 3:
            cell["fx"].host.context.runtime.transitions.request_pause(cell["run_id"])

    fx = ServerFixture(tmp_path, on_create=pause_at_alpha)
    try:
        cell["fx"] = fx
        fx.bank.scripts.extend([["phase one"], ["unused"], ["phase three continued"]])
        revision = await publish_pipeline(fx)
        session_id, task_id, task_version = await create_session_and_task(fx.client)
        started = await start_run(
            fx.client, revision.workflow_revision_id, session_id, task_id, task_version
        )
        run_id = started.json()["result"]["run"]["workflow_run_id"]
        cell["run_id"] = run_id
        parent = await wait_for_run(fx.client, run_id, "paused")

        definition = (await fx.client.get("/v1/catalog/workflow-definitions/pipeline")).json()[
            "workflow_definition"
        ]
        source = definition["source"]
        for node in source["nodes"]:
            if node["node_id"] == "alpha":
                node["task_contract"]["objective"] = "Conclude from inherited evidence"
        patch = {
            "workflow_patch_id": "wpatch_api_one",
            "workspace_id": fx.workspace_id,
            "parent_run_id": run_id,
            "base_workflow_revision_id": revision.workflow_revision_id,
            "expected_parent_row_version": parent["run"]["row_version"],
            "source": source,
            "requested_by": "cmd_patch_api",
        }
        validated = await fx.client.post("/v1/patches/validate", {"patch": patch})
        assert validated.status == 200, validated.body
        assert validated.json()["result"]["valid"] is True
        assert validated.json()["result"]["execution_node_ids"] == ["alpha"]
        assert "gamma" in validated.json()["result"]["past_node_ids"]

        saved = await fx.client.post(
            "/v1/patches/save", {"patch": patch, "command_id": "cmd_patch_save_1"}
        )
        assert saved.status == 200, saved.body

        applied = await fx.client.post(
            "/v1/patches/apply", {"patch": patch, "command_id": "cmd_patch_apply_1"}
        )
        assert applied.status == 200, applied.body
        result = applied.json()["result"]
        assert result["parent_run"]["status"] == "superseded"
        child = result["child_run"]
        assert child["run_relation"] == "continuation"
        assert child["parent_run_id"] == run_id
        assert result["driving"] is True

        # Apply retry with the same Command ID replays; no second child.
        replay = await fx.client.post(
            "/v1/patches/apply", {"patch": patch, "command_id": "cmd_patch_apply_1"}
        )
        assert replay.status == 200, replay.body
        assert replay.json()["receipt"]["disposition"] == "replay"
        assert replay.json()["result"]["child_run"]["workflow_run_id"] == child["workflow_run_id"]
        runs = (await fx.client.get("/v1/workflow-runs")).json()["workflow_runs"]
        assert len(runs) == 2

        child_view = await wait_for_run(fx.client, child["workflow_run_id"], "completed")
        assert child_view["run"]["result_status"] == "succeeded"
        assert [node["node"]["node_id"] for node in child_view["nodes"]] == ["alpha"]
        inherited = [
            (item["source_node_id"], item["output_slot"])
            for item in child_view["inherited_artifacts"]
        ]
        assert inherited == [("gamma", "result")]
        outputs = {
            (item["node_id"], item["output_slot"]): item["inherited"]
            for item in child_view["effective_outputs"]
        }
        assert outputs[("gamma", "result")] is True
        assert outputs[("alpha", "result")] is False

        statuses = [
            (event["aggregate_id"], event["payload"].get("status"))
            for event in (await fx.client.get("/v1/events?after=0")).json()["events"]
            if event["event_type"] == "workflow_run.status_changed"
        ]
        assert (run_id, "superseded") in statuses
    finally:
        fx.close()


# Catalog surface ----------------------------------------------------------------


async def test_catalog_surfaces(fx):
    await publish_pipeline(fx)
    agents = await fx.client.get("/v1/catalog/agent-definitions")
    assert agents.status == 200
    ids = [item["definition_id"] for item in agents.json()["agent_definitions"]]
    assert "helper" in ids

    definitions = await fx.client.get("/v1/catalog/workflow-definitions")
    assert any(
        item["workflow_definition_id"] == "pipeline"
        for item in definitions.json()["workflow_definitions"]
    )
    revisions = await fx.client.get("/v1/catalog/workflow-revisions?definition_id=pipeline")
    assert len(revisions.json()["workflow_revisions"]) == 1

    models = await fx.client.get("/v1/catalog/providers")
    assert models.json()["active_model"] == {"provider_id": "fake-provider", "model_id": "m1"}

    skills = await fx.client.get("/v1/catalog/skills")
    assert skills.status == 200

    tools = await fx.client.get("/v1/catalog/tools")
    names = [item["name"] for item in tools.json()["tools"]]
    assert {"read", "ls", "find", "grep", "edit", "write", "bash"} <= set(names)

    contracts = await fx.client.get("/v1/catalog/artifact-contracts")
    kinds = [item["kind"] for item in contracts.json()["contracts"]]
    assert "TaskContract" in kinds and "ReviewReport" in kinds
    assert "test_report" in contracts.json()["artifact_kinds"]

    missing = await fx.client.get("/v1/catalog/agent-definitions/nope")
    assert missing.status == 404


# CLI smoke ------------------------------------------------------------------------


def test_serve_help_smoke():
    result = CliRunner().invoke(cli_app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--port" in result.output
