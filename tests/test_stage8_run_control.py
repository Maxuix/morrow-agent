"""Stage 8 Subplan 7: run-control surface tests.

Patch preview diff + C8 risk classification, the §8.5 approval surface
(enriched wire, allow-session grants and the auto-approval precedent), and the
per-node usage / pre-run cost projections — through the same Core API the GUI
consumes. All Providers are scripted; synchronization never uses wall-clock
sleeps.
"""

from __future__ import annotations

import asyncio

import pytest

from morrow.application.workflows.compiler import compile_workflow
from morrow.application.workflows.patch_preview import classify_patch_risk, diff_compiled
from morrow.core.workflows.contracts import (
    ContractRef,
    NodeOutputBinding,
    NodeOutputRef,
    OutputContract,
)
from morrow.core.workflows.definitions import WorkflowBudget, WorkflowEdge
from test_stage7_serial_scheduler import (
    CATALOG,
    MODEL,
    OTHER,
    WS,
    DagFixture,
    chain_node,
    pair_source,
    publish,
)
from test_stage8_core_api import (
    WRITE_CALL_SCRIPT,
    ServerFixture,
    agent_source,
    create_session_and_task,
    publish_pipeline,
    start_run,
    wait_for_run,
    write_pair_source,
)


def _compile(fx, source, *, active=MODEL):
    versions = {
        node.agent_definition_ref.version_id: fx.journal.agent_definitions.get_version(
            WS, node.agent_definition_ref.version_id
        )
        for node in source.nodes
    }
    result = compile_workflow(source, agent_versions=versions, catalog=CATALOG, active_model=active)
    assert result.candidate is not None, result.diagnostics
    return result.candidate


def _writer_pair(ref, *, direction):
    """Two write-mode nodes, both exported; the ordering edge carries a binding."""

    forward = direction == "forward"
    first, second = ("gamma", "alpha") if forward else ("alpha", "gamma")
    base = pair_source(ref, binding=False)
    nodes = []
    for node in base.nodes:
        update = {"access_mode": "write"}
        if node.node_id == second:
            update["input_bindings"] = (
                NodeOutputBinding(
                    source="node_output",
                    input_name="prior",
                    accepts=ContractRef(kind="TextResult"),
                    node_output=NodeOutputRef(node_id=first, output_slot="result"),
                ),
            )
        nodes.append(node.model_copy(update=update))
    return base.model_copy(
        update={
            "nodes": tuple(nodes),
            "edges": (WorkflowEdge(from_node_id=first, to_node_id=second),),
            "required_outputs": (
                NodeOutputRef(node_id="gamma", output_slot="result"),
                NodeOutputRef(node_id="alpha", output_slot="result"),
            ),
        }
    )


def _trio_source(ref):
    """pair chain plus one removable sink node behind a control edge."""

    base = pair_source(ref)
    return base.model_copy(
        update={
            "nodes": (*base.nodes, chain_node(ref, "zeta", "Auxiliary independent work")),
            "edges": (*base.edges, WorkflowEdge(from_node_id="alpha", to_node_id="zeta")),
        }
    )


# Patch preview: diff and C8 risk classification -------------------------------


def test_patch_preview_low_risk_text_only_change(tmp_path):
    fx = DagFixture(tmp_path)
    try:
        _, publication = publish(fx, pair_source)
        base = publication.revision
        ref = base.nodes[0].agent_definition_ref
        source = pair_source(ref)
        source = source.model_copy(
            update={
                "nodes": tuple(
                    node.model_copy(
                        update={
                            "task_contract": node.task_contract.model_copy(
                                update={"objective": "Conclude with a narrower focus"}
                            )
                        }
                    )
                    if node.node_id == "alpha"
                    else node
                    for node in source.nodes
                )
            }
        )
        candidate = _compile(fx, source)
        diff = diff_compiled(base, candidate)
        assert diff.changed_node_ids == ("alpha",)
        assert diff.added_node_ids == () and diff.removed_node_ids == ()
        risk = classify_patch_risk(base, candidate)
        assert risk.level == "low" and risk.reasons == ()
    finally:
        fx.close()


def test_patch_preview_node_removal_is_elevated(tmp_path):
    fx = DagFixture(tmp_path)
    try:
        _, publication = publish(fx, _trio_source)
        base = publication.revision
        ref = base.nodes[0].agent_definition_ref
        source = _trio_source(ref)
        source = source.model_copy(
            update={
                "nodes": tuple(node for node in source.nodes if node.node_id != "zeta"),
                "edges": tuple(edge for edge in source.edges if edge.to_node_id != "zeta"),
            }
        )
        candidate = _compile(fx, source)
        diff = diff_compiled(base, candidate)
        assert diff.removed_node_ids == ("zeta",)
        risk = classify_patch_risk(base, candidate)
        assert risk.level == "elevated" and "node_removed" in risk.reasons
    finally:
        fx.close()


