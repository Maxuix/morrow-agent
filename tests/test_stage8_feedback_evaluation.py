"""Scripted feedback, explicit policy review, paired evaluation and promotion acceptance."""

import pytest

from morrow.core.orchestration import OrchestrationPolicy
from morrow.core.workflows.definitions import WorkflowEdge
from morrow.core.workflows.feedback import WorkflowFeedback
from test_stage8_core_api import ServerFixture, create_session_and_task, wait_for_run
from test_stage8_graph_planner import generate, planning, publish_roles


async def edited_draft(fx, number, *, role="reviewer"):
    draft_id = f"wdraft_feedback_{number}"
    request = planning(
        "Implement a small change",
        draft_id=draft_id,
        workflow_definition_id=f"feedback_{number}",
        requested_roles=[role],
    )
    planned = await generate(fx, request)
    draft = planned["workflow_draft"]["draft"]
    # A name change is evidence, but cannot imply a routing preference.
    response = await fx.client.request(
        "PUT",
        f"/v1/workflow-drafts/{draft_id}",
        body={
            "source": draft["source"] | {"name": "Edited Workflow"},
            "expected_row_version": 1,
        },
    )
    assert response.status == 200, response.body
    return draft, response


async def completed(fx, number, *, multi, objective="Implement a small change"):
    definition = f"eval_{number}"
    draft_id = f"wdraft_eval_{number}"
    generated = await generate(
        fx,
        planning(
            objective,
            draft_id=draft_id,
            workflow_definition_id=definition,
            requested_roles=["reviewer"] if multi else ["direct"],
        ),
    )
    frozen = await fx.client.post(
        f"/v1/workflow-drafts/{draft_id}/freeze", {"expected_row_version": 1}
    )
    assert frozen.status == 200, frozen.body
    revision_id = frozen.json()["result"]["workflow_revision"]["workflow_revision_id"]
    session, task, row = await create_session_and_task(fx.client)
    reply = await fx.client.post(
        "/v1/workflow-runs",
        {
            "workflow_definition_id": definition,
            "workflow_revision_id": revision_id,
            "session_id": session,
            "root_task_run_id": task,
            "expected_root_row_version": row,
            "objective": objective,
            "client_message_id": None if multi else f"message_eval_{number}",
        },
    )
    assert reply.status == 200, reply.body
    run = reply.json()["result"]["run"]["workflow_run_id"]
    await wait_for_run(fx.client, run, "completed")
    return run, generated


