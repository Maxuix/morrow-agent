"""Stage 7 Subplan 7: opt-in Direct invoking-session adapter."""

from __future__ import annotations

import asyncio
import json

import pytest

from morrow.application.agent_definitions.builtins import builtin_definitions
from morrow.application.workflows.builtins import builtin_direct_workflow
from morrow.application.workflows.capture import VALIDATION_REPORT_ROLE
from morrow.application.workflows.start import StartWorkflowCommand
from morrow.core.agent_runs import AgentDefinitionRef
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.artifacts import ArtifactKind
from morrow.core.domain import (
    ArtifactReference,
    DurableTaskRun,
    DurableTurn,
    TaskOutcomeEvidenceKind,
    TaskOutcomeTrigger,
    TaskRunStatus,
    TextSafetyProfile,
)
from morrow.core.faults import FaultPoint, InjectedFault, OnceFaultInjector
from morrow.core.models import AssistantMessage, FunctionToolCall
from morrow.core.runtime_control import RuntimeControlKind, RuntimeControlStatus
from morrow.core.workflows.contracts import (
    SUBMIT_NODE_RESULT_NAME,
    NodeOutputRef,
    OutputContract,
    node_output_artifact_id,
    parse_workflow_payload,
)
from morrow.core.workflows.contracts import (
    TestReport as WorkflowTestReport,
)
from morrow.core.workflows.contracts import (
    TestReportItem as WorkflowTestReportItem,
)
from morrow.core.workflows.runs import WorkflowStatus
from test_stage7_isolated_workflow_slice import (
    CONTRACT,
    WS,
    SliceFixture,
    only_node,
    outcomes,
    publish,
    root,
    workflow_source,
)
from test_stage7_multi_agent_pipeline import _script_submit_then_stop


@pytest.fixture
def fx(tmp_path):
    fixture = SliceFixture(tmp_path)
    yield fixture
    fixture.close()


def publish_direct(fx):
    version, isolated = publish(fx)
    ref = isolated.nodes[0].agent_definition_ref
    source = workflow_source(
        ref,
        nodes=(
            workflow_source(ref)
            .nodes[0]
            .model_copy(update={"conversation_scope": "invoking_session"}),
        ),
    )
    publication = fx.compiler.publish(
        source,
        source_revision=1,
        expected_head_revision=1,
        command_id="cmd_direct_publish",
        active_model=isolated.nodes[0].resolved_model_ref,
    )
    return version, publication.revision


def start_direct(fx, revision, *, command_id="cmd_direct_start", message_id="msg_direct"):
    return fx.runtime.start.start(
        StartWorkflowCommand(
            workflow_definition_id="pipeline",
            workflow_revision_id=revision.workflow_revision_id,
            session_id="ses_root",
            root_task_run_id="task_root",
            expected_root_row_version=root(fx).row_version,
            contract=CONTRACT,
            command_id=command_id,
            client_message_id=message_id,
        )
    )