def test_patch_preview_cap_relaxation_is_elevated(tmp_path):
    fx = DagFixture(tmp_path)
    try:
        _, publication = publish(fx, pair_source)
        base = publication.revision
        ref = base.nodes[0].agent_definition_ref
        relaxed = pair_source(
            ref,
            budget=WorkflowBudget(
                max_agent_generation_requests=20,
                default_node_max_agent_generation_requests=3,
                admission_timeout_seconds=300,
                max_concurrency=1,
            ),
        )
        candidate = _compile(fx, relaxed)
        risk = classify_patch_risk(base, candidate)
        assert risk.level == "elevated" and "cap_or_deadline_relaxed" in risk.reasons
        assert diff_compiled(base, candidate).budget_changed is True

        dropped_deadline = pair_source(
            ref,
            budget=WorkflowBudget(
                max_agent_generation_requests=10,
                default_node_max_agent_generation_requests=3,
                admission_timeout_seconds=None,
                max_concurrency=1,
            ),
        )
        candidate = _compile(fx, dropped_deadline)
        risk = classify_patch_risk(base, candidate)
        assert "cap_or_deadline_relaxed" in risk.reasons

        # Tightening a cap is the low-risk direction.
        tightened = pair_source(
            ref,
            budget=WorkflowBudget(
                max_agent_generation_requests=5,
                default_node_max_agent_generation_requests=3,
                admission_timeout_seconds=300,
                max_concurrency=1,
            ),
        )
        candidate = _compile(fx, tightened)
        risk = classify_patch_risk(base, candidate)
        assert risk.level == "low", risk.reasons
    finally:
        fx.close()


def test_patch_preview_required_output_retarget_is_elevated(tmp_path):
    fx = DagFixture(tmp_path)
    try:
        _, publication = publish(fx, _trio_source)
        base = publication.revision
        ref = base.nodes[0].agent_definition_ref
        source = _trio_source(ref).model_copy(
            update={"required_outputs": (NodeOutputRef(node_id="zeta", output_slot="result"),)}
        )
        candidate = _compile(fx, source)
        diff = diff_compiled(base, candidate)
        assert diff.required_outputs_changed is True
        risk = classify_patch_risk(base, candidate)
        assert "required_outputs_retargeted" in risk.reasons
    finally:
        fx.close()


def test_patch_preview_model_boundary_change_is_elevated(tmp_path):
    fx = DagFixture(tmp_path)
    try:
        _, publication = publish(fx, pair_source)
        base = publication.revision
        ref = base.nodes[0].agent_definition_ref
        # Same provider, different model: the data boundary still moved.
        candidate = _compile(fx, pair_source(ref), active=OTHER)
        risk = classify_patch_risk(base, candidate)
        assert risk.level == "elevated" and "provider_model_boundary_changed" in risk.reasons
    finally:
        fx.close()


def test_patch_preview_control_edge_removal_is_elevated(tmp_path):
    fx = DagFixture(tmp_path)
    try:

        def with_redundant_control(ref):
            base = pair_source(ref)
            return base.model_copy(
                update={
                    "nodes": (
                        *base.nodes,
                        chain_node(ref, "zeta", "Final sink work", bindings=()),
                    ),
                    "edges": (
                        *base.edges,
                        WorkflowEdge(from_node_id="alpha", to_node_id="zeta"),
                        WorkflowEdge(from_node_id="gamma", to_node_id="zeta"),
                    ),
                }
            )

        _, publication = publish(fx, with_redundant_control)
        base = publication.revision
        ref = base.nodes[0].agent_definition_ref
        source = with_redundant_control(ref).model_copy(
            update={
                "edges": tuple(
                    edge
                    for edge in with_redundant_control(ref).edges
                    if not (edge.from_node_id == "gamma" and edge.to_node_id == "zeta")
                )
            }
        )
        candidate = _compile(fx, source)
        diff = diff_compiled(base, candidate)
        assert diff.removed_edges == ("gamma->zeta",)
        risk = classify_patch_risk(base, candidate)
        assert "control_edge_removed" in risk.reasons
        assert "node_removed" not in risk.reasons
    finally:
        fx.close()