@pytest.mark.asyncio
async def test_draft_capture_is_atomic_idempotent_and_single_edit_never_changes_policy(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        await publish_roles(fx)
        draft, _ = await edited_draft(fx, 1)
        request = {
            "source": draft["source"] | {"name": "Edited Workflow"},
            "expected_row_version": 1,
        }
        assert (
            await fx.client.request("PUT", "/v1/workflow-drafts/wdraft_feedback_1", body=request)
        ).status == 200

        def facts():
            service = fx.host.context.products.workflow_drafts.feedback
            return service.records.list(
                WorkflowFeedback, service.workspace_id
            ), service.policies.view()

        feedback, policies = await fx.on_core(facts)
        assert len(feedback) == 1 and feedback[0].kind == "graph_edit"
        assert policies["workspace"]["revision"] == 0
        inbox = await fx.client.get("/v1/management/learning")
        assert inbox.json()["orchestration"]["items"] == []

        def fail(_):
            raise ValueError("scripted feedback failure")

        await fx.on_core(
            lambda: setattr(fx.host.context.products.workflow_drafts.feedback, "record", fail)
        )
        failed = await fx.client.request(
            "PUT",
            "/v1/workflow-drafts/wdraft_feedback_1",
            body={
                "source": draft["source"] | {"name": "Atomic failure"},
                "expected_row_version": 2,
            },
        )
        assert failed.status == 400
        unchanged = await fx.client.get("/v1/workflow-drafts/wdraft_feedback_1")
        assert unchanged.json()["workflow_draft"]["draft"]["source"]["name"] == "Edited Workflow"
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_repeat_post_run_feedback_learning_review_accept_and_replay(tmp_path):
    fx = ServerFixture(tmp_path, scripts=[["result"]] * 12)
    try:
        await publish_roles(fx)
        runs = []
        for number in range(2):
            run, _ = await completed(fx, number, multi=True)
            runs.append(run)
            for repeat in range(2):
                response = await fx.client.post(
                    "/v1/management/workflow-feedback",
                    {
                        "command_id": f"cmd_feedback_{number}_{repeat}",
                        "workflow_run_id": run,
                        "kind": "too_complex",
                    },
                )
                assert response.status == 200, response.body
            inbox = (await fx.client.get("/v1/management/learning")).json()
            assert len(inbox["orchestration"]["items"]) == number
        c = inbox["orchestration"]["items"][0]
        assert len(c["evidence_ids"]) == 2
        assert c["proposed_policy"]["multi_agent"] is False
        view = (await fx.client.get("/v1/orchestration-policies")).json()
        assert view["workspace"]["revision"] == 0
        body = {"command_id": "cmd_accept_candidate", "action": "accept", "expected_row_version": 1}
        route = "/v1/management/workflow-policy-decision/" + c["candidate_id"]
        response = await fx.client.post(route, body)
        assert response.status == 200, response.body
        assert (await fx.client.post(route, body)).json()["result"]["status"] == "replayed"
        assert (await fx.client.get("/v1/orchestration-policies")).json()["workspace"][
            "revision"
        ] == 1
        planned = await generate(
            fx,
            planning(
                "Implement a large change across modules",
                draft_id="wdraft_after",
                workflow_definition_id="after",
            ),
        )
        assert planned["explanation"]["mode"] == "direct"
        assert planned["explanation"]["auto_run_eligible"] is False
        # Real read-only metrics are distinct from user judgments.
        dashboard = (await fx.client.get("/v1/management/workflow-evaluation")).json()
        assert len(dashboard["runs"]) == 2
        assert all(r["requests"] == 2 for r in dashboard["runs"])
        assert all(r["reviewer_findings"][0]["findings"] == ["result"] for r in dashboard["runs"])
        assert dashboard["metrics"]["reviewer_value"] is None
        assert not any(p["promoted"] for p in dashboard["promotion"])
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_pair_metrics_estimates_independence_policy_and_regression_gate(tmp_path):
    fx = ServerFixture(tmp_path, scripts=[["verified result"]] * 30)
    try:
        await publish_roles(fx)
        policy = OrchestrationPolicy(
            scope="workspace",
            task_matcher="implementation",
            auto_run_mode="allow_promoted",
            auto_replan_mode="allow_low_risk",
        )
        await fx.on_core(
            lambda: fx.host.context.products.orchestration_policies.put(policy, expected_revision=0)
        )
        pairs = []
        for number in range(3):
            direct, _ = await completed(fx, f"direct_{number}", multi=False)
            multi, _ = await completed(fx, f"multi_{number}", multi=True)
            pairs.append((direct, multi))
            if number == 0:
                estimate = await fx.client.post(
                    "/v1/management/workflow-evaluation",
                    {
                        "command_id": "cmd_estimate",
                        "multi_run_id": multi,
                        "direct_estimated_requests": 9,
                    },
                )
                assert estimate.status == 200, estimate.body
            request = {
                "command_id": f"cmd_pair_{number}",
                "multi_run_id": multi,
                "direct_run_id": direct,
                "direct_quality": 2,
                "multi_quality": 4 if number < 2 else 1,
            }
            response = await fx.client.post("/v1/management/workflow-evaluation", request)
            assert response.status == 200, response.body
            dashboard = (await fx.client.get("/v1/management/workflow-evaluation")).json()
            gate = next(p for p in dashboard["promotion"] if p["task_type"] == "implementation")
            assert gate["paired_count"] == number + 1
            assert gate["promoted"] is (number == 1)
            assert gate["auto_run_eligible"] is (number == 1)
            assert gate["task_class_replan_eligible"] is (number == 1)
            settings = (await fx.client.get("/v1/orchestration-policies")).json()
            eligibility = next(
                row for row in settings["eligibility"] if row["task_type"] == "implementation"
            )
            assert eligibility == {
                "task_type": "implementation",
                "promoted": number == 1,
                "auto_run_eligible": number == 1,
                "auto_replan_eligible": number == 1,
            }
            if number == 1:
                approval = policy.model_copy(
                    update={"auto_run_mode": "approval_only", "auto_replan_mode": "approval_only"}
                )
                revision = settings["workspace"]["revision"]
                await fx.on_core(
                    lambda approval=approval, revision=revision: (
                        fx.host.context.products.orchestration_policies.put(
                            approval, expected_revision=revision
                        )
                    )
                )
                saved = (await fx.client.get("/v1/orchestration-policies")).json()
                row = next(r for r in saved["eligibility"] if r["task_type"] == "implementation")
                assert row["promoted"] is True
                assert row["auto_run_eligible"] is row["auto_replan_eligible"] is False
                await fx.on_core(
                    lambda revision=revision: fx.host.context.products.orchestration_policies.put(
                        policy, expected_revision=revision + 1
                    )
                )
            assert await fx.on_core(
                lambda: fx.host.context.runtime.replan._automation_allowed(policy)
            ) is (number == 1)
            planned = await generate(
                fx,
                planning(draft_id=f"wdraft_gate_{number}", workflow_definition_id=f"gate_{number}"),
            )
            assert planned["explanation"]["auto_run_eligible"] is (number == 1)
        duplicate = await fx.client.post(
            "/v1/management/workflow-evaluation", request | {"command_id": "cmd_duplicate"}
        )
        assert duplicate.status == 400
        invalid = await fx.client.post(
            "/v1/management/workflow-evaluation",
            request | {"command_id": "cmd_wrong_mode", "direct_run_id": pairs[0][1]},
        )
        assert invalid.status == 400
        foreign = await fx.client.post(
            "/v1/management/workflow-feedback",
            {
                "command_id": "cmd_foreign",
                "workflow_run_id": "wrun_elsewhere",
                "kind": "too_complex",
            },
        )
        assert foreign.status == 400
        assert (await fx.client.get("/v1/management/workflow-evaluation")).json() == dashboard
    finally:
        fx.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["removed_planner", "added_reviewer"])
