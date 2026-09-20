"""Current ReplanRequest/Signal contracts: bounded facts only."""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from morrow.core.workflows.replan import (
    AffectedTaskFact,
    ReplanEvidenceRef,
    ReplanRequest,
    ReplanSignal,
)
from morrow.core.workflows.runs import WorkflowStatus
from test_stage7_serial_scheduler import WS, DagFixture, ScriptBank, pair_source, publish, start


def test_current_request_accepts_evidence_and_affected_facts_without_a_graph():
    request = ReplanRequest(
        schema_version=2,
        reason="missing_dependency",
        evidence_refs=(
            ReplanEvidenceRef(
                kind="submitted_slot",
                slot="result",
                note="survey found a commit-order bug",
            ),
            ReplanEvidenceRef(kind="artifact", artifact_id="art_evidence1"),
            ReplanEvidenceRef(kind="node_output", node_id="gamma", slot="result"),
        ),
        affected_facts=(
            AffectedTaskFact(
                node_id="alpha",
                summary="Phase three assumed a cache invalidation bug",
                impact="invalidate",
            ),
        ),
    )
    dumped = request.model_dump(mode="json")
    assert dumped["schema_version"] == 2
    assert dumped["evidence_refs"][0]["slot"] == "result"
    assert ReplanRequest.model_validate(dumped) == request


def test_current_request_rejects_empty_facts():
    with pytest.raises(ValidationError, match="replan signals require evidence or affected facts"):
        ReplanRequest(schema_version=2, reason="new_evidence")


def test_leaf_cannot_submit_graph_or_authority():
    facts = {
        "schema_version": 2,
        "evidence_refs": [{"kind": "submitted_slot", "slot": "result"}],
    }
    for key, value in {
        "patch": {"workflow_patch_id": "wpatch_x"},
        "source": {"nodes": []},
        "graph": {"nodes": ["alpha"]},
        "risk_level": "low",
        "auto_apply": True,
        "approved": True,
        "permissions": {"write": True},
        "start": True,
        "plan_spec": {"nodes": []},
        "child_run_id": "wrun_child",
        "policy_id": "default",
        "nodes": [],
        "edges": [],
    }.items():
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            ReplanRequest.model_validate({**facts, key: value})


def test_evidence_ref_shapes():
    with pytest.raises(ValidationError, match="artifact evidence requires artifact_id"):
        ReplanEvidenceRef(kind="artifact")
    with pytest.raises(ValidationError, match="node_output evidence requires node_id and slot"):
        ReplanEvidenceRef(kind="node_output", node_id="gamma")
    with pytest.raises(ValidationError, match="submitted_slot evidence requires slot"):
        ReplanEvidenceRef(kind="submitted_slot")


def _v2_request(*, conflict=False, node_id="alpha"):
    facts = (
        AffectedTaskFact(
            node_id=node_id,
            summary="Phase three assumed a cache bug",
            impact="invalidate",
        ),
        AffectedTaskFact(
            node_id=node_id,
            summary="Scope is actually larger",
            impact="scope_change",
        ),
    )
    return ReplanRequest(
        schema_version=2,
        reason="new_evidence",
        evidence_refs=(
            ReplanEvidenceRef(
                kind="submitted_slot",
                slot="result",
                note="survey found a missed dependency",
            ),
        ),
        affected_facts=facts if conflict else facts[:1],
    )


@pytest.mark.asyncio
async def test_v2_signal_commits_at_closure_and_stays_unconsumed(tmp_path):
    from morrow.core.models import AssistantMessage, FunctionToolCall

    request = _v2_request()
    submission = AssistantMessage(
        tool_calls=(
            FunctionToolCall(
                id="call_replan",
                name="submit_node_result",
                arguments='{"outputs": {}, "replan":' + request.model_dump_json() + "}",
            ),
        )
    )
    fx = DagFixture(tmp_path, bank=ScriptBank())
    try:
        fx.bank.scripts.extend([[submission, "phase one closes"], ["must remain queued"]])
        _, publication = publish(fx, pair_source)
        parent = start(fx, publication.revision).run
        result = await fx.runtime.scheduler.run(parent.workflow_run_id)
        assert result.status is WorkflowStatus.PAUSED
        nodes = {n.node_id: n for n in fx.journal.workflows.list_nodes(WS, parent.workflow_run_id)}
        assert nodes["gamma"].status is WorkflowStatus.COMPLETED
        assert nodes["alpha"].status is WorkflowStatus.QUEUED
        signals = fx.journal.workflows.list_replan_signals(WS, parent.workflow_run_id)
        assert len(signals) == 1
        stored = signals[0].request
        assert stored.schema_version == 2
        assert stored.evidence_refs[0].slot == "result"
        assert stored.affected_facts[0].node_id == "alpha"
        pending = fx.journal.workflows.list_replan_signals(
            WS, parent.workflow_run_id, pending_only=True
        )
        assert len(pending) == 1
        assert fx.runtime.replan.list(parent.workflow_run_id) == ()
        assert fx.runtime.replan.process_signals(parent.workflow_run_id) == ()
        assert (
            fx.journal.workflows.list_replan_signals(WS, parent.workflow_run_id, pending_only=True)
            == pending
        )
    finally:
        fx.close()