def test_patch_preview_writer_reorder_is_elevated(tmp_path):
    fx = DagFixture(tmp_path)
    try:
        _, publication = publish(
            fx,
            lambda ref: _writer_pair(ref, direction="forward"),
            agent=agent_source(access_mode_ceiling="write"),
        )
        base = publication.revision
        ref = base.nodes[0].agent_definition_ref
        reordered = _writer_pair(ref, direction="reverse")
        candidate = _compile(fx, reordered)
        risk = classify_patch_risk(base, candidate)
        assert "writer_order_changed" in risk.reasons
        assert "control_edge_removed" not in risk.reasons
    finally:
        fx.close()


def test_patch_preview_report_dependency_removal_is_elevated(tmp_path):
    fx = DagFixture(tmp_path)
    try:

        def reporting(ref, **changes):
            base = pair_source(ref, binding=False, **changes)
            nodes = []
            for node in base.nodes:
                if node.node_id == "gamma":
                    node = node.model_copy(
                        update={
                            "output_contracts": (OutputContract(slot="result", kind="TestReport"),)
                        }
                    )
                elif node.node_id == "alpha":
                    node = node.model_copy(
                        update={
                            "input_bindings": (
                                NodeOutputBinding(
                                    source="node_output",
                                    input_name="prior",
                                    accepts=ContractRef(kind="TestReport"),
                                    node_output=NodeOutputRef(
                                        node_id="gamma", output_slot="result"
                                    ),
                                ),
                            )
                        }
                    )
                nodes.append(node)
            return base.model_copy(update={"nodes": tuple(nodes)})

        _, publication = publish(fx, reporting)
        base = publication.revision
        ref = base.nodes[0].agent_definition_ref
        unbound = reporting(ref).model_copy(
            update={
                "nodes": tuple(
                    node.model_copy(update={"input_bindings": ()})
                    if node.node_id == "alpha"
                    else node
                    for node in reporting(ref).nodes
                )
            }
        )
        candidate = _compile(fx, unbound)
        risk = classify_patch_risk(base, candidate)
        assert "report_dependency_removed" in risk.reasons
        assert "control_edge_removed" not in risk.reasons
    finally:
        fx.close()


def test_patch_preview_contract_relaxation_is_elevated(tmp_path):
    fx = DagFixture(tmp_path)
    try:
        _, publication = publish(fx, pair_source)
        base = publication.revision
        ref = base.nodes[0].agent_definition_ref
        relaxed = pair_source(ref).model_copy(
            update={
                "nodes": tuple(
                    node.model_copy(
                        update={
                            "output_contracts": (
                                OutputContract(slot="result"),
                                OutputContract(slot="notes", required_for_node_completion=False),
                            )
                        }
                    )
                    if node.node_id == "gamma"
                    else node
                    for node in pair_source(ref).nodes
                )
            }
        )
        candidate = _compile(fx, relaxed)
        risk = classify_patch_risk(base, candidate)
        assert "output_contract_relaxed" in risk.reasons
    finally:
        fx.close()


# Approval surface ---------------------------------------------------------------


@pytest.fixture
def fx(tmp_path):
    fixture = ServerFixture(tmp_path)
    yield fixture
    fixture.close()


async def _park_at_write_approval(fx, *, scripts=None):
    from morrow.core.agent_definitions import ToolRequirement

    agent = agent_source(
        access_mode_ceiling="write",
        tool_requirements=(ToolRequirement(name="update_configuration", requirement="required"),),
    )
    revision = await publish_pipeline(fx, agent=agent, make_source=write_pair_source)
    fx.bank.scripts.extend(scripts or [WRITE_CALL_SCRIPT, ["phase three"]])
    session_id, task_id, task_version = await create_session_and_task(fx.client)
    started = await start_run(
        fx.client, revision.workflow_revision_id, session_id, task_id, task_version
    )
    assert started.status == 200, started.body
    run_id = started.json()["result"]["run"]["workflow_run_id"]

    for _ in range(4000):
        await asyncio.sleep(0)
        pending = (await fx.client.get("/v1/approvals")).json()["approvals"]
        if pending:
            return run_id, pending[0]
    raise AssertionError("approval never became pending")


@pytest.mark.asyncio
async def test_approval_wire_carries_the_full_surface(fx):
    run_id, pending = await _park_at_write_approval(fx)

    assert pending["tool_name"] == "update_configuration"
    assert pending["workflow_run_id"] == run_id
    assert pending["node_id"] == "gamma"
    assert pending["node_run_id"] is not None
    assert pending["agent_id"] == "helper"
    assert pending["effect_class"]
    assert pending["risk_level"] in {"low", "medium", "high"}
    assert pending["session_scope_allowed"] is True
    assert any("workspace_profile" in item for item in pending["affected_objects"])
    # The redaction boundary still holds: no full tool arguments or intent.
    assert "arguments" not in pending and "intent" not in pending
    assert pending["granted_scope"] is None


