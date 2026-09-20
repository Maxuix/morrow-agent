"""Chat requests share Workflow admission, ownership and publication gates."""

import pytest

from test_stage8_chat_submission import drain, new_session
from test_stage8_core_api import ServerFixture, publish_pipeline


async def test_explicit_workflow_replay_source_cards_and_chat_follow_up(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        revision = await publish_pipeline(fx)
        sid, path = await new_session(fx)
        request = {
            "client_message_id": "workflow.input",
            "intent": "explicit_workflow",
            "text": "Inspect project structure",
            "workflow": {
                "workflow_definition_id": "pipeline",
                "workflow_revision_id": revision.workflow_revision_id,
            },
        }
        first = await fx.client.post(path + "/interactions", request)
        assert first.status == 202, first.body
        await drain(fx, sid)
        receipt = (await fx.client.get(path + "/interactions/workflow.input")).json()["receipt"]
        assert receipt["status"] == "settled", receipt
        assert receipt["workflow_run_id"]
        replay = await fx.client.post(path + "/interactions", request)
        assert (
            replay.status == 202
            and replay.json()["receipt"]["workflow_run_id"] == receipt["workflow_run_id"]
        )
        assert (
            await fx.client.post(path + "/interactions", {**request, "text": "different"})
        ).status == 409
        page = (await fx.client.get(path + "/workflow-interactions")).json()
        assert len(page["items"]) == 1 and page["items"][0]["text"] == request["text"]
        run_id = receipt["workflow_run_id"]
        outputs = (await fx.client.get(f"/v1/workflow-runs/{run_id}")).json()["view"][
            "effective_outputs"
        ]
        assert outputs
        processed = await fx.client.post(
            f"/v1/workflow-runs/{run_id}/replans/process", {"command_id": "cmd_process"}
        )
        assert processed.status == 200, processed.body
        for output in outputs:
            aid = output["binding"]["artifact_id"]
            preview = await fx.client.get(f"/v1/workflow-runs/{run_id}/artifacts/{aid}/content")
            assert preview.status == 200, preview.body
            assert len(preview.json()["content"].encode()) <= 65536
            assert (await fx.client.get(path + f"/artifacts/{aid}/content")).status == 404
        assert (
            await fx.client.get(f"/v1/workflow-runs/{run_id}/artifacts/art_missing/content")
        ).status == 404

        def verify():
            c = fx.host.context
            assert not c.journal.load_records(
                fx.workspace_id, sid
            )  # multi-node leaf logs stay private
            assert (
                c.journal.workflows.get_run(
                    fx.workspace_id, receipt["workflow_run_id"]
                ).root_task_run_id
                == page["items"][0]["task_run_id"]
            )

        await fx.on_core(verify)
        sent = await fx.client.post(
            path + "/interactions",
            {"client_message_id": "follow", "text": "Summarize the task result"},
        )
        assert sent.status == 202, sent.body
        await drain(fx, sid)
        receipt = (await fx.client.get(path + "/interactions/follow")).json()["receipt"]
        assert receipt["turn_id"], receipt
    finally:
        fx.close()


@pytest.mark.parametrize("kind,identity", [("agent", "helper"), ("workflow", "pipeline")])
async def test_definition_lifecycle_occ_replay_revocation(tmp_path, kind, identity):
    fx = ServerFixture(tmp_path)
    try:
        await publish_pipeline(fx)
        path = f"/v1/workspaces/{fx.workspace_id}/definition-actions/{kind}/{identity}"
        check = await fx.client.post(path, {"command_id": "cmd_validate", "action": "validate"})
        assert check.status == 200, check.body
        body = {"command_id": "cmd_disable", "action": "disable", "expected_head_revision": 1}
        disabled = await fx.client.post(path, body)
        assert disabled.status == 200, disabled.body
        assert (await fx.client.post(path, body)).status == 200
        assert (await fx.client.post(path, {**body, "command_id": "cmd_stale"})).status == 409
        enabled = await fx.client.post(
            path, {"command_id": "cmd_enable", "action": "enable", "expected_head_revision": 2}
        )
        assert enabled.status == 200, enabled.body
        definition = enabled.json()[kind + "_definition"]
        version = (
            definition["published_version"]["version_id"]
            if kind == "agent"
            else definition["published_revision"]["workflow_revision_id"]
        )
        body = {
            "command_id": "cmd_revoke",
            "action": "revoke",
            "version_id": version,
            "reason": "Fixture lifecycle check",
        }
        assert (await fx.client.post(path, body)).status == 200
        assert (await fx.client.post(path, body)).status == 200
        invalid = await fx.client.post(
            path,
            {
                **body,
                "command_id": "cmd_wrong",
                "version_id": "adev_other" if kind == "agent" else "wrev_other",
            },
        )
        assert invalid.status == 400, invalid.body
    finally:
        fx.close()


async def test_started_workflow_cannot_be_withdrawn_as_unsent_input(tmp_path):
    import asyncio

    fx = ServerFixture(tmp_path)
    try:
        revision = await publish_pipeline(fx)
        sid, path = await new_session(fx)
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()
        cell = {}

        async def hold():
            async with fx.host.context.supervisor.execution_lock:
                cell["release"] = asyncio.Event()
                loop.call_soon_threadsafe(entered.set)
                await cell["release"].wait()

        def install():
            cell["task"] = asyncio.create_task(hold())

        await fx.on_core(install)
        await asyncio.wait_for(entered.wait(), 5)
        sent = await fx.client.post(
            path + "/interactions",
            {
                "client_message_id": "held",
                "intent": "explicit_workflow",
                "text": "Inspect",
                "workflow": {
                    "workflow_definition_id": "pipeline",
                    "workflow_revision_id": revision.workflow_revision_id,
                },
            },
        )
        assert sent.status == 202
        for _ in range(1000):
            receipt = (await fx.client.get(path + "/interactions/held")).json()["receipt"]
            if receipt.get("workflow_run_id"):
                break
            await asyncio.sleep(0)
        assert receipt["workflow_run_id"]
        refused = await fx.client.post(
            path + "/interactions/held/withdraw",
            {"command_id": "cmd_bad_withdraw", "expected_revision": receipt["revision"]},
        )
        assert refused.status == 409, refused.body
        await fx.on_core(lambda: cell["release"].set())
        await drain(fx, sid)
    finally:
        fx.close()


async def test_workflow_sources_create_edit_clone_publish_and_draft_pagination(tmp_path):
    from test_stage8_core_api import pair_source

    fx = ServerFixture(tmp_path)
    try:
        original = await publish_pipeline(fx)
        source = pair_source(original.nodes[0].agent_definition_ref).model_copy(
            update={"workflow_definition_id": "editable", "name": "Editable"}
        )
        base = f"/v1/workspaces/{fx.workspace_id}"
        spec = {
            "command_id": "cmd_source_create",
            "source": source.model_dump(mode="json"),
            "expected_source_revision": 1,
        }
        first = await fx.client.post(base + "/workflow-definitions", spec)
        assert first.status == 200, first.body
        assert (await fx.client.post(base + "/workflow-definitions", spec)).status == 200
        source = source.model_copy(update={"name": "Updated"})
        changed = await fx.client.request(
            "PUT",
            base + "/workflow-definitions/editable",
            body={
                "command_id": "cmd_source_edit",
                "source": source.model_dump(mode="json"),
                "expected_source_revision": 2,
            },
        )
        assert changed.status == 200, changed.body
        action = base + "/definition-actions/workflow/editable"
        published = await fx.client.post(
            action,
            {
                "command_id": "cmd_publish_editable",
                "action": "publish",
                "expected_head_revision": 0,
            },
        )
        assert published.status == 200, published.body
        cloned = await fx.client.post(
            action,
            {
                "command_id": "cmd_clone_editable",
                "action": "clone",
                "new_definition_id": "clone",
                "expected_source_revision": 3,
            },
        )
        assert cloned.status == 200, cloned.body
        for i in range(3):
            result = await fx.client.post(
                base + "/workflow-drafts",
                {
                    "command_id": f"cmd_draft_{i}",
                    "draft_id": f"wdraft_page_{i}",
                    "source": source.model_dump(mode="json"),
                    "expected_source_revision": 4,
                },
            )
            assert result.status == 200, result.body
        first = (await fx.client.get(base + "/workflow-drafts?limit=2")).json()["workflow_drafts"]
        second = (
            await fx.client.get(
                base + "/workflow-drafts?limit=2&after=" + first[-1]["draft"]["draft_id"]
            )
        ).json()["workflow_drafts"]
        assert len(first) == 2 and len(second) == 1
        assert len({r["draft"]["draft_id"] for r in first + second}) == 3
    finally:
        fx.close()


async def test_promoted_plan_auto_run_follows_user_policy_and_current_revision(tmp_path):
    from morrow.core.orchestration import OrchestrationPolicy
    from test_stage8_graph_planner import generate, planning, publish_roles

    fx = ServerFixture(tmp_path, scripts=[["verified result"]] * 30)
    try:
        await publish_roles(fx)
        policy = OrchestrationPolicy(
            scope="workspace", task_matcher="implementation", auto_run_mode="auto"
        )
        await fx.on_core(
            lambda: fx.host.context.products.orchestration_policies.put(policy, expected_revision=0)
        )
        request = planning("Implement a small change")
        generated = await generate(fx, request)
        assert generated["explanation"]["auto_run_eligible"]
        assert generated["explanation"]["auto_run_reason"] == "user_policy"
        frozen = await fx.client.post(
            "/v1/workflow-drafts/wdraft_plan/freeze",
            {"command_id": "cmd_chat_plan_freeze", "expected_row_version": 1},
        )
        assert frozen.status == 200, frozen.body
        revision = frozen.json()["result"]["workflow_revision"]["workflow_revision_id"]
        sid, path = await new_session(fx)
        body = {
            "client_message_id": "promoted",
            "intent": "explicit_workflow",
            "text": request["task"]["objective"],
            "workflow": {
                "workflow_definition_id": "planned",
                "workflow_revision_id": revision,
                "promoted_plan": request,
            },
        }
        bad = await fx.client.post(path + "/interactions", {**body, "text": "A different task"})
        assert bad.status == 409, bad.body
        sent = await fx.client.post(path + "/interactions", body)
        assert sent.status == 202, sent.body
        await drain(fx, sid)
        assert (await fx.client.get(path + "/interactions/promoted")).json()["receipt"][
            "status"
        ] == "settled"
        await fx.on_core(
            lambda: fx.host.context.products.orchestration_policies.put(
                policy.model_copy(update={"auto_run_mode": "approval_only"}), expected_revision=1
            )
        )
        stale = await fx.client.post(
            path + "/interactions", {**body, "client_message_id": "after_policy"}
        )
        assert stale.status == 409, stale.body
    finally:
        fx.close()


@pytest.mark.parametrize("external", [False, "completed", "running"])
async def test_direct_workflow_reuses_history_once_then_chat_reloads_current_log(
    tmp_path, external
):
    from test_stage8_graph_planner import publish_roles

    fx = ServerFixture(tmp_path, scripts=[["answer"]] * 12)
    try:
        await publish_roles(fx)
        publication = await fx.on_core(
            lambda: fx.host.context.management.publish_workflow(
                "builtin_direct_workflow",
                expected_head_revision=0,
                command_id="cmd_direct_workflow",
            )
        )
        view = await fx.on_core(
            lambda: fx.host.context.runtime.queries.get_workflow_definition(
                "builtin_direct_workflow"
            )
        )
        assert (
            view.source.nodes[0].agent_definition_ref
            == publication.revision.nodes[0].agent_definition_ref
        )
        sid, path = await new_session(fx)
        assert (
            await fx.client.post(
                path + "/interactions",
                {"client_message_id": "ordinary.before", "text": "Before explicit direct"},
            )
        ).status == 202
        await drain(fx, sid)
        # A fresh task is explicit; this does not rewrite the previous conversation.
        result = await fx.client.post(
            path + "/commands", {"command_id": "cmd_direct_task", "action": "task"}
        )
        assert result.status == 200, result.body
        after_input = {
            "client_message_id": "ordinary.after",
            "text": "Continue after explicit direct",
        }
        if external:
            if external == "running":
                import asyncio

                started = asyncio.Event()
                loop = asyncio.get_running_loop()
                cell = {}

                def hold_workflow():
                    # An evicted Chat runtime must not restore an in-progress
                    # Workflow as abandoned work while waiting for its gate.
                    fx.host.context.chat.runtimes.pop(sid)
                    cell["release"] = asyncio.Event()

                    def hook(_count):
                        provider = fx.bank.providers[-1]
                        original = provider.stream

                        async def held(*args, **kwargs):
                            loop.call_soon_threadsafe(started.set)
                            await cell["release"].wait()
                            async for event in original(*args, **kwargs):
                                yield event

                        provider.stream = held

                    fx.bank.on_create = hook

                await fx.on_core(hold_workflow)
            session = (await fx.client.get(path)).json()["session"]
            task = (await fx.client.get("/v1/tasks/" + session["current_task_run_id"])).json()[
                "task"
            ]
            sent = await fx.client.post(
                "/v1/workflow-runs",
                {
                    "command_id": "cmd_external_direct",
                    "client_message_id": "direct.once",
                    "workflow_definition_id": "builtin_direct_workflow",
                    "workflow_revision_id": publication.revision.workflow_revision_id,
                    "session_id": sid,
                    "root_task_run_id": task["task_run_id"],
                    "expected_root_row_version": task["row_version"],
                    "objective": "Explicit direct target",
                },
            )
            assert sent.status == 200, sent.body
            run_id = sent.json()["result"]["run"]["workflow_run_id"]
            if external == "running":
                await asyncio.wait_for(started.wait(), 5)
                discover = await fx.client.post(
                    path + "/recovery",
                    {"command_id": "cmd_active_workflow_discover", "action": "discover"},
                )
                assert discover.status == 503, discover.body
                assert discover.json()["error"]["code"] == "busy"
                queued = await fx.client.post(path + "/interactions", after_input)
                assert queued.status == 202, queued.body
                # Await an observable gate waiter/driver result, not wall time.
                for _ in range(1000):
                    waiting = await fx.on_core(
                        lambda: (
                            bool(fx.host.context.workspaces.coordinator.waiting)
                            or sid not in fx.host.context.chat.drivers
                        )
                    )
                    if waiting:
                        break
                assert waiting
                current = (await fx.client.get(path)).json()["session"]
                assert current["health"] == "ok", current
                await fx.on_core(lambda: cell["release"].set())
            await fx.host.execute_preparation(
                lambda: fx.host.context.supervisor.wait_driver(run_id)
            )
        else:
            sent = await fx.client.post(
                path + "/interactions",
                {
                    "client_message_id": "direct.once",
                    "intent": "explicit_workflow",
                    "text": "Explicit direct target",
                    "workflow": {
                        "workflow_definition_id": "builtin_direct_workflow",
                        "workflow_revision_id": publication.revision.workflow_revision_id,
                    },
                },
            )
            assert sent.status == 202, sent.body
            await drain(fx, sid)
            receipt = (await fx.client.get(path + "/interactions/direct.once")).json()["receipt"]
            assert receipt["status"] == "settled", receipt
        if external != "running":
            assert (await fx.client.post(path + "/interactions", after_input)).status == 202
        await drain(fx, sid)

        def verify():
            users = [
                r.payload["content"]
                for r in fx.host.context.journal.load_records(fx.workspace_id, sid)
                if r.payload.get("role") == "user"
            ]
            assert users == [
                "Before explicit direct",
                "Explicit direct target",
                "Continue after explicit direct",
            ]

        await fx.on_core(verify)
    finally:
        fx.close()


async def test_recovery_discovery_after_workflow_input(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        revision = await publish_pipeline(fx)
        sid, path = await new_session(fx)
        sent = await fx.client.post(
            path + "/interactions",
            {
                "client_message_id": "workflow.recovery",
                "intent": "explicit_workflow",
                "text": "Inspect project structure",
                "workflow": {
                    "workflow_definition_id": "pipeline",
                    "workflow_revision_id": revision.workflow_revision_id,
                },
            },
        )
        assert sent.status == 202, sent.body
        await drain(fx, sid)
        response = await fx.client.post(
            path + "/recovery", {"command_id": "cmd_discover_workflow", "action": "discover"}
        )
        assert response.status == 200, response.body
        assert not response.json()["pending_resume"]
    finally:
        fx.close()