def test_signal_created_at_is_timezone_aware():
    request = _v2_request()
    signal = ReplanSignal(
        signal_id="rsig_facts",
        workspace_id="ws_test",
        workflow_run_id="wrun_parent",
        node_run_id="nrun_gamma",
        request=request,
        created_at=datetime(2026, 9, 7, tzinfo=UTC),
    )
    assert signal.created_at.tzinfo is not None
    assert ReplanSignal.model_validate_json(signal.model_dump_json()).request.reason == (
        "new_evidence"
    )


def test_pin_past_keeps_admitted_nodes_and_budget():
    from morrow.application.workflows.plan_spec import pin_past
    from morrow.core.agent_runs import AgentDefinitionRef
    from morrow.core.workflows.contracts import NodeOutputRef, OutputContract, TaskContract
    from morrow.core.workflows.definitions import (
        AgentNodeSource,
        WorkflowBudget,
        WorkflowDefinitionSource,
        WorkflowEdge,
    )

    ref = AgentDefinitionRef(definition_id="general", version_id="adev_1", content_hash="ab" * 32)

    def node(identity, objective):
        return AgentNodeSource(
            node_id=identity,
            agent_definition_ref=ref,
            task_contract=TaskContract(objective=objective),
            output_contracts=(OutputContract(slot="result"),),
            access_mode="read",
        )

    required = (NodeOutputRef(node_id="audit", output_slot="result"),)
    base = WorkflowDefinitionSource(
        workflow_definition_id="task_example",
        name="task",
        nodes=(node("work", "original work"), node("audit", "original audit")),
        edges=(WorkflowEdge(from_node_id="work", to_node_id="audit"),),
        required_outputs=required,
        default_budget=WorkflowBudget(max_agent_generation_requests=3),
    )
    candidate = WorkflowDefinitionSource(
        workflow_definition_id="task_example",
        name="task",
        nodes=(
            node("work", "rewritten work"),
            node("fix", "new fix"),
            node("audit", "new audit"),
        ),
        edges=(
            WorkflowEdge(from_node_id="work", to_node_id="fix"),
            WorkflowEdge(from_node_id="fix", to_node_id="audit"),
        ),
        required_outputs=required,
        default_budget=WorkflowBudget(max_agent_generation_requests=99),
    )
    pinned = pin_past(base, candidate, {"work"})
    by_id = {item.node_id: item for item in pinned.nodes}
    assert set(by_id) == {"work", "fix", "audit"}
    assert by_id["work"].task_contract.objective == "original work"
    assert by_id["fix"].task_contract.objective == "new fix"
    assert by_id["audit"].task_contract.objective == "new audit"
    assert pinned.default_budget.max_agent_generation_requests == 3


def _replan_message(*, conflict=False):
    from morrow.core.models import AssistantMessage, FunctionToolCall

    request = _v2_request(conflict=conflict, node_id="audit")
    return AssistantMessage(
        tool_calls=(
            FunctionToolCall(
                id="call_replan",
                name="submit_node_result",
                arguments='{"outputs": {}, "replan":' + request.model_dump_json() + "}",
            ),
        )
    )