async def test_repeated_role_edits_propose_only_after_independent_drafts(tmp_path, action):
    fx = ServerFixture(tmp_path)
    try:
        await publish_roles(fx)
        for number in range(2):
            roles = ["planner"] if action == "removed_planner" else ["direct"]
            await generate(
                fx,
                planning(
                    "Implement a small change",
                    requested_roles=roles,
                    draft_id=f"wdraft_before_{number}",
                    workflow_definition_id=f"before_{number}",
                ),
            )
            roles = ["direct"] if action == "removed_planner" else ["reviewer"]
            after = (
                await generate(
                    fx,
                    planning(
                        "Implement a small change",
                        requested_roles=roles,
                        draft_id=f"wdraft_after_{number}",
                        workflow_definition_id=f"after_{number}",
                    ),
                )
            )["workflow_draft"]["draft"]
            body = {
                "source": after["source"] | {"workflow_definition_id": f"before_{number}"},
                "expected_row_version": 1,
            }
            reply = await fx.client.request(
                "PUT", f"/v1/workflow-drafts/wdraft_before_{number}", body=body
            )
            assert reply.status == 200, reply.body
            assert (
                await fx.client.request(
                    "PUT", f"/v1/workflow-drafts/wdraft_before_{number}", body=body
                )
            ).status == 200
            candidates = (await fx.client.get("/v1/management/workflow-policy-candidates")).json()[
                "items"
            ]
            assert len(candidates) == number
        candidate = candidates[0]
        assert candidate["feedback_kind"] == action
        assert (await fx.client.get("/v1/orchestration-policies")).json()["workspace"][
            "revision"
        ] == 0

        def verify():
            from morrow.application.workflows.integrity import verify_workflow_rows

            return fx.host.context.journal.transact(
                lambda _: verify_workflow_rows(fx.host.context.journal._backend.executor())
            )

        assert await fx.on_core(verify) == (True, ())
    finally:
        fx.close()


async def seed_candidate(fx):
    from morrow.application.management_requests import WorkflowFeedbackRequest

    for number in range(2):
        run, _ = await completed(fx, f"candidate_{number}", multi=True)
        request = WorkflowFeedbackRequest(
            command_id=f"cmd_seed_{number}", workflow_run_id=run, kind="too_complex"
        )
        await fx.on_core(
            lambda request=request: fx.host.context.products.workflow_drafts.feedback.submit(
                request
            )
        )
    return (await fx.client.get("/v1/management/workflow-policy-candidates")).json()["items"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [50, 105])