@pytest.mark.asyncio
async def test_allow_session_records_scope_and_auto_approves_the_next_call(fx):
    # Gamma parks at the write approval, then repeats the same-scope call in
    # the same node session; the second call is covered by the session grant.
    gamma_scripts = [WRITE_CALL_SCRIPT[0], WRITE_CALL_SCRIPT[0], ["configuration finished"]]
    run_id, pending = await _park_at_write_approval(
        fx, scripts=[gamma_scripts, ["alpha concluded"]]
    )

    resolved = await fx.client.post(
        f"/v1/approvals/{pending['approval_id']}/resolve",
        {"approved": True, "decision": "allow_session", "command_id": "cmd_allow_session_1"},
    )
    assert resolved.status == 200, resolved.body
    result = resolved.json()["result"]["approval"]
    assert result["resolution"] == "approved"
    assert result["granted_scope"] is not None
    assert result["granted_scope"].startswith("session:")
    assert result["granted_scope"].endswith(pending["requested_scope"])

    # Gamma's second same-scope call is resolved durably at creation — the run
    # never parks at a second approval.
    view = await wait_for_run(fx.client, run_id, "completed")
    assert view["run"]["result_status"] == "succeeded"

    approvals = (await fx.client.get("/v1/approvals?pending=false")).json()["approvals"]
    same_scope = [a for a in approvals if a["requested_scope"] == pending["requested_scope"]]
    assert len(same_scope) == 2
    auto = next(a for a in same_scope if a["approval_id"] != pending["approval_id"])
    assert auto["resolution"] == "approved"
    assert auto["granted_scope"] == result["granted_scope"]
    assert auto["resolved_at"] == auto["created_at"]


@pytest.mark.asyncio
async def test_allow_session_is_refused_for_high_risk_operations(fx):
    from morrow.core.execution import EffectClass, IsolationLabel, session_scope_allowed

    assert session_scope_allowed(EffectClass.RECONCILEABLE_FILE_WRITE, None) is True
    assert session_scope_allowed(EffectClass.UNCONFINED_EXTERNAL_EFFECT, None) is False
    assert (
        session_scope_allowed(EffectClass.RECONCILEABLE_FILE_WRITE, IsolationLabel.UNCONFINED_HOST)
        is False
    )

    # The server enforces the same boundary: a pending medium-risk approval
    # accepts allow_session, and a mismatched decision flag is rejected.
    _run_id, pending = await _park_at_write_approval(fx)
    conflicted = await fx.client.post(
        f"/v1/approvals/{pending['approval_id']}/resolve",
        {"approved": False, "decision": "allow_once"},
    )
    assert conflicted.status == 400


# Usage and pre-run cost projections --------------------------------------------


@pytest.mark.asyncio
async def test_run_preview_and_per_node_usage(fx):
    revision = await publish_pipeline(fx)
    preview = await fx.client.get(
        f"/v1/catalog/workflow-revisions/{revision.workflow_revision_id}/run-preview"
    )
    assert preview.status == 200, preview.body
    summary = preview.json()["pre_run_summary"]
    assert summary["node_count"] == 2
    assert summary["models"] == ["m1"]
    assert summary["providers"] == ["fake-provider"]
    assert summary["max_agent_generation_requests"] == 10
    assert summary["max_concurrency"] == 1
    assert summary["writer_node_ids"] == []

    missing = await fx.client.get("/v1/catalog/workflow-revisions/wrev_missing/run-preview")
    assert missing.status == 404

    fx.bank.scripts.extend([["phase one"], ["phase three"]])
    session_id, task_id, task_version = await create_session_and_task(fx.client)
    started = await start_run(
        fx.client, revision.workflow_revision_id, session_id, task_id, task_version
    )
    assert started.status == 200, started.body
    run_id = started.json()["result"]["run"]["workflow_run_id"]
    view = await wait_for_run(fx.client, run_id, "completed")
    assert view["pre_run_summary"]["node_count"] == 2
    for node in view["nodes"]:
        assert node["agent_generation_request_count"] >= 1
    assert view["agent_generation_request_count"] == sum(
        node["agent_generation_request_count"] for node in view["nodes"]
    )


