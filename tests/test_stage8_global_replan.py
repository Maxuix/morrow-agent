"""Offline global replan policy, immutable evidence, compiler and OCC acceptance."""

from types import SimpleNamespace

import pytest

from morrow.application.workflows.replan import revision_source
from morrow.core.application import ApplicationError
from morrow.core.models import AssistantMessage, FunctionToolCall
from morrow.core.orchestration import OrchestrationPolicy
from morrow.core.workflows.runs import WorkflowStatus
from test_stage7_serial_scheduler import WS, DagFixture, pair_source, publish, start
from test_stage8_patch_continuation import _paused_after_first_node


def policy(coordinator, mode):
    coordinator.policies = SimpleNamespace(
        resolve=lambda _: OrchestrationPolicy(auto_replan_mode=mode)
    )


def corrected(base):
    source = revision_source(base)
    return source.model_copy(
        update={
            "nodes": tuple(
                n.model_copy(
                    update={
                        "task_contract": n.task_contract.model_copy(
                            update={"objective": "Use the completed evidence"}
                        )
                    }
                )
                if n.node_id == "alpha"
                else n
                for n in source.nodes
            )
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,expected", [("approval_only", "pending"), ("allow_low_risk", "applied")]
)
async def test_low_risk_policy_and_durable_audit(tmp_path, mode, expected):
    fx, base, parent = await _paused_after_first_node(tmp_path)
    try:
        coordinator = fx.runtime.replan
        policy(coordinator, mode)
        head = fx.journal.workflows.get_head(WS, base.workflow_definition_id)
        proposal = coordinator.propose(parent.workflow_run_id, corrected(base))
        assert proposal.status == expected
        assert proposal.risk_level == "low"
        assert proposal.auto_applied == (expected == "applied")
        assert fx.journal.workflows.get_head(WS, base.workflow_definition_id) == head
        assert coordinator.list(parent.workflow_run_id)[0]["proposal"]["status"] == expected
        if expected == "pending":
            proposal = coordinator.decide(
                proposal.proposal_id, approved=True, expected_row_version=1
            )
            assert proposal.status == "applied" and not proposal.auto_applied
        assert (
            fx.journal.workflows.get_run(WS, parent.workflow_run_id).status
            is WorkflowStatus.SUPERSEDED
        )
        with pytest.raises(ApplicationError):
            coordinator.decide(proposal.proposal_id, approved=False, expected_row_version=1)
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_stale_proposal_never_replays_on_new_base(tmp_path):
    fx, base, parent = await _paused_after_first_node(tmp_path)
    try:
        c = fx.runtime.replan
        one = c.propose(parent.workflow_run_id, corrected(base))
        two = c.propose(parent.workflow_run_id, corrected(base))
        c.decide(one.proposal_id, approved=True, expected_row_version=1)
        conflict = c.decide(two.proposal_id, approved=True, expected_row_version=1)
        assert conflict.status == "conflict" and conflict.child_run_id is None
        assert conflict.disposition_reason == "stale_base"
    finally:
        fx.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("remove", [True, False])
async def test_text_result_input_removal_requires_approval_but_addition_remains_low(
    tmp_path, remove
):
    from morrow.core.workflows.contracts import TaskContractRef, WorkflowInputBinding

    fx, base, parent = await _paused_after_first_node(tmp_path)
    try:
        coordinator = fx.runtime.replan
        policy(coordinator, "allow_low_risk")
        source = revision_source(base)
        extra = WorkflowInputBinding(
            source="workflow_input",
            input_name="task",
            accepts=TaskContractRef(),
            workflow_input="task",
        )
        source = source.model_copy(
            update={
                "nodes": tuple(
                    n.model_copy(
                        update={"input_bindings": () if remove else (*n.input_bindings, extra)}
                    )
                    if n.node_id == "alpha"
                    else n
                    for n in source.nodes
                )
            }
        )
        proposal = coordinator.propose(parent.workflow_run_id, source)
        if remove:
            assert proposal.status == "pending"
            assert proposal.risk_level == "elevated"
            assert "input_dependency_removed" in proposal.risk_reasons
            assert (
                fx.journal.workflows.get_run(WS, parent.workflow_run_id).status
                is WorkflowStatus.PAUSED
            )
            decided = coordinator.decide(
                proposal.proposal_id, approved=True, expected_row_version=1
            )
            assert decided.status == "applied" and not decided.auto_applied
        else:
            assert proposal.status == "applied" and proposal.auto_applied
            assert proposal.risk_level == "low"
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_guardrail_escalation_and_compile_failure(tmp_path):
    fx, base, parent = await _paused_after_first_node(tmp_path)
    try:
        c = fx.runtime.replan
        policy(c, "allow_low_risk")
        source = corrected(base)
        source = source.model_copy(
            update={
                "default_budget": source.default_budget.model_copy(
                    update={"max_agent_generation_requests": 999}
                )
            }
        )
        proposal = c.propose(parent.workflow_run_id, source)
        assert proposal.status == "pending"
        assert "cap_or_deadline_relaxed" in proposal.risk_reasons
        past = revision_source(base)
        past = past.model_copy(
            update={
                "nodes": tuple(
                    n.model_copy(
                        update={
                            "task_contract": n.task_contract.model_copy(
                                update={"objective": "forge past"}
                            )
                        }
                    )
                    for n in past.nodes
                )
            }
        )
        invalid = c.propose(parent.workflow_run_id, past)
        assert invalid.status == "invalid"
        assert (
            fx.journal.workflows.get_run(WS, parent.workflow_run_id).status is WorkflowStatus.PAUSED
        )
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_handoff_and_decision_rollback_together(tmp_path, monkeypatch):
    fx, base, parent = await _paused_after_first_node(tmp_path)
    try:
        c = fx.runtime.replan
        proposal = c.propose(parent.workflow_run_id, corrected(base))
        before = fx.journal.workflows.list_runs(WS)

        def fail(*args, **kwargs):
            raise RuntimeError("injected decision failure")

        monkeypatch.setattr(fx.journal.workflows, "decide_replan_proposal", fail)
        with pytest.raises(RuntimeError, match="injected"):
            c.decide(proposal.proposal_id, approved=True, expected_row_version=1)
        assert fx.journal.workflows.list_runs(WS) == before
        assert (
            fx.journal.workflows.get_replan_proposal(WS, proposal.proposal_id).status == "pending"
        )
        assert fx.journal.workflows.active_for_root(WS, parent.root_task_run_id) == parent
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_task_class_promotion_stays_approval_only(tmp_path):
    fx, base, parent = await _paused_after_first_node(tmp_path)
    try:
        c = fx.runtime.replan
        c.policies = SimpleNamespace(
            resolve=lambda _: OrchestrationPolicy(
                task_matcher="general",
                auto_replan_mode="allow_low_risk",
            )
        )
        assert c.propose(parent.workflow_run_id, corrected(base)).status == "pending"
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_reject_keeps_parent_paused_and_history(tmp_path):
    fx, base, parent = await _paused_after_first_node(tmp_path)
    try:
        c = fx.runtime.replan
        p = c.propose(parent.workflow_run_id, corrected(base))
        p = c.decide(p.proposal_id, approved=False, expected_row_version=1)
        assert p.status == "rejected" and p.decided_by == "user"
        assert fx.journal.workflows.get_run(WS, parent.workflow_run_id) == parent
        assert c.list(parent.workflow_run_id)[0]["diff"]["changed_node_ids"] == ("alpha",)
    finally:
        fx.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dimension",
    [
        "role_added",
        "role_replaced",
        "conversation_scope_changed",
        "permission_widened",
        "output_contract_relaxed",
        "cap_or_deadline_relaxed",
        "task_scope_changed",
        "explicit_constraint_removed",
        "unclassified_change",
    ],
)
async def test_c8_dimensions_are_never_low_risk(tmp_path, dimension):
    from morrow.application.workflows.patch_preview import classify_patch_risk
    from morrow.core.agent_definitions import ToolRequirement

    fx, base, _ = await _paused_after_first_node(tmp_path)
    try:
        node = base.nodes[0]
        old = base
        changes = {
            "role_replaced": {
                "agent_definition_ref": node.agent_definition_ref.model_copy(
                    update={"version_id": "adev_replaced"}
                )
            },
            "conversation_scope_changed": {"conversation_scope": "invoking_session"},
            "permission_widened": {
                "resolved_tool_requirements": (
                    ToolRequirement(name="bash", requirement="required"),
                )
            },
            "output_contract_relaxed": {"output_contracts": ()},
            "cap_or_deadline_relaxed": {"declared_node_max_agent_generation_requests": None},
            "task_scope_changed": {
                "task_contract": node.task_contract.model_copy(update={"scope": ("wider",)})
            },
            "explicit_constraint_removed": {"task_contract": node.task_contract},
            "unclassified_change": {"node_id": "new_id"},
        }
        if dimension == "role_added":
            candidate = base.model_copy(
                update={"nodes": (*base.nodes, node.model_copy(update={"node_id": "extra"}))}
            )
        elif dimension == "unclassified_change":
            candidate = base.model_copy(update={"compiler_version": "unknown"})
        else:
            if dimension == "explicit_constraint_removed":
                oldnode = node.model_copy(
                    update={
                        "task_contract": node.task_contract.model_copy(
                            update={"constraints": ("Keep review",)}
                        )
                    }
                )
                old = base.model_copy(update={"nodes": (oldnode, *base.nodes[1:])})
            candidate = base.model_copy(
                update={"nodes": (node.model_copy(update=changes[dimension]), *base.nodes[1:])}
            )
        result = classify_patch_risk(old, candidate)
        assert result.level == "elevated" and dimension in result.reasons
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_auto_path_revalidates_compiler_and_records_failure(tmp_path, monkeypatch):
    from morrow.application.workflows.compiler import CompilationResult
    from morrow.application.workflows.patching import PatchValidation

    fx, base, parent = await _paused_after_first_node(tmp_path)
    try:
        c = fx.runtime.replan
        policy(c, "allow_low_risk")
        validate = c.patches.validate
        count = 0

        def invalid_on_apply(*args, **kwargs):
            nonlocal count
            count += 1
            return (
                validate(*args, **kwargs)
                if count == 1
                else PatchValidation(CompilationResult(None, ()), (), ())
            )

        monkeypatch.setattr(c.patches, "validate", invalid_on_apply)
        result = c.propose(parent.workflow_run_id, corrected(base))
        assert count == 2
        assert result.status == "invalid" and not result.auto_applied
        assert result.disposition_reason == "auto_apply_validation_failed"
        assert fx.journal.workflows.get_run(WS, parent.workflow_run_id) == parent
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_blocked_unknown_parent_only_produces_proposals(tmp_path):
    from morrow.core.faults import FaultPoint, InjectedFault, OnceFaultInjector
    from test_stage7_serial_scheduler import reader_agent

    fx = DagFixture(tmp_path)
    try:
        fx.bank.scripts.append(
            [
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(id="call_read", name="read", arguments='{"path":"a.py"}'),
                    )
                )
            ]
        )
        _, pub = publish(fx, pair_source, agent=reader_agent())
        run = start(fx, pub.revision).run
        fx.runtime.scheduler.faults = OnceFaultInjector(
            FaultPoint.CONVERSATION_BEFORE_TOOL_MESSAGE_COMMIT
        )
        with pytest.raises(InjectedFault):
            await fx.runtime.scheduler.run(run.workflow_run_id)
        fx.runtime.scheduler.faults = None
        blocked = await fx.runtime.scheduler.recover(run.workflow_run_id)
        assert blocked.status is WorkflowStatus.BLOCKED
        c = fx.runtime.replan
        policy(c, "allow_low_risk")
        result = c.propose(run.workflow_run_id, corrected(pub.revision))
        assert result.status == "pending" and result.child_run_id is None
        with pytest.raises(ApplicationError, match="fully paused"):
            c.decide(result.proposal_id, approved=True, expected_row_version=1)
        assert fx.journal.workflows.get_run(WS, run.workflow_run_id) == blocked
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_replan_integrity_and_cli_projection(tmp_path, monkeypatch):
    import json

    from typer.testing import CliRunner

    from morrow.application.workflows.integrity import verify_workflow_rows
    from morrow.interfaces import workflow_cli
    from morrow.interfaces.cli import app

    fx, base, parent = await _paused_after_first_node(tmp_path)
    try:
        c = fx.runtime.replan
        p = c.propose(parent.workflow_run_id, corrected(base))
        products = SimpleNamespace(
            workflow_runtime=fx.runtime,
            persistence=SimpleNamespace(store_session=SimpleNamespace(close=lambda: None)),
        )
        monkeypatch.setattr(workflow_cli, "_session_management", lambda *a, **kw: products)
        result = CliRunner().invoke(app, ["workflow", "replan", "list", parent.workflow_run_id])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)[0] == json.loads(
            json.dumps(c.list(parent.workflow_run_id)[0])
        )
        result = CliRunner().invoke(
            app,
            [
                "workflow",
                "replan",
                "decide",
                p.proposal_id,
                "--reject",
                "--expected-row-version",
                "1",
            ],
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["proposal"]["status"] == "rejected"
        assert fx.handle.run_read(verify_workflow_rows) == (True, ())
        fx.handle.run_write(
            lambda ex: ex.execute("UPDATE workflow_replan_proposals SET workspace_id='ws_wrong'")
        )
        assert fx.handle.run_read(verify_workflow_rows) == (False, ("workflow_integrity",))
    finally:
        fx.close()