async def test_learning_pagination_includes_orchestration_only_pages(tmp_path, count):
    from morrow.core.workflows.feedback import WorkflowPolicyCandidate

    fx = ServerFixture(tmp_path, scripts=[["result"]] * 8)
    try:
        await publish_roles(fx)
        candidate = await seed_candidate(fx)

        def seed_pages():
            feedback = fx.host.context.products.workflow_drafts.feedback
            sample = feedback.records.get(
                WorkflowPolicyCandidate, feedback.workspace_id, candidate["candidate_id"]
            )
            # Populate review history from a valid candidate; page reads must not
            # depend on live Learning execution or on other candidate kinds.
            for number in range(count - 1):
                feedback.records.put(
                    sample.model_copy(
                        update={
                            "candidate_id": f"wpc_page_{number}",
                            "status": "rejected",
                            "decision_command_id": f"cmd_page_{number}",
                            "row_version": 2,
                        }
                    )
                )

        await fx.on_core(seed_pages)
        seen = set()
        for page in range((count + 49) // 50):
            result = (await fx.client.get(f"/v1/management/learning?page={page}")).json()
            assert result["candidates"] == result["proposals"] == []
            items = result["orchestration"]["items"]
            assert len(items) == min(50, count - page * 50)
            assert result["next_cursor"] == (
                str((page + 1) * 50) if count > (page + 1) * 50 else None
            )
            seen.update(c["candidate_id"] for c in items)
        assert len(seen) == count
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_settings_show_wildcard_replan_authorization_without_promotion(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        policy = OrchestrationPolicy(auto_replan_mode="allow_low_risk")
        await fx.on_core(
            lambda: fx.host.context.products.orchestration_policies.put(policy, expected_revision=0)
        )
        rows = (await fx.client.get("/v1/orchestration-policies")).json()["eligibility"]
        assert len(rows) == 6
        assert all(not row["promoted"] and not row["auto_run_eligible"] for row in rows)
        assert all(row["auto_replan_eligible"] for row in rows)
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_accept_intent_recovers_after_yaml_write_and_detects_stale_policy(tmp_path):
    fx = ServerFixture(tmp_path, scripts=[["result"]] * 10)
    try:
        await publish_roles(fx)
        candidate = await seed_candidate(fx)

        def install_failure():
            policies = fx.host.context.products.orchestration_policies
            original = policies.put

            def interrupted(*args, **kwargs):
                policies.put = original
                original(*args, **kwargs)
                raise OSError("scripted interruption after YAML")

            policies.put = interrupted

        await fx.on_core(install_failure)
        body = {"command_id": "cmd_recover_policy", "action": "accept", "expected_row_version": 1}
        path = "/v1/management/workflow-policy-decision/" + candidate["candidate_id"]
        assert (await fx.client.post(path, body)).status == 400
        interrupted = (await fx.client.get("/v1/management/workflow-policy-candidates")).json()[
            "items"
        ][0]
        assert interrupted["status"] == "applying"

        # A fresh service instance reads the durable intent, as on restart.
        def rebuild():
            from morrow.application.workflows.feedback import WorkflowFeedbackService

            old = fx.host.context.context_management.workflow_feedback
            fx.host.context.context_management.workflow_feedback = WorkflowFeedbackService(
                old.journal,
                workspace_id=old.workspace_id,
                policies=old.policies,
                artifacts=old.artifacts,
            )

        await fx.on_core(rebuild)
        response = await fx.client.post(path, body)
        assert response.status == 200, response.body
        assert (await fx.client.get("/v1/orchestration-policies")).json()["workspace"][
            "revision"
        ] == 1
        assert (await fx.client.get("/v1/management/workflow-policy-candidates")).json()["items"][
            0
        ]["status"] == "accepted"
    finally:
        fx.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["workspace", "global"])
async def test_candidate_stale_rejection_and_policy_unchanged(tmp_path, scope):
    fx = ServerFixture(tmp_path, scripts=[["result"]] * 10)
    try:
        await publish_roles(fx)
        candidate = await seed_candidate(fx)
        await fx.on_core(
            lambda: fx.host.context.products.orchestration_policies.put(
                OrchestrationPolicy(scope=scope, required_roles=("reviewer",)),
                expected_revision=0,
            )
        )
        path = "/v1/management/workflow-policy-decision/" + candidate["candidate_id"]
        response = await fx.client.post(
            path,
            {"command_id": "cmd_stale_candidate", "action": "accept", "expected_row_version": 1},
        )
        assert response.status == 409, response.body
        response = await fx.client.post(
            path,
            {"command_id": "cmd_reject_candidate", "action": "reject", "expected_row_version": 1},
        )
        assert response.status == 200
        policies = (await fx.client.get("/v1/orchestration-policies")).json()[scope]
        assert policies["revision"] == 1 and policies["policies"][0]["required_roles"] == [
            "reviewer"
        ]
    finally:
        fx.close()


def test_v28_migration_preserves_records_and_adds_feedback_tables(tmp_path):
    from morrow.adapters.state.journal import SqliteOperationalJournal
    from morrow.adapters.state.migrations import MigrationRegistry, production_registry
    from morrow.adapters.state.operational import OperationalStore
    from morrow.core.store import SUPPORTED_SCHEMA_VERSION, StoreOpenMode
    from test_stage4_journal import _session, _task

    registry = MigrationRegistry(supported_version=28)
    for migration in production_registry().pending(0):
        if migration.version <= 28:
            registry.add(migration)
    store = OperationalStore(tmp_path / "state", registry=registry)
    handle = store.initialize()
    old = SqliteOperationalJournal(handle)
    old.create_session(_session(), task=_task())
    session = old.get_session("ws_a", "ses_1")
    handle.close()
    store = OperationalStore(tmp_path / "state")
    report = store.migrate()
    assert report.from_version == 28 and report.to_version == SUPPORTED_SCHEMA_VERSION
    with store.open(StoreOpenMode.READ_WRITE) as handle:
        journal = SqliteOperationalJournal(handle)
        assert journal.get_session("ws_a", "ses_1") == session
        assert journal.workflow_feedback.list(WorkflowFeedback, "ws_a") == ()


@pytest.mark.asyncio
async def test_invalid_draft_reference_remains_editable_and_safe_query_is_cli_identical(tmp_path):
    import json

    from typer.testing import CliRunner

    from morrow.interfaces import cli

    fx = ServerFixture(tmp_path)
    try:
        await publish_roles(fx)
        draft = (await generate(fx, planning()))["workflow_draft"]["draft"]
        source = draft["source"]
        source["nodes"][0]["agent_definition_ref"]["version_id"] = "adev_missing"
        reply = await fx.client.request(
            "PUT",
            "/v1/workflow-drafts/wdraft_plan",
            body={"source": source, "expected_row_version": 1},
        )
        assert reply.status == 200, reply.body
        assert reply.json()["result"]["workflow_draft"]["draft"]["status"] == "invalid"
        # Actual CLI entry uses the same read-only state after the Core releases its lease.
        api_projection = (await fx.client.get("/v1/management/workflow-evaluation")).json()
        state_root, workspace = fx.state_root, fx.workspace_dir
    finally:
        fx.close()
    response = CliRunner().invoke(
        cli.app,
        [
            "manage",
            "query",
            "workflow-evaluation",
            "--state-root",
            str(state_root),
            "--dir",
            str(workspace),
        ],
    )
    assert response.exit_code == 0, response.output
    assert json.loads(response.output) == api_projection


@pytest.mark.asyncio
async def test_model_swaps_capture_exact_preference_without_routing_write(tmp_path):
    from morrow.core.agent_definitions import AgentDefinitionSource
    from morrow.core.agent_runs import AgentDefinitionRef
    from test_stage7_serial_scheduler import OTHER

    fx = ServerFixture(tmp_path)
    try:
        await publish_roles(fx)
        version = await fx.on_core(
            lambda: fx.host.context.management.agent_publication.publish(
                AgentDefinitionSource(
                    definition_id="other_direct",
                    name="Direct",
                    role_prompt="Answer the task",
                    model_selection=OTHER,
                ),
                source_revision=0,
                expected_head_revision=0,
                command_id="cmd_other_direct",
            )
        )
        ref = AgentDefinitionRef(
            definition_id=version.source.definition_id,
            version_id=version.version_id,
            content_hash=version.content_hash,
        )
        for number in range(2):
            generated = await generate(
                fx,
                planning(
                    "Explain the schema",
                    draft_id=f"wdraft_swap_{number}",
                    workflow_definition_id=f"swap_{number}",
                ),
            )
            source = generated["workflow_draft"]["draft"]["source"]
            source["nodes"][0]["agent_definition_ref"] = ref.model_dump(mode="json")
            result = await fx.client.request(
                "PUT",
                f"/v1/workflow-drafts/wdraft_swap_{number}",
                body={"source": source, "expected_row_version": 1},
            )
            assert result.status == 200, result.body
        candidates = (await fx.client.get("/v1/management/learning")).json()["orchestration"][
            "items"
        ]
        assert len(candidates) == 1 and candidates[0]["feedback_kind"] == "model_changed"
        assert candidates[0]["proposed_policy"]["model_preferences_by_role"][
            "direct"
        ] == OTHER.model_dump(mode="json")
        assert (await fx.client.get("/v1/management/context")).json()["pending_learning_count"] == 1
        assert (await fx.client.get("/v1/orchestration-policies")).json()["workspace"][
            "revision"
        ] == 0
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_fallback_agent_definitions_preserve_planned_roles_for_feedback_and_evaluation(
    tmp_path,
):
    from morrow.core.workflows.definitions import WorkflowDefinitionSource

    fx = ServerFixture(tmp_path, scripts=[["role result"]] * 12)
    try:

        def minimal_catalog():
            for role in ("direct", "explorer"):
                fx.host.context.management.publish_agent(
                    f"builtin_{role}", expected_head_revision=0, command_id=f"cmd_publish_{role}"
                )

        await fx.on_core(minimal_catalog)
        generated = await generate(fx, planning("Refactor a large system"))
        source = WorkflowDefinitionSource.model_validate(
            generated["workflow_draft"]["draft"]["source"]
        )
        feedback = fx.host.context.products.workflow_drafts.feedback
        roles = await fx.on_core(lambda: {n.node_id: feedback.role(n) for n in source.nodes})
        assert roles == {
            "coder": "coder",
            "explorer": "explorer",
            "planner": "planner",
            "reviewer": "reviewer",
        }
        # Remove the Planner and reconnect the ordinary chain. Feedback must
        # track the task role even though its reusable Definition is Explorer.
        planner = next(n for n in source.nodes if n.node_id == "planner")
        predecessor = next(e.from_node_id for e in source.edges if e.to_node_id == "planner")
        changed = source.model_copy(
            update={
                "nodes": tuple(
                    n.model_copy(
                        update={
                            "input_bindings": tuple(
                                b.model_copy(
                                    update={
                                        "node_output": b.node_output.model_copy(
                                            update={"node_id": predecessor}
                                        )
                                    }
                                )
                                if b.source == "node_output" and b.node_output.node_id == "planner"
                                else b
                                for b in n.input_bindings
                            )
                        }
                    )
                    for n in source.nodes
                    if n != planner
                ),
                "edges": tuple(
                    e.model_copy(update={"from_node_id": predecessor})
                    if e.from_node_id == "planner"
                    else e
                    for e in source.edges
                    if e.to_node_id != "planner"
                ),
            }
        )
        response = await fx.client.request(
            "PUT",
            "/v1/workflow-drafts/wdraft_plan",
            body={"source": changed.model_dump(mode="json"), "expected_row_version": 1},
        )
        assert response.status == 200, response.body
        recorded = await fx.on_core(
            lambda: feedback.records.list(WorkflowFeedback, feedback.workspace_id)
        )
        assert any(f.kind == "removed_planner" for f in recorded)
        run, _ = await completed(fx, "fallback", multi=True)
        response = await fx.client.post(
            "/v1/management/workflow-feedback",
            {
                "command_id": "cmd_fallback_review",
                "workflow_run_id": run,
                "kind": "reviewer_useful",
            },
        )
        assert response.status == 200, response.body
        dashboard = (await fx.client.get("/v1/management/workflow-evaluation")).json()
        summary = next(r for r in dashboard["runs"] if r["workflow_run_id"] == run)
        assert [r["node_id"] for r in summary["reviewer_findings"]] == ["reviewer"]
        assert dashboard["metrics"]["reviewer_useful_tasks"] == 1
    finally:
        fx.close()


def test_task_role_keeps_numbered_roles_and_non_role_definition_fallback():
    from morrow.application.workflows.roles import task_role
    from morrow.core.agent_definitions import AgentDefinitionSource

    definition = AgentDefinitionSource(
        definition_id="custom_review",
        name="Review",
        role_prompt="Review",
        derived_from_definition_id="builtin_reviewer",
        derived_from_source_hash="a" * 64,
    )
    assert task_role("reviewer_2") == "reviewer"
    assert task_role("explorer_1", definition) == "explorer"
    assert task_role("independent_check", definition) == "reviewer"
    assert task_role("custom_task") == "custom_task"


@pytest.mark.asyncio
async def test_user_run_patch_records_once_and_preserves_root_sample(tmp_path):
    from test_stage7_serial_scheduler import MODEL, WS
    from test_stage8_global_replan import corrected
    from test_stage8_patch_continuation import _patch, _paused_after_first_node

    fx, base, parent = await _paused_after_first_node(tmp_path)
    try:
        patch = _patch(parent, base, source=corrected(base))
        fx.runtime.patches.save(patch, active_model=MODEL)
        fx.runtime.patches.save(patch, active_model=MODEL)
        records = fx.journal.workflow_feedback.list(WorkflowFeedback, WS)
        assert len(records) == 1
        assert records[0].kind == "graph_edit" and records[0].subject_kind == "run"
        assert records[0].sample_id == parent.root_task_run_id
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_dead_read_node_metric_and_feedback_reference_integrity(tmp_path):
    from morrow.application.workflows.evaluation import WorkflowEvaluationService
    from morrow.application.workflows.feedback import WorkflowFeedbackService
    from morrow.application.workflows.integrity import verify_workflow_rows
    from test_stage7_serial_scheduler import WS, DagFixture, chain_node, pair_source, publish, start

    fx = DagFixture(tmp_path, scripts=[["conclusion"], ["unused evidence"]])
    try:

        def source(ref):
            # Both nodes really execute; only alpha's result is requested.
            return pair_source(
                ref,
                binding=False,
                edges=(WorkflowEdge(from_node_id="alpha", to_node_id="unused"),),
                nodes=(
                    chain_node(ref, "alpha", "Conclude"),
                    chain_node(ref, "unused", "Unused survey"),
                ),
            )

        _, publication = publish(fx, source)
        run = start(fx, publication.revision).run
        await fx.runtime.scheduler.run(run.workflow_run_id)
        feedback = WorkflowFeedbackService(
            fx.journal, workspace_id=WS, artifacts=fx.runtime.finalizer.artifacts
        )
        data = WorkflowEvaluationService(feedback).dashboard()
        assert data["runs"][0]["dead_node_ids"] == ["unused"]
        assert data["runs"][0]["requests"] == 2
        assert data["runs"][0]["outcome"] is not None

        def corrupt(txn):
            txn._backend.executor().execute(
                "INSERT INTO workflow_feedback VALUES(?,?,?)",
                (
                    "wfb_broken",
                    WS,
                    WorkflowFeedback(
                        feedback_id="wfb_broken",
                        workspace_id=WS,
                        subject_kind="run",
                        subject_id="wrun_missing",
                        sample_id="task_missing",
                        task_type="general",
                        kind="too_complex",
                        created_at=fx.journal.now(),
                    ).model_dump_json(),
                ),
            )
            return verify_workflow_rows(txn._backend.executor())

        assert fx.journal.transact(corrupt) == (False, ("workflow_integrity",))
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_actual_cli_feedback_command_reuses_api_receipt(tmp_path, monkeypatch):
    import json

    from typer.testing import CliRunner

    from morrow.interfaces import cli

    fx = ServerFixture(tmp_path, scripts=[["result"]] * 4)
    try:
        await publish_roles(fx)
        run, _ = await completed(fx, "parity", multi=True)
        body = {
            "command_id": "cmd_cli_api_feedback",
            "workflow_run_id": run,
            "kind": "missing_exploration",
        }
        path = "/v1/management/workflow-feedback"
        assert (await fx.client.post(path, body)).status == 200
        replay = (await fx.client.post(path, body)).json()["result"]
        request_file = tmp_path / "feedback-request.json"
        request_file.write_text(json.dumps(body))

        def invoke():
            monkeypatch.setattr(
                cli, "_state_services", lambda **_: (fx.app, None, fx.host.context.api, None, None)
            )
            return CliRunner().invoke(
                cli.app, ["manage", "command", "workflow-feedback", str(request_file)]
            )

        result = await fx.on_core(invoke)
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == replay
    finally:
        fx.close()
