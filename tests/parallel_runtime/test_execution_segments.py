"""Lane A / P02: append-only node execution segments and durable pause facts."""

from __future__ import annotations

from datetime import timedelta

import pytest

from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.execution_pause_journal import SqliteExecutionPauseJournal
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.artifacts import ArtifactService
from morrow.application.execution_pause import ExecutionPauseService
from morrow.core.agent_definitions import AgentDefinitionSource, AgentDefinitionVersion
from morrow.core.agent_runs import AgentDefinitionRef
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.contracts import ExecutionSegmentIdentity, SegmentDirectoryPort
from morrow.core.domain import (
    AgentRunSnapshot,
    DurableAgentRun,
    DurableSession,
    DurableTaskRun,
    DurableTurn,
    TaskRunPurpose,
    sha256_digest,
)
from morrow.core.execution_pause import PauseIntentRequest, PauseSafetyPoint
from morrow.core.workflows.contracts import ArtifactBinding, TaskContract
from morrow.core.workflows.definitions import WorkflowDefinitionHead
from morrow.core.workflows.runs import NodeRun, WorkflowRun, WorkflowStatus
from morrow.testing import FixedClock, FixedIdSource
from test_stage4_journal import _snapshot
from test_stage7_workflow_domain import BUDGET, NOW, node, revision, source

WS = "ws_one"


@pytest.fixture
def state(tmp_path):
    """Isolated current store seeded like tests/test_stage7_workflow_store.py."""
    store = OperationalStore(tmp_path / "state", clock=FixedClock())
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle, clock=FixedClock().now)
    journal.create_session(
        DurableSession(session_id="ses_root", workspace_id=WS),
        task=DurableTaskRun(task_run_id="task_root", session_id="ses_root", workspace_id=WS),
    )
    src = AgentDefinitionSource(definition_id="helper", name="Helper", role_prompt="Inspect tests")
    version = AgentDefinitionVersion(
        version_id="adev_one",
        workspace_id=WS,
        version=1,
        source=src,
        content_hash=src.content_hash,
        source_revision=0,
        created_at=NOW,
    )
    journal.transact(lambda _: journal.agent_definitions.put_version(version))
    rev = revision(
        nodes=(
            node(
                agent_definition_ref=AgentDefinitionRef(
                    definition_id="helper",
                    version_id="adev_one",
                    content_hash=src.content_hash,
                )
            ),
        )
    )
    head = WorkflowDefinitionHead(
        workspace_id=WS,
        workflow_definition_id="pipeline",
        workflow_revision_id=rev.workflow_revision_id,
        source_revision=0,
        source_hash=source().content_hash,
        row_version=1,
    )
    journal.workflows.store_compiled_revision(rev, head, expected_row_version=0)
    artifacts = ArtifactService(
        journal=journal,
        filesystem=FilesystemArtifactStore(store.layout),
        workspace_id=WS,
        id_source=FixedIdSource(),
        clock=FixedClock().now,
    )
    artifact = artifacts.publish_workflow_payload(
        TaskContract(objective="Inspect password_validation.py"),
        session_id="ses_root",
        task_run_id="task_root",
    )
    run = WorkflowRun(
        workflow_run_id="wrun_one",
        workspace_id=WS,
        workflow_revision_id=rev.workflow_revision_id,
        root_task_run_id="task_root",
        budget_snapshot=BUDGET,
        started_at=NOW,
        admission_deadline_at=NOW + timedelta(seconds=BUDGET.admission_timeout_seconds),
        input_artifacts=(
            ArtifactBinding(
                name="task", artifact_id=artifact.artifact_id, contract={"kind": "TaskContract"}
            ),
        ),
    )
    leaf = NodeRun(
        node_run_id="nrun_one", workspace_id=WS, workflow_run_id="wrun_one", node_id="worker"
    )
    journal.workflows.create_run(run, (leaf,))
    yield journal, rev, run, leaf, handle
    handle.close()