async def _paused_replan(tmp_path, replan_nodes, *, conflict=False):
    from test_stage8_chat_submission import new_session
    from test_stage8_core_api import ServerFixture, wait_for_run
    from test_workflow_plan_change import _start_body
    from test_workflow_task_planning import request, spec

    fx = ServerFixture(tmp_path, scripts=[[[json.dumps(spec("work", "audit"))]]])
    sid, root = await new_session(fx)
    planned = await fx.client.post(root + "/task-plan", request(sid, command_id="cmd_plan"))
    assert planned.status == 200, planned.body
    view = (await fx.client.get(root + "/task-plan")).json()
    fx.bank.scripts.append([_replan_message(conflict=conflict), "phase one"])
    fx.bank.scripts.append([[json.dumps(spec(*replan_nodes))]])
    started = await fx.client.post(root + "/task-plan/start", _start_body(sid, view))
    assert started.status == 200, started.body
    run_id = started.json()["run"]["workflow_run_id"]
    await wait_for_run(fx.client, run_id, "paused")
    ws = fx.workspace_id

    def snapshot():
        planning = fx.host.context.chat.planning
        binding = planning.repo.binding(ws, sid)
        operations = planning.repo.operations(ws, sid)
        events = planning.repo.events(ws, sid)
        run = fx.host.context.journal.workflows.get_run(ws, run_id)
        pending = fx.host.context.journal.workflows.list_replan_signals(
            ws, run_id, pending_only=True
        )
        return binding, operations, events, run, pending

    binding = operations = events = run = pending = None
    for _ in range(4000):
        binding, operations, events, run, pending = await fx.on_core(snapshot)
        revise = tuple(item for item in operations if item.operation == "revise")
        if run.status is WorkflowStatus.PAUSED and revise and revise[-1].status != "running":
            break
        await asyncio.sleep(0)
    return fx, sid, root, run_id, binding, operations, events, run, pending