async def run_direct_with_read(fx, revision):
    fx.bank.scripts.append(
        [
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="call_history",
                        name="read",
                        arguments='{"path": "history.py"}',
                    ),
                )
            ),
            ["historical answer"],
        ]
    )
    run = await fx.runtime.scheduler.run(start_direct(fx, revision).run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED
    execution = fx.journal.list_task_executions(WS, "task_root")[-1]
    fx.tasks.resume("task_root", command_id="cmd_history_resume")
    return execution


def publish_direct_output(fx, base, *, kind, slot):
    contracts = (OutputContract(kind=kind, slot=slot),)
    node = (
        workflow_source(base.nodes[0].agent_definition_ref)
        .nodes[0]
        .model_copy(
            update={
                "conversation_scope": "invoking_session",
                "output_contracts": contracts,
            }
        )
    )
    source = workflow_source(
        node.agent_definition_ref,
        nodes=(node,),
        required_outputs=(NodeOutputRef(node_id="worker", output_slot=slot),),
    )
    return fx.compiler.publish(
        source,
        source_revision=2,
        expected_head_revision=2,
        command_id=f"cmd_{kind.lower()}_publish",
        active_model=base.nodes[0].resolved_model_ref,
    ).revision


def test_direct_start_requires_distinct_client_message_binding(fx):
    _, revision = publish_direct(fx)
    with pytest.raises(ApplicationError, match="client-message ID"):
        fx.runtime.start.start(
            StartWorkflowCommand(
                workflow_definition_id="pipeline",
                workflow_revision_id=revision.workflow_revision_id,
                session_id="ses_root",
                root_task_run_id="task_root",
                expected_root_row_version=root(fx).row_version,
                contract=CONTRACT,
                command_id="cmd_missing_message",
            )
        )


def test_builtin_direct_fixture_is_explicit_one_node_source(fx):
    revision_model = publish_direct(fx)[1].nodes[0].resolved_model_ref
    direct = builtin_definitions(revision_model)[0]
    version = fx.agents.publish(
        direct,
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_builtin_direct",
        origin="builtin",
    )
    ref = AgentDefinitionRef(
        definition_id=version.source.definition_id,
        version_id=version.version_id,
        content_hash=version.content_hash,
    )
    source = builtin_direct_workflow(ref)
    assert version.version_id == source.nodes[0].agent_definition_ref.version_id
    assert source.origin == "builtin"
    assert source.nodes[0].conversation_scope == "invoking_session"
    assert fx.compiler.validate(source, active_model=revision_model).candidate


@pytest.mark.asyncio
async def test_direct_adapter_reuses_root_and_finalizes_after_turn(fx):
    fx.bank.scripts.append([["direct answer"]])
    _, revision = publish_direct(fx)
    started = start_direct(fx, revision)
    replayed = start_direct(fx, revision)
    assert replayed.replayed
    assert replayed.run.workflow_run_id == started.run.workflow_run_id

    run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED
    assert run.result_status == "succeeded"
    node = only_node(fx, run.workflow_run_id)
    assert node.status is WorkflowStatus.COMPLETED
    assert node.conversation_session_id == "ses_root"
    assert node.leaf_task_run_id == "task_root"
    assert root(fx).status is TaskRunStatus.READY_FOR_ACCEPTANCE
    assert sum(len(provider.stream_calls) for provider in fx.bank.providers) == 1
    user_inputs = [
        message.content
        for message in fx.bank.providers[-1].stream_calls[0]
        if message.role == "user"
    ]
    assert user_inputs == [CONTRACT.objective]
    receipt = fx.journal.get_receipt(WS, "ses_root", "msg_direct")
    assert receipt is not None and receipt.disposition.value == "accepted_closed"

    artifact_id = node_output_artifact_id(node.node_run_id, "result")
    assert fx.artifacts.get(artifact_id) is not None
    snapshots = [item for item in outcomes(fx) if item.trigger is TaskOutcomeTrigger.SNAPSHOT]
    assert len(snapshots) == 1
    assert snapshots[0].text_safety_profile is TextSafetyProfile.WORKFLOW_VALUE_SENSITIVE

    again = await fx.runtime.scheduler.run(run.workflow_run_id)
    assert again == run
    assert sum(len(provider.stream_calls) for provider in fx.bank.providers) == 1

    accepted = fx.tasks.accept("task_root", command_id="cmd_direct_accept")
    assert accepted.outcome.text_safety_profile is TextSafetyProfile.WORKFLOW_VALUE_SENSITIVE
    assert accepted.outcome.goal_reference.kind is TaskOutcomeEvidenceKind.TURN
    assert artifact_id in {ref.artifact_id for ref in accepted.outcome.artifact_refs}


def test_direct_turn_repository_rechecks_bound_client_message(fx):
    _, revision = publish_direct(fx)
    started = start_direct(fx, revision)
    node = only_node(fx, started.run.workflow_run_id)
    with pytest.raises(ValueError, match="frozen root binding"):
        fx.journal.create_workflow_turn(
            WS,
            node.node_run_id,
            DurableTurn(
                turn_id="turn_wrong",
                session_id="ses_root",
                task_run_id="task_root",
                client_message_id="msg_wrong",
            ),
        )


@pytest.mark.asyncio
async def test_direct_turn_admission_never_falls_back_to_replaced_current_task(fx):
    fx.journal.transact(
        lambda txn: txn._task_journal._create(
            WS,
            DurableTaskRun(task_run_id="task_other", session_id="ses_root", workspace_id=WS),
            make_current=False,
        )
    )
    _, revision = publish_direct(fx)
    started = start_direct(fx, revision)
    original_drive = fx.runtime.scheduler._drive

    async def replace_then_drive(*args, **kwargs):
        session = fx.journal.get_session(WS, "ses_root")
        fx.journal.save_session(
            WS, session.model_copy(update={"current_task_run_id": "task_other"})
        )
        return await original_drive(*args, **kwargs)

    fx.runtime.scheduler._drive = replace_then_drive
    try:
        run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    finally:
        fx.runtime.scheduler._drive = original_drive

    assert run.status is WorkflowStatus.FAILED
    assert root(fx).status is TaskRunStatus.OPEN
    assert not fx.bank.providers[-1].stream_calls


@pytest.mark.asyncio
async def test_direct_turn_neither_consumes_nor_creates_ordinary_runtime_control(fx):
    fx.bank.scripts.append([["done"]])
    _, revision = publish_direct(fx)
    fx.journal.enqueue_runtime_control(
        WS,
        session_id="ses_root",
        kind=RuntimeControlKind.STEER,
        client_message_id="msg_direct",
        text=CONTRACT.objective,
        created_at=fx.clock.now(),
    )
    started = start_direct(fx, revision)
    run = await fx.runtime.scheduler.run(started.run.workflow_run_id)

    assert run.status is WorkflowStatus.COMPLETED
    controls = fx.journal.list_runtime_controls(WS, "ses_root")
    assert len(controls) == 1
    assert controls[0].status is RuntimeControlStatus.PENDING


@pytest.mark.asyncio
async def test_direct_model_failure_preserves_turn_outcome_without_snapshot(fx):
    fx.bank.scripts.append([RuntimeError("boom")])
    _, revision = publish_direct(fx)
    run = await fx.runtime.scheduler.run(start_direct(fx, revision).run.workflow_run_id)

    assert run.status is WorkflowStatus.FAILED
    assert only_node(fx, run.workflow_run_id).status is WorkflowStatus.FAILED
    assert root(fx).status is TaskRunStatus.FAILED
    terminal = outcomes(fx)
    assert len(terminal) == 1
    assert terminal[0].trigger is TaskOutcomeTrigger.TERMINAL_CLOSE
    assert terminal[0].text_safety_profile is TextSafetyProfile.WORKFLOW_VALUE_SENSITIVE
    assert "workflow_result_snapshot" not in {ref.role for ref in terminal[0].evidence_refs}


@pytest.mark.asyncio
async def test_direct_cancel_uses_existing_turn_terminal(fx):
    fx.bank.scripts.append(["cancel"])
    _, revision = publish_direct(fx)
    started = start_direct(fx, revision)
    task = asyncio.create_task(fx.runtime.scheduler.run(started.run.workflow_run_id))
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()
    run = await task

    assert run.status is WorkflowStatus.CANCELLED
    assert root(fx).status is TaskRunStatus.CANCELLED
    assert outcomes(fx)[-1].text_safety_profile is TextSafetyProfile.WORKFLOW_VALUE_SENSITIVE


@pytest.mark.asyncio
async def test_direct_admitted_run_ignores_ordinary_head_disable(fx):
    fx.bank.scripts.append([["still runs"]])
    _, revision = publish_direct(fx)
    started = start_direct(fx, revision)
    fx.compiler.set_enabled("pipeline", enabled=False, expected_head_revision=2)
    fx.agents.set_enabled("helper", enabled=False, expected_head_revision=1)

    run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED


@pytest.mark.asyncio
async def test_direct_pre_turn_preparation_failure_leaves_root_open(fx):
    _, revision = publish_direct(fx)
    started = start_direct(fx, revision)
    original = fx.runtime.scheduler._compose_leaf_runtime

    def fail_before_turn(*_args):
        raise ApplicationError(ApplicationErrorCode.INVALID, "prepared runtime unavailable")

    fx.runtime.scheduler._compose_leaf_runtime = fail_before_turn
    try:
        run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    finally:
        fx.runtime.scheduler._compose_leaf_runtime = original

    assert run.status is WorkflowStatus.FAILED
    assert only_node(fx, run.workflow_run_id).status is WorkflowStatus.CANCELLED
    assert root(fx).status is TaskRunStatus.OPEN
    assert outcomes(fx) == ()


@pytest.mark.asyncio
async def test_direct_artifact_commit_crash_recovers_without_second_request(fx):
    fx.bank.scripts.append([["recover me"]])
    _, revision = publish_direct(fx)
    started = start_direct(fx, revision)
    fx.artifacts.faults = OnceFaultInjector(FaultPoint.ARTIFACT_AFTER_RESERVE)
    with pytest.raises(InjectedFault):
        await fx.runtime.scheduler.run(started.run.workflow_run_id)
    fx.artifacts.faults = None

    run = await fx.runtime.scheduler.recover(started.run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED
    assert root(fx).status is TaskRunStatus.READY_FOR_ACCEPTANCE
    assert sum(len(provider.stream_calls) for provider in fx.bank.providers) == 1


@pytest.mark.asyncio
async def test_direct_finalizer_crash_recovers_from_durable_turn_without_rerun(fx):
    fx.bank.scripts.append([["finalized later"]])
    _, revision = publish_direct(fx)
    started = start_direct(fx, revision)
    original = fx.runtime.finalizer.finalize_success
    fired = False

    def crash_once(workflow_run_id):
        nonlocal fired
        if not fired:
            fired = True
            raise InjectedFault(FaultPoint.ARTIFACT_AFTER_RESERVE)
        return original(workflow_run_id)

    fx.runtime.finalizer.finalize_success = crash_once
    try:
        with pytest.raises(InjectedFault):
            await fx.runtime.scheduler.run(started.run.workflow_run_id)
        assert root(fx).status is TaskRunStatus.READY_FOR_ACCEPTANCE
        run = await fx.runtime.scheduler.recover(started.run.workflow_run_id)
    finally:
        fx.runtime.finalizer.finalize_success = original

    assert run.status is WorkflowStatus.COMPLETED
    assert sum(len(provider.stream_calls) for provider in fx.bank.providers) == 1


@pytest.mark.asyncio
async def test_direct_blocking_review_is_completed_needs_revision(fx):
    fx.bank.scripts.append(
        _script_submit_then_stop(
            "call_review",
            "review",
            {"verdict": "request_changes", "findings": ["add coverage"]},
            "reviewed",
        )
    )
    _version, base = publish_direct(fx)
    node = base.nodes[0].model_copy(
        update={"output_contracts": (OutputContract(kind="ReviewReport", slot="review"),)}
    )
    source = workflow_source(
        node.agent_definition_ref,
        nodes=(
            workflow_source(node.agent_definition_ref)
            .nodes[0]
            .model_copy(
                update={
                    "conversation_scope": "invoking_session",
                    "output_contracts": node.output_contracts,
                }
            ),
        ),
        required_outputs=(NodeOutputRef(node_id="worker", output_slot="review"),),
    )
    revision = fx.compiler.publish(
        source,
        source_revision=2,
        expected_head_revision=2,
        command_id="cmd_review_publish",
        active_model=base.nodes[0].resolved_model_ref,
    ).revision

    run = await fx.runtime.scheduler.run(start_direct(fx, revision).run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED
    assert run.result_status == "needs_revision"
    assert root(fx).status is TaskRunStatus.READY_FOR_ACCEPTANCE
    assert "workflow_result=needs_revision" in outcomes(fx)[-1].completion_basis


@pytest.mark.asyncio
async def test_direct_structured_submission_rejects_prior_root_execution(fx):
    _version, base = publish_direct(fx)
    historical = await run_direct_with_read(fx, base)
    revision = publish_direct_output(fx, base, kind="ReviewReport", slot="review")
    fx.bank.scripts.append(
        [
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="call_review_history",
                        name=SUBMIT_NODE_RESULT_NAME,
                        arguments=json.dumps(
                            {
                                "schema_version": 1,
                                "outputs": {
                                    "review": {
                                        "verdict": "approve",
                                        "findings": [],
                                    }
                                },
                                "summary": "reused old evidence",
                                "evidence_refs": [historical.tool_execution_id],
                            }
                        ),
                    ),
                )
            ),
            ["done"],
        ]
    )

    run = await fx.runtime.scheduler.run(
        start_direct(
            fx,
            revision,
            command_id="cmd_review_history_start",
            message_id="msg_review_history",
        ).run.workflow_run_id
    )

    assert run.status is WorkflowStatus.FAILED
    node = only_node(fx, run.workflow_run_id)
    assert fx.artifacts.get(node_output_artifact_id(node.node_run_id, "review")) is None