def _assert_current_schema(handle) -> None:
    """Assert that the current store includes the segment tables."""

    rows = handle.run_read(
        lambda executor: executor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_node_segments'"
        )
    )
    assert rows


def _admit_leaf(journal, leaf, rev, run) -> NodeRun:
    journal.create_workflow_leaf(
        WS,
        leaf.node_run_id,
        DurableSession(session_id="ses_leaf", workspace_id=WS),
        DurableTaskRun(
            task_run_id="task_leaf",
            session_id="ses_leaf",
            workspace_id=WS,
            purpose=TaskRunPurpose.WORKFLOW_NODE,
            created_at=NOW,
            updated_at=NOW,
        ),
    )
    journal.create_workflow_turn(
        WS,
        leaf.node_run_id,
        DurableTurn(
            turn_id="turn_leaf",
            session_id="ses_leaf",
            task_run_id="task_leaf",
            client_message_id="input",
            created_at=NOW,
        ),
    )
    snapshot = AgentRunSnapshot.model_validate(
        {
            **_snapshot().model_dump(),
            "definition_ref": rev.nodes[0].agent_definition_ref,
            "conversation_session_id": "ses_leaf",
            "model": rev.nodes[0].resolved_model_ref,
            "prompt_profile_id": "default",
            "prompt_profile_version": "1",
            "prompt_profile_digest": "a" * 64,
            "role_prompt_digest": sha256_digest("Inspect tests"),
        }
    )
    journal.create_agent_run(
        WS,
        DurableAgentRun(
            agent_run_id="arun_leaf",
            session_id="ses_leaf",
            turn_id="turn_leaf",
            snapshot=snapshot,
            created_at=NOW,
        ),
    )
    running = leaf.model_copy(
        update={
            "status": WorkflowStatus.RUNNING,
            "started_at": NOW,
            "row_version": 2,
            "conversation_session_id": "ses_leaf",
            "leaf_task_run_id": "task_leaf",
            "agent_run_id": "arun_leaf",
            "effective_node_generation_request_cap": 2,
        }
    )
    journal.workflows.save_node(running, expected_row_version=1)
    journal.workflows.save_run(
        run.model_copy(update={"status": WorkflowStatus.RUNNING, "row_version": 2}),
        expected_row_version=1,
    )
    return running


def _pause_service(journal) -> ExecutionPauseService:
    return ExecutionPauseService(
        journal, workspace_id=WS, id_source=FixedIdSource(), clock=FixedClock().now
    )


def _record_first_segment(service, run, running, agent_run_id="arun_leaf"):
    """Admission-time first segment for rows created in the current schema."""
    return service.record_initial_segment(
        workflow_run_id=run.workflow_run_id,
        node_run_id=running.node_run_id,
        leaf_session_id="ses_leaf",
        leaf_task_run_id="task_leaf",
        agent_run_id=agent_run_id,
    )


# Segments ---------------------------------------------------------------------


def test_initial_segment_keeps_node_binding(state):
    journal, rev, run, leaf, handle = state
    running = _admit_leaf(journal, leaf, rev, run)
    _record_first_segment(_pause_service(journal), run, running)
    segments = journal.workflows.segments_for_node(WS, running.node_run_id)
    assert len(segments) == 1
    first = segments[0]
    assert first.ordinal == 1 and first.status == "active"
    assert first.agent_run_id == "arun_leaf"
    assert first.leaf_session_id == "ses_leaf" and first.leaf_task_run_id == "task_leaf"
    assert first.previous_segment_id is None
    # The historical NodeRun binding stays readable and unchanged.
    assert journal.workflows.get_node(WS, running.node_run_id).agent_run_id == "arun_leaf"
    # Re-recording the binding is idempotent: it adds no rows.
    _record_first_segment(_pause_service(journal), run, running)
    assert len(journal.workflows.segments_for_node(WS, running.node_run_id)) == 1
    # The frozen port view resolves the current executor.
    directory = journal.workflows.segment_directory()
    assert isinstance(directory, SegmentDirectoryPort)
    current = directory.current_segment(running.node_run_id)
    assert current is not None and current.segment_id == first.segment_id
    assert isinstance(current, ExecutionSegmentIdentity)
    assert directory.segments(run.workflow_run_id, running.node_run_id) == segments
    assert directory.current_segment("nrun_missing") is None


