"""Suggestion-only GraphPlanner through scripted Providers and real Core/Draft boundaries."""

from __future__ import annotations

import asyncio
import json

import pytest

from morrow.core.orchestration import OrchestrationPolicy, TaskClassification
from test_stage8_core_api import ServerFixture


async def publish_roles(fx):
    def publish():
        management = fx.host.context.management
        for role in ("direct", "explorer", "coder", "planner", "reviewer", "synthesizer"):
            management.publish_agent(
                f"builtin_{role}", expected_head_revision=0, command_id=f"cmd_publish_{role}"
            )

    await fx.on_core(publish)


def planning(objective="Fix a typo in one file", **overrides):
    value = {
        "draft_id": "wdraft_plan",
        "workflow_definition_id": "planned",
        "name": "Planned task",
        "task": {"objective": objective},
        "use_model": False,
    }
    return value | overrides


async def generate(fx, request):
    reply = await fx.client.post("/v1/workflow-planner", {"planning": request})
    assert reply.status == 200, reply.body
    return reply.json()["result"]


@pytest.mark.asyncio
async def test_small_task_uses_direct_and_reopens_editable_draft_without_running(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        await publish_roles(fx)
        result = await generate(fx, planning())
        draft = result["workflow_draft"]["draft"]
        assert draft["status"] == "valid"
        assert [node["node_id"] for node in draft["source"]["nodes"]] == ["direct"]
        assert result["explanation"]["auto_run_eligible"] is False
        assert result["explanation"]["budget"]["max_agent_generation_requests"] is None
        assert (await fx.client.get("/v1/catalog/workflow-revisions?definition_id=planned")).json()[
            "workflow_revisions"
        ] == []
        reopened = (await fx.client.get("/v1/workflow-drafts/wdraft_plan")).json()["workflow_draft"]
        assert reopened["draft"]["planner"] == result["metadata"]
        assert (await generate(fx, planning()))["workflow_draft"] == result["workflow_draft"]
        conflict = await fx.client.post(
            "/v1/workflow-planner", {"planning": planning("Explain another task")}
        )
        assert conflict.status == 409
        source = draft["source"] | {"name": "Edited"}
        edited = await fx.client.request(
            "PUT",
            "/v1/workflow-drafts/wdraft_plan",
            body={"source": source, "expected_row_version": 1},
        )
        assert edited.status == 200
        assert edited.json()["result"]["workflow_draft"]["draft"]["status"] == "valid"
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_complex_implementation_has_reviewer_and_research_has_distinct_fanin(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        await publish_roles(fx)
        implementation = await generate(
            fx, planning("Implement a large change across the API and persistence layers")
        )
        imp = implementation["workflow_draft"]["draft"]["source"]
        assert {n["node_id"] for n in imp["nodes"]} == {"explorer", "coder", "reviewer"}
        assert implementation["explanation"]["writing_nodes"] == ["coder"]
        research = await generate(
            fx,
            planning(
                task={
                    "objective": "Research and compare storage designs",
                    "scope": ["sqlite", "postgres"],
                },
                draft_id="wdraft_research",
                workflow_definition_id="research",
            ),
        )
        src = research["workflow_draft"]["draft"]["source"]
        assert {n["node_id"] for n in src["nodes"]} == {"explorer_1", "explorer_2", "synthesizer"}
        assert {(e["from_node_id"], e["to_node_id"]) for e in src["edges"]} == {
            ("explorer_1", "synthesizer"),
            ("explorer_2", "synthesizer"),
        }
        assert all(n["access_mode"] == "read" for n in src["nodes"])
        assert all(o["kind"] == "TextResult" for n in src["nodes"] for o in n["output_contracts"])
        assert src["default_budget"]["max_concurrency"] == 1
        scopes = [
            n["task_contract"]["scope"] for n in src["nodes"] if n["node_id"].startswith("explorer")
        ]
        assert scopes == [["sqlite"], ["postgres"]]
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_guardrail_and_excluded_role_are_preserved(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        await publish_roles(fx)
        result = await generate(
            fx,
            planning(
                "Refactor a large system without Reviewer",
                budget={"max_agent_generation_requests": 2},
            ),
        )
        source = result["workflow_draft"]["draft"]["source"]
        assert result["explanation"]["mode"] == "direct"
        assert source["default_budget"]["max_agent_generation_requests"] == 2
        excluded = await generate(
            fx, planning("Refactor a large system without Reviewer", draft_id="wdraft_excluded")
        )
        assert "reviewer" not in {
            n["node_id"] for n in excluded["workflow_draft"]["draft"]["source"]["nodes"]
        }
        waiting = await generate(
            fx,
            planning(
                "Refactor a large system",
                draft_id="wdraft_required",
                requested_roles=["reviewer"],
                budget={"max_agent_generation_requests": 1},
            ),
        )
        assert waiting["workflow_draft"] is None
        assert waiting["explanation"]["mode"] == "needs_input"
        assert any("guardrail_conflict" in d for d in waiting["diagnostics"])
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_workspace_policy_overrides_global_and_autorun_needs_real_promotion(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        await publish_roles(fx)

        async def put(policy, revision):
            return await fx.client.request(
                "PUT",
                "/v1/orchestration-policies",
                body={"policy": policy.model_dump(mode="json"), "expected_revision": revision},
            )

        first = await put(OrchestrationPolicy(policy_id="global_rules", multi_agent=False), 0)
        assert first.status == 200, first.body
        local = OrchestrationPolicy(
            policy_id="workspace_rules",
            scope="workspace",
            required_roles=("reviewer",),
            auto_run_mode="allow_promoted",
            evidence=("fake_evidence",),
        )
        second = await put(local, 0)
        assert second.status == 200, second.body
        result = await generate(fx, planning())
        assert result["metadata"]["policy_id"] == "workspace_rules"
        assert "reviewer" in {
            n["node_id"] for n in result["workflow_draft"]["draft"]["source"]["nodes"]
        }
        assert result["explanation"]["auto_run_eligible"] is False
        assert result["explanation"]["auto_run_reason"] == "paired_evidence_missing"
        stale = await put(local.model_copy(update={"multi_agent": False}), 0)
        assert stale.status == 409
        # Persistence uses the Extension authority, including OCC with unrelated Skill/MCP changes.
        loaded = (await fx.client.get("/v1/orchestration-policies")).json()
        assert loaded["workspace"]["revision"] == 1
        assert loaded["workspace"]["policies"][0]["auto_replan_mode"] == "approval_only"
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_one_scripted_classification_no_tools_and_durable_retry_skips_provider(tmp_path):
    classification = TaskClassification(
        task_type="refactor",
        number_of_areas=4,
        requires_code_write=True,
        review_value="high",
        expected_duration_class="long",
    )
    fx = ServerFixture(tmp_path, scripts=[[classification.model_dump_json()]])
    try:
        await publish_roles(fx)
        result = await generate(fx, planning("Refactor the subsystem", use_model=True))
        assert result["metadata"]["classification"] == "model"
        assert result["explanation"]["mode"] == "multi"
        replay = await generate(fx, planning("Refactor the subsystem", use_model=True))
        assert replay == result
        assert len(fx.bank.providers) == 2  # composition + one classification
        provider = fx.bank.providers[-1]
        assert len(provider.stream_calls) == 1
        assert provider.stream_tools[0] == ()
    finally:
        fx.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        '{"task_type":"general","reasoning":"private"}',
        '{"task_type":"general","budget":1000}',
    ],
)
async def test_invalid_classification_falls_back_without_leaking_raw_output(tmp_path, payload):
    fx = ServerFixture(tmp_path, scripts=[[payload]])
    try:
        await publish_roles(fx)
        result = await generate(fx, planning(use_model=True))
        assert result["metadata"]["classification"] == "invalid"
        assert result["explanation"]["mode"] == "direct"
        assert payload not in json.dumps(result)
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_compile_failure_regenerates_once_then_validates_direct_fallback(tmp_path):
    from morrow.application.workflows.compiler import CompilationResult
    from morrow.core.workflows.drafts import WorkflowDraftDiagnostic

    fx = ServerFixture(tmp_path)
    calls = []
    try:
        await publish_roles(fx)

        def install():
            drafts = fx.host.context.products.workflow_drafts
            original = drafts.validate

            def reject_multi(source):
                calls.append(len(source.nodes))
                if len(source.nodes) > 1:
                    return CompilationResult(None, ()), (
                        WorkflowDraftDiagnostic(
                            severity="error", code="graph_cycle", message="remove the cycle"
                        ),
                    )
                return original(source)

            drafts.validate = reject_multi

        await fx.on_core(install)
        result = await generate(fx, planning("Implement a large change across modules"))
        assert calls == [3, 3, 1]
        assert result["explanation"]["mode"] == "direct"
        assert result["workflow_draft"]["draft"]["status"] == "valid"
        assert any("graph_cycle" in d for d in result["diagnostics"])
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_missing_catalog_waits_for_user_and_catalog_query_never_publishes(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        assert (await fx.client.get("/v1/catalog/planning")).json()["nodes"] == []
        result = await generate(fx, planning())
        assert result["workflow_draft"] is None
        assert result["explanation"]["mode"] == "needs_input"
        assert "catalog_missing" in result["diagnostics"][0]
        assert (await fx.client.get("/v1/workflow-drafts")).json()["workflow_drafts"] == []
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_read_only_constraint_cannot_be_widened_by_classifier(tmp_path):
    classified = TaskClassification(
        requires_code_write=True,
        number_of_areas=3,
        risk_level="high",
        review_value="high",
        expected_duration_class="long",
    )
    fx = ServerFixture(tmp_path, scripts=[[classified.model_dump_json()]])
    try:
        await publish_roles(fx)
        result = await generate(
            fx,
            planning(
                task={
                    "objective": "Investigate a large refactor",
                    "constraints": ["Read only; do not modify files"],
                },
                use_model=True,
            ),
        )
        assert result["metadata"]["features"]["requires_code_write"] is False
        assert all(
            n["access_mode"] == "read" for n in result["workflow_draft"]["draft"]["source"]["nodes"]
        )
        assert all(
            "Read only; do not modify files" in n["task_contract"]["constraints"]
            for n in result["workflow_draft"]["draft"]["source"]["nodes"]
        )
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_explicit_small_scope_is_not_expanded_by_model(tmp_path):
    classified = TaskClassification(
        requires_code_write=True,
        number_of_areas=8,
        expected_scope=("speculative-module",),
        review_value="high",
        expected_duration_class="long",
    )
    fx = ServerFixture(tmp_path, scripts=[[classified.model_dump_json()]])
    try:
        await publish_roles(fx)
        result = await generate(fx, planning(use_model=True))
        assert result["explanation"]["mode"] == "direct"
        assert result["metadata"]["features"]["number_of_areas"] == 1
        assert result["metadata"]["features"]["expected_scope"] == []
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_exact_configured_model_selected_only_by_explicit_role_policy(tmp_path):
    from morrow.core.agent_definitions import AgentDefinitionSource
    from test_stage7_serial_scheduler import OTHER

    fx = ServerFixture(tmp_path)
    try:
        await publish_roles(fx)

        def publish_other():
            service = fx.host.context.management.agent_publication
            return service.publish(
                AgentDefinitionSource(
                    definition_id="other_direct",
                    name="Direct",
                    role_prompt="Answer the task",
                    model_selection=OTHER,
                ),
                source_revision=0,
                expected_head_revision=0,
                command_id="cmd_publish_other",
            )

        await fx.on_core(publish_other)
        first = await generate(fx, planning("Explain the schema"))
        assert first["explanation"]["models"][0]["model_id"] == "m1"
        policy = OrchestrationPolicy(model_preferences_by_role={"direct": OTHER})
        await fx.on_core(
            lambda: fx.host.context.products.orchestration_policies.put(policy, expected_revision=0)
        )
        second = await generate(fx, planning("Explain the schema", draft_id="wdraft_other"))
        assert second["explanation"]["models"][0]["model_id"] == "m2"
        assert (
            second["workflow_draft"]["draft"]["source"]["nodes"][0]["agent_definition_ref"][
                "definition_id"
            ]
            == "other_direct"
        )
        await fx.on_core(
            lambda: setattr(
                fx.host.context.products.workflow_drafts, "model_available", lambda _: False
            )
        )
        assert (await fx.client.get("/v1/catalog/planning")).json()["nodes"] == []
        assert (await generate(fx, planning(draft_id="wdraft_unavailable")))[
            "workflow_draft"
        ] is None
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_disabled_and_revoked_agents_are_excluded_and_no_invalid_draft_is_saved(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        await publish_roles(fx)

        def disable():
            management = fx.host.context.management
            for role in ("direct", "explorer", "coder", "planner", "reviewer", "synthesizer"):
                management.set_agent_enabled(
                    f"builtin_{role}", enabled=False, expected_head_revision=1
                )

        await fx.on_core(disable)
        assert (await fx.client.get("/v1/catalog/planning")).json()["nodes"] == []
        result = await generate(fx, planning("Implement a large change"))
        assert result["workflow_draft"] is None
        assert (await fx.client.get("/v1/workflow-drafts")).json()["workflow_drafts"] == []
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_scout_is_one_bounded_read_without_exposing_unknown_names(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        await publish_roles(fx)
        (fx.workspace_dir / "pyproject.toml").write_text("[project]\nname='demo'\n")
        (fx.workspace_dir / "private-file-name.txt").write_text("private content")
        result = await generate(fx, planning(scout=True))
        assert result["metadata"]["brief"]["project_markers"] == ["pyproject.toml"]
        assert result["metadata"]["brief"]["observed_entries"] == 2
        assert "private-file-name" not in json.dumps(result)
        assert "private content" not in json.dumps(result)
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_pending_classification_does_not_block_core_commands_and_policy_is_rechecked(
    tmp_path,
):
    fx = ServerFixture(tmp_path)
    entered = asyncio.Event()
    outer = asyncio.get_running_loop()
    release = None
    request_task = None
    try:
        await publish_roles(fx)

        def install():
            nonlocal release
            release = asyncio.Event()

            class PausedClassifier:
                async def classify(self, *_):
                    outer.call_soon_threadsafe(entered.set)
                    await release.wait()
                    return TaskClassification(
                        number_of_areas=4, requires_code_write=True, expected_duration_class="long"
                    )

            fx.host.context.products.graph_planner.classifier = PausedClassifier()

        await fx.on_core(install)
        request_task = asyncio.create_task(
            generate(fx, planning("Implement the task", use_model=True))
        )
        await asyncio.wait_for(entered.wait(), 5)
        policy = OrchestrationPolicy(multi_agent=False)
        response = await asyncio.wait_for(
            fx.client.request(
                "PUT",
                "/v1/orchestration-policies",
                body={"policy": policy.model_dump(mode="json"), "expected_revision": 0},
            ),
            5,
        )
        assert response.status == 200
        await fx.on_core(release.set)
        result = await request_task
        assert result["explanation"]["mode"] == "direct"
        assert result["metadata"]["policy_revision"] == 1
    finally:
        if release is not None:
            await fx.on_core(release.set)
        if request_task is not None:
            await asyncio.gather(request_task, return_exceptions=True)
        fx.close()


def test_policy_document_retains_legacy_digest_and_other_extension_fields(tmp_path):
    from morrow.adapters.state.extension_yaml import ExtensionYamlStore, extension_document_digest
    from morrow.application.workflows.orchestration_policy import OrchestrationPolicyService
    from morrow.core.domain import canonical_json_bytes, sha256_digest
    from morrow.core.skills.bindings import GlobalExtensionDocument, SkillBinding

    store = ExtensionYamlStore(tmp_path)
    document = GlobalExtensionDocument(bindings=(SkillBinding(skill_id="example", scope="global"),))
    old = document.model_dump(mode="json", by_alias=True, exclude={"orchestration", "updated_at"})
    assert extension_document_digest(document) == sha256_digest(canonical_json_bytes(old))
    store.write_global(document, expected_revision=0)
    service = OrchestrationPolicyService(store, workspace_id="ws_test")
    service.put(OrchestrationPolicy(), expected_revision=1)
    current = store.load_global().value
    assert current.bindings == document.bindings
    assert current.orchestration[0].revision == 2
    assert store.load_global_backup().value.bindings == document.bindings
    assert (
        OrchestrationPolicyService(ExtensionYamlStore(tmp_path), workspace_id="ws_test")
        .resolve("research")
        .revision
        == 2
    )


@pytest.mark.asyncio
async def test_similar_implementation_tasks_change_shape_with_scope(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        await publish_roles(fx)
        first = await generate(
            fx,
            planning(task={"objective": "Implement the feature", "scope": ["api", "db", "tests"]}),
        )
        second = await generate(
            fx,
            planning(
                draft_id="wdraft_wider",
                task={
                    "objective": "Implement the feature",
                    "scope": ["api", "db", "tests", "gui", "docs"],
                },
            ),
        )
        assert first["explanation"]["node_count"] == 3
        assert second["explanation"]["node_count"] == 4
        assert "planner" in {
            n["node_id"] for n in second["workflow_draft"]["draft"]["source"]["nodes"]
        }
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_generated_workflow_manual_run_works_without_promotion(tmp_path):
    from test_stage8_core_api import create_session_and_task

    fx = ServerFixture(tmp_path, scripts=[["evidence"], ["implementation"], ["review passed"]])
    try:
        await publish_roles(fx)
        policy = OrchestrationPolicy(auto_run_mode="allow_promoted")
        await fx.on_core(
            lambda: fx.host.context.products.orchestration_policies.put(policy, expected_revision=0)
        )
        planned = await generate(fx, planning("Implement a large change across modules"))
        assert planned["explanation"]["auto_run_eligible"] is False
        frozen = await fx.client.post(
            "/v1/workflow-drafts/wdraft_plan/freeze",
            {"command_id": "cmd_manual_freeze", "expected_row_version": 1},
        )
        assert frozen.status == 200, frozen.body
        revision = frozen.json()["result"]["workflow_revision"]["workflow_revision_id"]
        session, task, row = await create_session_and_task(fx.client)
        started = await fx.client.post(
            "/v1/workflow-runs",
            {
                "command_id": "cmd_manual_run",
                "workflow_definition_id": "planned",
                "workflow_revision_id": revision,
                "session_id": session,
                "root_task_run_id": task,
                "expected_root_row_version": row,
                "objective": "Implement a large change across modules",
            },
        )
        assert started.status == 200, started.body
        run_id = started.json()["result"]["run"]["workflow_run_id"]
        for _ in range(2000):
            if await _run_complete(fx, run_id):
                break
        else:
            pytest.fail("generated manual Workflow did not complete")
        result = (await fx.client.get(f"/v1/workflow-runs/{run_id}")).json()["view"]
        assert result["run"]["status"] == "completed"
        assert all(node["node"]["status"] == "completed" for node in result["nodes"])
    finally:
        fx.close()


async def _run_complete(fx, run_id):
    view = (await fx.client.get(f"/v1/workflow-runs/{run_id}")).json()["view"]
    return view["run"]["status"] == "completed"


@pytest.mark.asyncio
async def test_command_conflict_rejected_before_classification_and_invalid_request_is_redacted(
    tmp_path,
):
    fx = ServerFixture(tmp_path)
    try:
        await publish_roles(fx)
        first = await fx.client.post(
            "/v1/workflow-planner", {"command_id": "cmd_planning", "planning": planning()}
        )
        assert first.status == 200
        second = await fx.client.post(
            "/v1/workflow-planner",
            {
                "command_id": "cmd_planning",
                "planning": planning(draft_id="wdraft_new", use_model=True),
            },
        )
        assert second.status == 409
        assert len(fx.bank.providers) == 1
        secret = "sk-" + "x" * 40
        rejected = await fx.client.post("/v1/workflow-planner", {"planning": planning(secret)})
        assert rejected.status == 400
        assert secret.encode() not in rejected.body
    finally:
        fx.close()


def test_policy_cli_uses_the_same_yaml_owner_and_reports_conflict(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from morrow.interfaces import workflow_cli
    from morrow.interfaces.cli import app

    fx = ServerFixture(tmp_path)
    try:
        monkeypatch.setattr(workflow_cli, "build_application", lambda **_: fx.app)
        policy_file = tmp_path / "policy.json"
        policy_file.write_text(
            OrchestrationPolicy(scope="workspace", multi_agent=False).model_dump_json()
        )
        args = [
            "workflow",
            "policy",
            "set",
            str(policy_file),
            "--expected-revision",
            "0",
            "--workspace-id",
            fx.workspace_id,
        ]
        runner = CliRunner()
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["workspace"]["policies"][0]["multi_agent"] is False
        policy_file.write_text(
            OrchestrationPolicy(scope="workspace", multi_agent=True).model_dump_json()
        )
        assert runner.invoke(app, args).exit_code == 2
        viewed = runner.invoke(
            app, ["workflow", "policy", "show", "--workspace-id", fx.workspace_id]
        )
        assert viewed.exit_code == 0
        assert json.loads(viewed.output)["workspace"]["revision"] == 1
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_planner_inherits_exact_enabled_skill_and_excludes_disabled_skill(tmp_path):
    from dataclasses import replace

    from morrow.bootstrap import build_skill_services
    from morrow.core.agent_definitions import AgentDefinitionSource
    from test_skill_selection import _source

    fx = ServerFixture(tmp_path)
    try:
        await publish_roles(fx)

        def install():
            skills = build_skill_services(
                fx.app, workspace_id=fx.workspace_id, journal=fx.host.context.journal
            )
            installed = skills.lifecycle.install(
                _source(tmp_path), scope_id=fx.workspace_id, confirmed=True
            )
            skills.lifecycle.enable("writer-skill", scope_id=fx.workspace_id)
            publication = fx.host.context.management.agent_publication
            publication.catalog = replace(
                publication.catalog, skill_version_ids=frozenset({installed.version_id})
            )
            publication.publish(
                AgentDefinitionSource(
                    definition_id="skilled",
                    name="Skilled",
                    role_prompt="Produce a report",
                    skill_version_ids=(installed.version_id,),
                ),
                source_revision=0,
                expected_head_revision=0,
                command_id="cmd_skilled",
            )
            return skills, installed.version_id

        skills, version_id = await fx.on_core(install)
        catalog = (await fx.client.get("/v1/catalog/planning")).json()
        assert next(n for n in catalog["nodes"] if n["name"] == "Skilled")["skills"] == [version_id]
        result = await generate(fx, planning("Produce a report", requested_roles=["skilled"]))
        assert result["workflow_draft"]["draft"]["status"] == "valid"
        await fx.on_core(lambda: skills.lifecycle.disable("writer-skill", scope_id=fx.workspace_id))
        blocked = await generate(
            fx,
            planning(
                "Produce a report", requested_roles=["skilled"], draft_id="wdraft_disabled_skill"
            ),
        )
        assert blocked["workflow_draft"] is None
        assert any("catalog_missing" in d for d in blocked["diagnostics"])
    finally:
        fx.close()