@pytest.mark.asyncio
async def test_fact_signal_pauses_then_opens_frozen_replan_candidate(tmp_path):
    fx, sid, root, run_id, binding, operations, events, run, pending = await _paused_replan(
        tmp_path, ("work", "fix", "audit")
    )
    try:
        assert run.status is WorkflowStatus.PAUSED
        assert binding is not None and binding.mode == "change"
        assert binding.parent_run_id == run_id
        assert len(pending) == 1
        assert pending[0].request.schema_version == 2
        revise = tuple(item for item in operations if item.operation == "revise")
        assert revise and revise[-1].status == "succeeded"
        draft = (
            await fx.on_core(
                lambda: fx.host.context.chat.planning.drafts.get(binding.current_draft_id)
            )
        ).draft
        by_id = {node.node_id: node for node in draft.source.nodes}
        assert "work" in by_id
        assert "Perform work" in by_id["work"].task_contract.objective
        assert "fix" in by_id
        assert any(event.get("replan") for event in events)
        replan_calls = [
            messages
            for provider in fx.bank.providers
            for messages in provider.stream_calls
            if messages and "mid-run replan" in getattr(messages[0], "content", "")
        ]
        assert replan_calls
        assert "past_node_ids" in replan_calls[0][1].content
        assert '"work"' in replan_calls[0][1].content
        before = len(revise)
        await fx.on_core(lambda: fx.host.context.runtime.replan.process_signals(run_id))
        operations_after = await fx.on_core(
            lambda: fx.host.context.chat.planning.repo.operations(fx.workspace_id, sid)
        )
        assert len(tuple(item for item in operations_after if item.operation == "revise")) == before
        fx.bank.scripts.extend([["fix done"], ["audit done"]])
        latest = (await fx.client.get(root + "/task-plan")).json()
        assert latest["candidate"]["digest"]
        assert latest["candidate"]["origin"] == "replan"
        assert latest["candidate"]["reason"]
        accepted = await fx.client.post(
            root + "/task-plan/apply-change",
            {
                "command_id": "cmd_accept_replan",
                "session_id": sid,
                "decision": "accept_change",
                "action_source": "button",
                "interaction_id": "message_accept",
                "candidate_digest": latest["candidate"]["digest"],
                "expected_parent_row_version": latest["candidate"]["expected_parent_row_version"],
            },
        )
        assert accepted.status == 200, accepted.body
        child = accepted.json()["child"]
        assert child is not None and child["workflow_run_id"] != run_id
        replay = await fx.client.post(
            root + "/task-plan/apply-change",
            {
                "command_id": "cmd_accept_replan",
                "session_id": sid,
                "decision": "accept_change",
                "action_source": "button",
                "interaction_id": "message_accept",
                "candidate_digest": latest["candidate"]["digest"],
                "expected_parent_row_version": latest["candidate"]["expected_parent_row_version"],
            },
        )
        assert replay.status == 200 and replay.json()["replayed"] is True
        parent = await fx.on_core(
            lambda: fx.host.context.journal.workflows.get_run(fx.workspace_id, run_id)
        )
        assert parent.status is WorkflowStatus.SUPERSEDED

        def integrity():
            from morrow.application.workflows.integrity import verify_workflow_rows

            backend = fx.host.context.journal._backend

            class Executor:
                def execute(self, sql, parameters=()):
                    return backend.read_all(sql, tuple(parameters))

            return verify_workflow_rows(Executor())

        assert await fx.on_core(integrity) == (True, ())
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_identical_replan_without_new_graph_stops(tmp_path):
    fx, sid, _root, run_id, binding, operations, events, run, pending = await _paused_replan(
        tmp_path, ("work", "audit")
    )
    try:
        revise = tuple(item for item in operations if item.operation == "revise")
        assert revise and revise[-1].status == "failed"
        assert "replan_no_progress" in revise[-1].diagnostics
        assert run.status is WorkflowStatus.PAUSED
        assert pending
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_blocking_review_pauses_for_repair_instead_of_completing(tmp_path):
    from morrow.core.models import AssistantMessage, FunctionToolCall
    from test_stage8_chat_submission import new_session
    from test_stage8_core_api import ServerFixture, wait_for_run
    from test_workflow_plan_change import _start_body
    from test_workflow_task_planning import request, spec

    review = AssistantMessage(
        tool_calls=(
            FunctionToolCall(
                id="call_review",
                name="submit_node_result",
                arguments=(
                    '{"outputs": {"review": {"verdict": "request_changes", '
                    '"findings": ["needs a follow-up fix"]}}}'
                ),
            ),
        )
    )
    followup = {
        "nodes": [
            {
                "node_id": "work",
                "title": "work",
                "task": "Perform work",
                "agent": "preset:general",
                "responsibility": "implementation",
                "depends_on": [],
                "completion": ["work is verified"],
            },
            {
                "node_id": "fix",
                "title": "fix",
                "task": "Repair the blocking findings",
                "agent": "preset:general",
                "responsibility": "implementation",
                "depends_on": ["work"],
                "completion": ["fix is verified"],
            },
            {
                "node_id": "recheck",
                "title": "recheck",
                "task": "Re-review the repair",
                "agent": "preset:review",
                "responsibility": "review",
                "depends_on": ["fix"],
                "completion": ["recheck is verified"],
            },
        ],
        "deliverables": ["recheck"],
    }
    fx = ServerFixture(tmp_path, scripts=[[[json.dumps(spec("work", "audit"))]]])
    sid, root = await new_session(fx)
    try:
        planned = await fx.client.post(root + "/task-plan", request(sid, command_id="cmd_plan"))
        assert planned.status == 200, planned.body
        view = (await fx.client.get(root + "/task-plan")).json()
        fx.bank.scripts.append(["work complete"])
        fx.bank.scripts.append([review, "reviewed"])
        fx.bank.scripts.append([[json.dumps(followup)]])
        started = await fx.client.post(root + "/task-plan/start", _start_body(sid, view))
        assert started.status == 200, started.body
        run_id = started.json()["run"]["workflow_run_id"]
        await wait_for_run(fx.client, run_id, "paused")
        ws = fx.workspace_id

        def snapshot():
            run = fx.host.context.journal.workflows.get_run(ws, run_id)
            pending = fx.host.context.journal.workflows.list_replan_signals(
                ws, run_id, pending_only=True
            )
            operations = fx.host.context.chat.planning.repo.operations(ws, sid)
            binding = fx.host.context.chat.planning.repo.binding(ws, sid)
            return run, pending, operations, binding

        run = pending = operations = binding = None
        for _ in range(4000):
            run, pending, operations, binding = await fx.on_core(snapshot)
            revise = tuple(item for item in operations if item.operation == "revise")
            if run.status is WorkflowStatus.PAUSED and (
                not revise or revise[-1].status != "running"
            ):
                break
            await asyncio.sleep(0)
        assert run.status is WorkflowStatus.PAUSED
        assert run.result_status is None
        assert pending
        assert pending[0].request.evidence_refs[0].slot == "review"
        assert binding is not None and binding.mode == "change"
        draft = (
            await fx.on_core(
                lambda: fx.host.context.chat.planning.drafts.get(binding.current_draft_id)
            )
        ).draft
        by_id = {node.node_id: node for node in draft.source.nodes}
        assert "audit" in by_id
        assert (
            "fix" in by_id
            or tuple(item for item in operations if item.operation == "revise")[-1].status
            == "failed"
        )
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_conflicting_replan_signals_are_diagnosed(tmp_path):
    fx, sid, _root, run_id, binding, operations, events, run, pending = await _paused_replan(
        tmp_path, ("work", "fix", "audit"), conflict=True
    )
    try:
        replan_events = [event for event in events if event.get("replan")]
        assert replan_events and replan_events[-1]["conflicting_signals"] is True
        revise = tuple(item for item in operations if item.operation == "revise")
        assert revise and revise[-1].status == "succeeded"
    finally:
        fx.close()