def test_segment_append_close_and_continuation_roundtrip(state):
    journal, rev, run, leaf, handle = state
    _assert_current_schema(handle)
    running = _admit_leaf(journal, leaf, rev, run)
    service = _pause_service(journal)
    first = _record_first_segment(service, run, running)
    closed = journal.workflows.close_segment(
        WS,
        first.segment_id,
        status="interrupted",
        pause_reason="user_interrupt",
        expected_row_version=first.row_version,
    )
    assert closed.status == "interrupted" and closed.pause_reason == "user_interrupt"
    assert journal.workflows.current_segment(running.node_run_id) is None
    segment, point, receipt, replayed = service.accept_continuation(
        command_id="cmd_cont_1",
        workflow_run_id=run.workflow_run_id,
        node_run_id=running.node_run_id,
        leaf_session_id="ses_leaf",
        leaf_task_run_id="task_leaf",
        agent_run_id="arun_two",
        turn_id="turn_two",
    )
    assert not replayed
    assert segment.ordinal == 2
    assert segment.previous_segment_id == first.segment_id
    assert segment.accepted_command_id == "cmd_cont_1"
    assert segment.status == "active"
    assert receipt.result_id == segment.segment_id
    current = journal.workflows.current_segment(running.node_run_id)
    assert current is not None and current.segment_id == segment.segment_id
    ordered = journal.workflows.segments_for_node(WS, running.node_run_id)
    assert [s.ordinal for s in ordered] == [1, 2]


def test_segment_rejects_second_active_bad_ownership_and_stale_close(state):
    journal, rev, run, leaf, handle = state
    _assert_current_schema(handle)
    running = _admit_leaf(journal, leaf, rev, run)
    service = _pause_service(journal)
    first = _record_first_segment(service, run, running)
    duplicate = first.model_copy(update={"segment_id": "seg_dup", "ordinal": 2, "row_version": 1})
    with pytest.raises(ValueError, match="predecessor"):
        journal.workflows.append_segment(duplicate)
    drifted = first.model_copy(
        update={"segment_id": "seg_drift", "leaf_session_id": "ses_root", "ordinal": 2}
    )
    with pytest.raises(ValueError, match="leaf ownership"):
        journal.workflows.append_segment(drifted)
    orphan = first.model_copy(
        update={"segment_id": "seg_orphan", "previous_segment_id": None, "ordinal": 1}
    )
    with pytest.raises(ValueError, match="first segment"):
        journal.workflows.append_segment(orphan)
    with pytest.raises(ValueError, match="revision conflict"):
        journal.workflows.close_segment(
            WS,
            first.segment_id,
            status="completed",
            expected_row_version=first.row_version + 5,
        )
    journal.workflows.close_segment(
        WS,
        first.segment_id,
        status="interrupted",
        pause_reason="user_interrupt",
        expected_row_version=first.row_version,
    )
    successor = first.model_copy(
        update={
            "segment_id": "seg_next",
            "ordinal": 2,
            "row_version": 1,
            "previous_segment_id": first.segment_id,
            "agent_run_id": "arun_two",
        }
    )
    appended = journal.workflows.append_segment(successor)
    assert appended.status == "active"
    # The node now has one closed and one active segment; a third active one is rejected.
    third = appended.model_copy(update={"segment_id": "seg_third", "ordinal": 3, "row_version": 1})
    with pytest.raises(ValueError, match="predecessor|second active segment"):
        journal.workflows.append_segment(third)