@pytest.mark.asyncio
async def test_direct_capture_ignores_prior_root_validation_artifact(fx):
    _version, base = publish_direct(fx)
    historical = await run_direct_with_read(fx, base)
    prior_report = WorkflowTestReport(
        items=(
            WorkflowTestReportItem(
                validator_kind="pytest",
                scope="historical",
                status="passed",
                exit_code=0,
                evidence_summary="old validation",
                content_complete=True,
                tool_execution_id=historical.tool_execution_id,
            ),
        ),
        content_complete=True,
    )
    artifact = fx.artifacts.publish_bytes(
        prior_report.model_dump_json().encode(),
        kind=ArtifactKind.TEST_REPORT,
        session_id="ses_root",
        task_run_id="task_root",
    )
    updated = historical.model_copy(
        update={
            "artifact_refs": (
                *historical.artifact_refs,
                ArtifactReference(
                    artifact_id=artifact.artifact_id,
                    role=VALIDATION_REPORT_ROLE,
                ),
            ),
            "row_version": historical.row_version + 1,
        }
    )
    fx.journal.save_execution(
        WS,
        updated,
        expected_row_version=historical.row_version,
    )
    revision = publish_direct_output(fx, base, kind="TestReport", slot="tests")
    fx.bank.scripts.append([["current result"]])

    run = await fx.runtime.scheduler.run(
        start_direct(
            fx,
            revision,
            command_id="cmd_test_report_start",
            message_id="msg_test_report",
        ).run.workflow_run_id
    )

    assert run.status is WorkflowStatus.COMPLETED
    node = only_node(fx, run.workflow_run_id)
    stored = fx.artifacts.get(node_output_artifact_id(node.node_run_id, "tests"))
    report = parse_workflow_payload(
        "TestReport",
        fx.artifacts.read(stored.artifact_id, max_bytes=stored.byte_size).content,
    )
    assert report.items == ()


@pytest.mark.asyncio
async def test_direct_snapshot_excludes_prior_root_turn_and_execution(fx):
    _version, revision = publish_direct(fx)
    historical = await run_direct_with_read(fx, revision)
    fx.bank.scripts.append([["current answer"]])

    run = await fx.runtime.scheduler.run(
        start_direct(
            fx,
            revision,
            command_id="cmd_current_start",
            message_id="msg_current",
        ).run.workflow_run_id
    )

    assert run.status is WorkflowStatus.COMPLETED
    snapshot = outcomes(fx)[-1]
    evidence_ids = {item.reference_id for item in snapshot.evidence_refs}
    assert historical.tool_execution_id not in evidence_ids
    assert historical.turn_id not in evidence_ids
    assert len([item for item in snapshot.evidence_refs if item.role == "workflow_leaf_turn"]) == 1