@pytest.mark.asyncio
async def test_patch_validate_returns_diff_and_risk(tmp_path):
    cell = {}

    def pause_at_alpha(count):
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
            "workflow_patch_id": "wpatch_rc_one",
            "workspace_id": fx.workspace_id,
            "parent_run_id": run_id,
            "base_workflow_revision_id": revision.workflow_revision_id,
            "expected_parent_row_version": parent["run"]["row_version"],
            "source": source,
            "requested_by": "cmd_patch_rc",
        }
        validated = await fx.client.post("/v1/patches/validate", {"patch": patch})
        assert validated.status == 200, validated.body
        result = validated.json()["result"]
        assert result["valid"] is True
        assert result["diff"]["changed_node_ids"] == ["alpha"]
        assert result["diff"]["removed_node_ids"] == []
        assert result["risk"] == {"level": "low", "reasons": []}

        # A cap-relaxing edit previews as elevated on the same surface.
        source["default_budget"]["max_agent_generation_requests"] = 99
        patch2 = {**patch, "workflow_patch_id": "wpatch_rc_two", "source": source}
        validated2 = await fx.client.post("/v1/patches/validate", {"patch": patch2})
        result2 = validated2.json()["result"]
        assert result2["risk"]["level"] == "elevated"
        assert "cap_or_deadline_relaxed" in result2["risk"]["reasons"]
    finally:
        fx.close()


# GUI–CLI conflict parity -------------------------------------------------------


@pytest.mark.asyncio
async def test_gui_cli_outcome_accept_conflict_has_exactly_one_winner(fx):
    """GUI (API) and CLI accept the same TaskOutcome with the same row version:
    the first commits, the loser gets the OCC conflict, and the task closes
    exactly once (no lost update)."""

    import json

    from typer.testing import CliRunner

    from morrow.interfaces.cli import app as cli_app

    fx.bank.scripts.extend([["phase one"], ["phase three"]])
    revision = await publish_pipeline(fx)
    session_id, task_id, task_version = await create_session_and_task(fx.client)
    started = await start_run(
        fx.client, revision.workflow_revision_id, session_id, task_id, task_version
    )
    assert started.status == 200, started.body
    run_id = started.json()["result"]["run"]["workflow_run_id"]
    await wait_for_run(fx.client, run_id, "completed")

    task = (await fx.client.get(f"/v1/tasks/{task_id}")).json()["task"]
    assert task["status"] == "ready_for_acceptance"
    row_version = task["row_version"]

    # The GUI path accepts first.
    accepted = await fx.client.post(
        f"/v1/tasks/{task_id}/accept",
        {"expected_row_version": row_version, "command_id": "cmd_gui_accept_1"},
    )
    assert accepted.status == 200, accepted.body

    # The CLI accept against the same (now stale) facts loses with a conflict.
    cli = CliRunner().invoke(
        cli_app,
        [
            "task",
            "accept",
            task_id,
            "--expected-row-version",
            str(row_version),
            "--workspace-id",
            fx.workspace_id,
            "--dir",
            str(fx.workspace_dir),
            "--state-root",
            str(fx.state_root),
        ],
    )
    assert cli.exit_code == 2
    assert "stale" in cli.output

    # Reverse order: CLI wins, the GUI call gets the 409 conflict.
    fx.bank.scripts.extend([["phase one"], ["phase three"]])
    session2 = await fx.client.post("/v1/sessions", {})
    session2_id = session2.json()["result"]["session"]["session_id"]
    task2 = await fx.client.post("/v1/tasks", {"session_id": session2_id})
    task2_id = task2.json()["result"]["task"]["task_run_id"]
    task2_version = task2.json()["result"]["task"]["row_version"]
    started2 = await start_run(
        fx.client, revision.workflow_revision_id, session2_id, task2_id, task2_version
    )
    assert started2.status == 200, started2.body
    run2_id = started2.json()["result"]["run"]["workflow_run_id"]
    await wait_for_run(fx.client, run2_id, "completed")
    task2_after = (await fx.client.get(f"/v1/tasks/{task2_id}")).json()["task"]
    task2_version = task2_after["row_version"]

    cli_win = CliRunner().invoke(
        cli_app,
        [
            "task",
            "accept",
            task2_id,
            "--expected-row-version",
            str(task2_version),
            "--workspace-id",
            fx.workspace_id,
            "--dir",
            str(fx.workspace_dir),
            "--state-root",
            str(fx.state_root),
        ],
    )
    assert cli_win.exit_code == 0, cli_win.output
    gui_loses = await fx.client.post(
        f"/v1/tasks/{task2_id}/accept",
        {"expected_row_version": task2_version, "command_id": "cmd_gui_accept_2"},
    )
    assert gui_loses.status == 409
    final = (await fx.client.get(f"/v1/tasks/{task2_id}")).json()["task"]
    assert final["status"] == "accepted"
    assert final["row_version"] == task2_version + 1