def test_active_segment_can_be_continued_after_pause(state):
    journal, rev, run, leaf, handle = state
    _assert_current_schema(handle)
    running = _admit_leaf(journal, leaf, rev, run)
    service = _pause_service(journal)
    _record_first_segment(service, run, running)
    service.accept_pause(
        PauseIntentRequest(
            command_id="cmd_pause_1",
            owner="workflow_run",
            owner_id=run.workflow_run_id,
            reason="user_interrupt",
        ),
        node_run_id=running.node_run_id,
    )
    segment, point, _, replayed = service.accept_continuation(
        command_id="cmd_cont_9",
        workflow_run_id=run.workflow_run_id,
        node_run_id=running.node_run_id,
        leaf_session_id="ses_leaf",
        leaf_task_run_id="task_leaf",
        agent_run_id="arun_next",
    )
    assert not replayed
    assert segment.ordinal == 2
    segments = journal.workflows.segments_for_node(WS, running.node_run_id)
    assert [s.status for s in segments] == ["interrupted", "active"]
    assert segments[0].pause_reason == "user_interrupt"
    assert point is not None and point.fact.lifecycle == "resumed"


# Pause facts ------------------------------------------------------------------


def test_accept_pause_replay_conflict_and_generation(state):
    journal, rev, run, leaf, handle = state
    _assert_current_schema(handle)
    service = _pause_service(journal)
    request = PauseIntentRequest(
        command_id="cmd_pause_a",
        owner="workflow_run",
        owner_id=run.workflow_run_id,
        reason="user_interrupt",
    )
    point = service.accept_pause(request, expected_run_row_version=1)
    assert point.fact.control_generation == 1
    assert point.fact.lifecycle == "requested"
    # A legal replay wins over a stale version expectation.
    replayed = service.accept_pause(request, expected_run_row_version=999)
    assert replayed.pause_point_id == point.pause_point_id
    assert replayed.row_version == point.row_version
    with pytest.raises(ApplicationError) as err:
        service.accept_pause(request.model_copy(update={"reason": "node_boundary"}))
    assert err.value.code is ApplicationErrorCode.CONFLICT
    second = service.accept_pause(request.model_copy(update={"command_id": "cmd_pause_b"}))
    assert second.fact.control_generation == 2
    with pytest.raises(ApplicationError) as err:
        service.accept_pause(
            request.model_copy(update={"command_id": "cmd_pause_c"}),
            expected_run_row_version=42,
        )
    assert err.value.code is ApplicationErrorCode.STALE


def test_pause_lifecycle_occ_and_cancel_fact_separate(state):
    journal, rev, run, leaf, handle = state
    _assert_current_schema(handle)
    _admit_leaf(journal, leaf, rev, run)
    service = _pause_service(journal)
    point = service.accept_pause(
        PauseIntentRequest(
            command_id="cmd_pause_l",
            owner="workflow_run",
            owner_id=run.workflow_run_id,
            reason="user_interrupt",
        )
    )
    generation = point.fact.control_generation
    with pytest.raises(ApplicationError) as err:
        service.advance_lifecycle(
            workflow_run_id=run.workflow_run_id,
            control_generation=generation,
            target="suspended",
        )
    assert err.value.code is ApplicationErrorCode.CONFLICT
    quiescing = service.advance_lifecycle(
        workflow_run_id=run.workflow_run_id,
        control_generation=generation,
        target="quiescing",
    )
    assert quiescing.fact.lifecycle == "quiescing"
    replayed = service.advance_lifecycle(
        workflow_run_id=run.workflow_run_id,
        control_generation=generation,
        target="quiescing",
    )
    assert replayed.row_version == quiescing.row_version
    with pytest.raises(ApplicationError) as err:
        service.advance_lifecycle(
            workflow_run_id=run.workflow_run_id,
            control_generation=generation,
            target="suspended",
            expected_row_version=1,
        )
    assert err.value.code is ApplicationErrorCode.STALE
    suspended = service.advance_lifecycle(
        workflow_run_id=run.workflow_run_id,
        control_generation=generation,
        target="suspended",
        safety=PauseSafetyPoint(
            committed_position=7,
            interrupted_turn_id="turn_leaf",
            interrupted_agent_run_id="arun_leaf",
        ),
        expected_row_version=quiescing.row_version,
    )
    assert suspended.fact.lifecycle == "suspended"
    assert suspended.fact.suspended_at is not None
    assert suspended.safety is not None and suspended.safety.committed_position == 7
    cancelled = service.record_cancel(
        workflow_run_id=run.workflow_run_id,
        control_generation=generation,
        cancel_command_id="cmd_cancel_1",
        reason="user_cancel",
    )
    # Cancel is its own fact; the lifecycle is never rewritten to resumed.
    assert cancelled.fact.lifecycle == "suspended"
    assert cancelled.cancel_command_id == "cmd_cancel_1"
    again = service.record_cancel(
        workflow_run_id=run.workflow_run_id,
        control_generation=generation,
        cancel_command_id="cmd_cancel_1",
    )
    assert again.pause_point_id == cancelled.pause_point_id
    with pytest.raises(ApplicationError) as err:
        service.record_cancel(
            workflow_run_id=run.workflow_run_id,
            control_generation=generation,
            cancel_command_id="cmd_cancel_2",
        )
    assert err.value.code is ApplicationErrorCode.CONFLICT


def test_cancelled_pause_cycle_never_accepts_a_continuation(state):
    journal, rev, run, leaf, handle = state
    _assert_current_schema(handle)
    running = _admit_leaf(journal, leaf, rev, run)
    service = _pause_service(journal)
    _record_first_segment(service, run, running)
    point = service.accept_pause(
        PauseIntentRequest(
            command_id="cmd_pause_c2",
            owner="workflow_run",
            owner_id=run.workflow_run_id,
            reason="user_interrupt",
        )
    )
    generation = point.fact.control_generation
    service.advance_lifecycle(
        workflow_run_id=run.workflow_run_id,
        control_generation=generation,
        target="quiescing",
    )
    service.advance_lifecycle(
        workflow_run_id=run.workflow_run_id,
        control_generation=generation,
        target="suspended",
        safety=PauseSafetyPoint(committed_position=3),
    )
    service.record_cancel(
        workflow_run_id=run.workflow_run_id,
        control_generation=generation,
        cancel_command_id="cmd_cancel_c2",
    )
    with pytest.raises(ApplicationError) as err:
        service.accept_continuation(
            command_id="cmd_cont_cancelled",
            workflow_run_id=run.workflow_run_id,
            node_run_id=running.node_run_id,
            leaf_session_id="ses_leaf",
            leaf_task_run_id="task_leaf",
            agent_run_id="arun_x",
        )
    assert err.value.code is ApplicationErrorCode.INVALID
    # The refused continuation left no half-written segment behind.
    segments = journal.workflows.segments_for_node(WS, running.node_run_id)
    assert len(segments) == 1 and segments[0].status == "active"


def test_continuation_acceptance_is_atomic_replayable_and_validated(state):
    journal, rev, run, leaf, handle = state
    _assert_current_schema(handle)
    running = _admit_leaf(journal, leaf, rev, run)
    service = _pause_service(journal)
    _record_first_segment(service, run, running)
    service.accept_pause(
        PauseIntentRequest(
            command_id="cmd_pause_pre",
            owner="workflow_run",
            owner_id=run.workflow_run_id,
            reason="user_interrupt",
        ),
        node_run_id=running.node_run_id,
    )
    # A continuation against wrong leaf ownership is rejected before any write.
    with pytest.raises(ApplicationError) as err:
        service.accept_continuation(
            command_id="cmd_cont_bad",
            workflow_run_id=run.workflow_run_id,
            node_run_id=running.node_run_id,
            leaf_session_id="ses_root",
            leaf_task_run_id="task_root",
            agent_run_id="arun_bad",
        )
    assert err.value.code is ApplicationErrorCode.INVALID
    assert journal.workflows.segments_for_node(WS, running.node_run_id)[0].status == "active"
    segment, point, receipt, replayed = service.accept_continuation(
        command_id="cmd_cont_ok",
        workflow_run_id=run.workflow_run_id,
        node_run_id=running.node_run_id,
        leaf_session_id="ses_leaf",
        leaf_task_run_id="task_leaf",
        agent_run_id="arun_two",
        continuation_input="先不要改 UI，改为修复保存功能",
    )
    assert not replayed
    assert receipt.result_id == segment.segment_id
    assert point.continuation_segment_id == segment.segment_id
    assert point.continuation_input == "先不要改 UI，改为修复保存功能"
    assert point.fact.lifecycle == "resumed" and point.fact.resumed_at is not None
    same = service.accept_continuation(
        command_id="cmd_cont_ok",
        workflow_run_id=run.workflow_run_id,
        node_run_id=running.node_run_id,
        leaf_session_id="ses_leaf",
        leaf_task_run_id="task_leaf",
        agent_run_id="arun_two",
        continuation_input="先不要改 UI，改为修复保存功能",
    )
    assert same[0].segment_id == segment.segment_id
    assert same[3] is True
    assert len(journal.workflows.segments_for_node(WS, running.node_run_id)) == 2
    with pytest.raises(ApplicationError) as err:
        service.accept_continuation(
            command_id="cmd_cont_ok",
            workflow_run_id=run.workflow_run_id,
            node_run_id=running.node_run_id,
            leaf_session_id="ses_leaf",
            leaf_task_run_id="task_leaf",
            agent_run_id="arun_other",
        )
    assert err.value.code is ApplicationErrorCode.CONFLICT
    # A completed node never accepts continuations.
    journal.workflows.close_segment(
        WS,
        segment.segment_id,
        status="completed",
        expected_row_version=segment.row_version,
    )
    with pytest.raises(ApplicationError) as err:
        service.accept_continuation(
            command_id="cmd_cont_after_complete",
            workflow_run_id=run.workflow_run_id,
            node_run_id=running.node_run_id,
            leaf_session_id="ses_leaf",
            leaf_task_run_id="task_leaf",
            agent_run_id="arun_three",
        )
    assert err.value.code is ApplicationErrorCode.INVALID


def test_record_initial_segment_is_idempotent(state):
    journal, rev, run, leaf, handle = state
    _assert_current_schema(handle)
    running = _admit_leaf(journal, leaf, rev, run)
    service = _pause_service(journal)
    first = service.record_initial_segment(
        workflow_run_id=run.workflow_run_id,
        node_run_id=running.node_run_id,
        leaf_session_id="ses_leaf",
        leaf_task_run_id="task_leaf",
        agent_run_id="arun_leaf",
    )
    assert first.ordinal == 1 and first.status == "active"
    journal.workflows.close_segment(
        WS,
        first.segment_id,
        status="completed",
        expected_row_version=first.row_version,
    )
    # Re-recording the same binding replays instead of appending a segment.
    again = service.record_initial_segment(
        workflow_run_id=run.workflow_run_id,
        node_run_id=running.node_run_id,
        leaf_session_id="ses_leaf",
        leaf_task_run_id="task_leaf",
        agent_run_id="arun_leaf",
    )
    assert again.segment_id == first.segment_id
    assert len(journal.workflows.segments_for_node(WS, running.node_run_id)) == 1


def test_pause_point_journal_roundtrip_direct(state):
    journal, _, _, _, handle = state
    _assert_current_schema(handle)
    pause_journal = SqliteExecutionPauseJournal(journal._backend)
    assert pause_journal.next_control_generation(WS, owner="workflow_run", owner_id="wrun_x") == 1
    assert pause_journal.get_pause_point("missing") is None
