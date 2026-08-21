"""S51.4 Project Knowledge promotion and candidate-only acceptance coverage."""

from __future__ import annotations

import pytest

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import DurableConversationRecord, DurableTurn, TaskRunStatus
from morrow.core.learning import (
    CandidateDraftBatch,
    LearningCandidate,
    LearningCandidateStatus,
    LearningCandidateType,
    LearningConfidenceBand,
    LearningSensitivity,
)
from morrow.core.learning_commands import (
    AcceptLearningCandidateCommand,
    DeleteProjectKnowledgeCommand,
    DisableProjectKnowledgeCommand,
    EditAndAcceptLearningCandidateCommand,
    EnableProjectKnowledgeCommand,
    MarkProjectKnowledgeDisputedCommand,
)
from morrow.core.learning_memory import (
    LearningConflictResolution,
    ProjectKnowledgeCategory,
    ProjectKnowledgeStatus,
)
from morrow.core.learning_payloads import (
    LearningCandidateDraft,
    OrchestrationPolicyCandidatePayload,
    ProjectKnowledgeCandidatePayload,
    SkillCandidatePayload,
    WorkflowFeedbackCandidatePayload,
)
from morrow.core.models import ModelRef
from test_stage5_review_pipeline import NOW, _accepted, _api


class ProjectKnowledgeReviewer:
    def __init__(self, statements=()):
        self.statements = tuple(statements) or (
            "The application persists operational state in SQLite.",
        )
        self.index = 0

    async def review(self, context, *, model: ModelRef, timeout_seconds: float):
        del model, timeout_seconds
        evidence = next(
            item for item in context.evidence if item.authority.value == "deterministic_task_fact"
        )
        index = self.index
        statement = self.statements[min(index, len(self.statements) - 1)]
        self.index += 1
        return CandidateDraftBatch(
            drafts=(
                LearningCandidateDraft(
                    candidate_type=LearningCandidateType.PROJECT_KNOWLEDGE,
                    operation="set" if index == 0 else "replace",
                    semantic_key="architecture.persistence",
                    proposed_scope="workspace",
                    proposed_payload=ProjectKnowledgeCandidatePayload(
                        category="architecture",
                        semantic_key="architecture.persistence",
                        statement=statement,
                    ),
                    evidence_ids=(evidence.evidence_id,),
                    temporary_or_durable="durable",
                ),
            )
        )


async def _project_candidate(tmp_path, *, statements=()):
    reviewer = ProjectKnowledgeReviewer(statements)
    session, journal, api = _api(tmp_path, reviewer=reviewer)
    accepted = _accepted(api, journal, with_user_turn=True)
    outcome = api.list_outcomes(accepted.value.task_run_id)[0]
    review = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items[0]
    await api.run_learning_review(review.review_id)
    candidate = api.list_learning_candidate_views(status="proposed").items[0]
    return session, journal, api, candidate, reviewer


async def _run_project_review(api, journal, *, index=2):
    task = api.task_new("ses_1", command_id=f"cmd_new_{index}").value
    journal.create_turn(
        "ws_1",
        DurableTurn(
            turn_id=f"turn_{index}",
            session_id="ses_1",
            task_run_id=task.task_run_id,
            client_message_id=f"client_{index}",
            created_at=NOW,
        ),
    )
    journal.append_records(
        "ws_1",
        (
            DurableConversationRecord(
                record_id=f"rec_{index}",
                session_id="ses_1",
                conversation_position=index,
                kind="message",
                payload={"role": "user", "content": "请记录新的架构事实"},
            ),
        ),
    )
    ready = api.tasks._transition(
        task,
        TaskRunStatus.READY_FOR_ACCEPTANCE,
        reason="answer",
        turn_id=None,
        command_id=None,
    )
    accepted = api.task_accept(
        ready.task_run_id,
        command_id=f"cmd_accept_{index}",
        expected_row_version=ready.row_version,
    )
    outcome = api.list_outcomes(accepted.value.task_run_id)[0]
    review = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items[0]
    await api.run_learning_review(review.review_id)
    return api.list_learning_candidate_views(status="proposed").items[0]


def _accept_command(candidate, command_id: str, **kwargs):
    source = candidate.candidate if hasattr(candidate, "candidate") else candidate
    return AcceptLearningCandidateCommand(
        workspace_id="ws_1",
        candidate_id=source.candidate_id,
        expected_row_version=source.row_version,
        command_id=command_id,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_project_knowledge_accept_confirm_replace_and_replay_are_atomic(tmp_path):
    session, journal, api, candidate, _reviewer = await _project_candidate(
        tmp_path,
        statements=(
            "The application persists operational state in SQLite.",
            "The application persists operational state in SQLite.",
        ),
    )
    try:
        first = api.accept_learning_candidate(_accept_command(candidate, "cmd_knw_1"))
        assert first.value.outcome == "activated"
        assert first.value.revision == 1
        assert first.value.memory_revision == 1
        head = journal.get_project_knowledge_head("ws_1", first.value.knowledge_id)
        assert head is not None and head.status is ProjectKnowledgeStatus.ACTIVE
        revision = journal.get_project_knowledge_revision("ws_1", first.value.knowledge_revision_id)
        assert revision is not None
        assert revision.source_candidate_id == candidate.candidate_id
        terms = journal.list_memory_search_terms(
            "ws_1", knowledge_revision_id=revision.knowledge_revision_id
        )
        assert {term.token for term in terms} >= {"operational", "sqlite"}
        assert (
            len(journal.list_project_knowledge_evidence("ws_1", revision.knowledge_revision_id))
            == 1
        )
        assert journal.get_memory_workspace_state("ws_1").memory_revision == 1
        assert [event.event_type for event in journal.list_application_events("ws_1")][-2:] == [
            "learning.candidate_accepted",
            "memory.record_activated",
        ]

        replay = api.accept_learning_candidate(_accept_command(candidate, "cmd_knw_1"))
        assert replay.receipt is not None
        assert replay.receipt.disposition.value == "replay"
        assert replay.value.decision.decision_id == first.value.decision.decision_id
        assert len(journal.list_project_knowledge_revisions("ws_1", head.knowledge_id)) == 1

        same_candidate = await _run_project_review(api, journal)
        confirmed = api.accept_learning_candidate(
            _accept_command(same_candidate, "cmd_knw_confirm")
        )
        assert confirmed.value.outcome == "confirmed"
        assert confirmed.value.revision == 1
        assert confirmed.value.memory_revision == 2
        assert len(journal.list_project_knowledge_revisions("ws_1", head.knowledge_id)) == 1

    finally:
        session.close()


@pytest.mark.asyncio
async def test_promotion_lazily_expires_due_candidates_before_mutation(tmp_path):
    session, journal, api, candidate, _reviewer = await _project_candidate(tmp_path)
    try:
        session.run_write(
            lambda executor: executor.execute(
                "UPDATE learning_candidates SET expires_at_unix = ? WHERE candidate_id = ?",
                (int(NOW.replace(year=2025).timestamp()), candidate.candidate_id),
            )
        )
        command = _accept_command(candidate, "cmd_knw_expired")
        with pytest.raises(ApplicationError) as expired:
            api.accept_learning_candidate(command)
        assert expired.value.code is ApplicationErrorCode.CONFLICT
        assert journal.get_learning_candidate("ws_1", candidate.candidate_id).status.value == (
            "expired"
        )
        decisions = journal.list_learning_candidate_decisions(
            "ws_1", candidate_id=candidate.candidate_id
        )
        assert len(decisions) == 1
        assert decisions[0].kind.value == "expire"
        with pytest.raises(ApplicationError) as replayed:
            api.accept_learning_candidate(command)
        assert replayed.value.code is ApplicationErrorCode.STALE
        assert (
            len(
                journal.list_learning_candidate_decisions(
                    "ws_1", candidate_id=candidate.candidate_id
                )
            )
            == 1
        )
    finally:
        session.close()


@pytest.mark.asyncio
async def test_project_knowledge_edit_accept_creates_superseding_revision(tmp_path):
    session, journal, api, candidate, _reviewer = await _project_candidate(
        tmp_path,
        statements=(
            "The application persists operational state in SQLite.",
            "The application stores durable state.",
        ),
    )
    try:
        first = api.accept_learning_candidate(_accept_command(candidate, "cmd_knw_first"))
        current = await _run_project_review(api, journal)
        assert current is not None
        edited = EditAndAcceptLearningCandidateCommand(
            workspace_id="ws_1",
            candidate_id=current.candidate_id,
            expected_row_version=current.row_version,
            command_id="cmd_knw_edit",
            conflict_resolution=LearningConflictResolution.REPLACE,
            final_payload=ProjectKnowledgeCandidatePayload(
                category="architecture",
                semantic_key="architecture.persistence",
                statement="Operational state is persisted in SQLite with immutable revisions.",
            ),
        )
        result = api.edit_and_accept_learning_candidate(edited)
        assert result.value.outcome == "superseded"
        assert result.value.revision == 2
        assert result.value.memory_revision == 2
        revisions = journal.list_project_knowledge_revisions("ws_1", first.value.knowledge_id)
        assert len(revisions) == 2
        assert revisions[1].supersedes_revision_id == revisions[0].knowledge_revision_id
        assert revisions[1].statement.endswith("immutable revisions.")
        assert (
            journal.list_memory_search_terms(
                "ws_1", knowledge_revision_id=revisions[0].knowledge_revision_id
            )
            == ()
        )
        assert journal.list_memory_search_terms(
            "ws_1", knowledge_revision_id=revisions[1].knowledge_revision_id
        )
    finally:
        session.close()


@pytest.mark.asyncio
async def test_project_knowledge_preview_rejects_category_conflicts(tmp_path):
    session, _journal, api, candidate, _reviewer = await _project_candidate(tmp_path)
    try:
        first = api.accept_learning_candidate(_accept_command(candidate, "cmd_category_first"))
        current = await _run_project_review(api, _journal)
        current_view = api.get_learning_candidate_view(current.candidate_id)
        assert current_view is not None
        edited_payload = current_view.candidate.proposed_payload.model_copy(
            update={
                "category": ProjectKnowledgeCategory.CONVENTION,
                "statement": "same statement",
            }
        )
        preview = api.preview_learning_candidate_decision(
            current.candidate_id,
            edit=edited_payload,
            conflict_resolution=LearningConflictResolution.REPLACE,
        )
        assert preview.available is False
        assert preview.reason == "knowledge_category_conflict"
        command = EditAndAcceptLearningCandidateCommand(
            workspace_id="ws_1",
            candidate_id=current.candidate_id,
            expected_row_version=current.row_version,
            command_id="cmd_category_conflict",
            conflict_resolution=LearningConflictResolution.REPLACE,
            final_payload=edited_payload,
        )
        with pytest.raises(ApplicationError) as error:
            api.edit_and_accept_learning_candidate(command)
        assert error.value.code is ApplicationErrorCode.CONFLICT
        assert api.get_project_knowledge(first.value.knowledge_id).revision.revision == 1
    finally:
        session.close()


@pytest.mark.asyncio
async def test_project_knowledge_lifecycle_is_replay_safe_and_hides_logical_deletes(tmp_path):
    session, journal, api, candidate, _reviewer = await _project_candidate(
        tmp_path,
        statements=(
            "The application persists operational state in SQLite.",
            "The application stores durable state.",
            "The application stores durable state with an auditable history.",
        ),
    )
    try:
        first = api.accept_learning_candidate(_accept_command(candidate, "cmd_lifecycle_accept"))
        knowledge_id = first.value.knowledge_id
        disabled = api.disable_project_knowledge(
            DisableProjectKnowledgeCommand(
                workspace_id="ws_1",
                knowledge_id=knowledge_id,
                expected_row_version=2,
                command_id="cmd_knw_disable",
            )
        )
        assert disabled.value.operation == "disabled"
        assert disabled.value.memory_revision == 2
        replay = api.disable_project_knowledge(
            DisableProjectKnowledgeCommand(
                workspace_id="ws_1",
                knowledge_id=knowledge_id,
                expected_row_version=2,
                command_id="cmd_knw_disable",
            )
        )
        assert replay.receipt is not None
        assert replay.receipt.disposition.value == "replay"

        second = await _run_project_review(api, journal, index=2)
        enabled = api.accept_learning_candidate(
            _accept_command(
                second,
                "cmd_knw_re_enable",
                conflict_resolution=LearningConflictResolution.RE_ENABLE,
            )
        )
        assert enabled.value.outcome == "enabled"
        assert enabled.value.revision == 2
        assert enabled.value.memory_revision == 3

        disputed = api.dispute_project_knowledge(
            MarkProjectKnowledgeDisputedCommand(
                workspace_id="ws_1",
                knowledge_id=knowledge_id,
                expected_row_version=4,
                command_id="cmd_knw_dispute",
            )
        )
        assert disputed.value.head.status is ProjectKnowledgeStatus.DISPUTED
        assert disputed.value.memory_revision == 4

        third = await _run_project_review(api, journal, index=3)
        resolved = api.accept_learning_candidate(
            _accept_command(
                third,
                "cmd_knw_resolve_dispute",
                conflict_resolution=LearningConflictResolution.RESOLVE_DISPUTE,
            )
        )
        assert resolved.value.outcome == "superseded"
        assert resolved.value.revision == 3
        assert resolved.value.memory_revision == 5

        deleted = api.delete_project_knowledge(
            DeleteProjectKnowledgeCommand(
                workspace_id="ws_1",
                knowledge_id=knowledge_id,
                expected_row_version=6,
                command_id="cmd_knw_delete",
            )
        )
        assert deleted.value.head.status is ProjectKnowledgeStatus.DELETED
        assert deleted.value.memory_revision == 6
        replay_after_lifecycle = api.disable_project_knowledge(
            DisableProjectKnowledgeCommand(
                workspace_id="ws_1",
                knowledge_id=knowledge_id,
                expected_row_version=2,
                command_id="cmd_knw_disable",
            )
        )
        assert replay_after_lifecycle.value.head.status is ProjectKnowledgeStatus.DISABLED
        assert api.list_project_knowledge().items == ()
        assert len(api.list_project_knowledge(status=ProjectKnowledgeStatus.DELETED).items) == 1
        history = api.get_project_knowledge(knowledge_id)
        assert history is not None
        assert len(history.timeline) == 3
        with pytest.raises(ApplicationError) as deleted_error:
            api.enable_project_knowledge(
                EnableProjectKnowledgeCommand(
                    workspace_id="ws_1",
                    knowledge_id=knowledge_id,
                    expected_row_version=7,
                    command_id="cmd_knw_enable_deleted",
                )
            )
        assert deleted_error.value.code is ApplicationErrorCode.CONFLICT
    finally:
        session.close()


@pytest.mark.asyncio
async def test_project_knowledge_candidate_only_acceptance_has_no_active_side_effect(tmp_path):
    session, journal, api = _api(tmp_path)
    try:
        accepted = _accepted(api, journal, with_user_turn=True)
        grants_before = journal.list_capability_grants("ws_1")
        snapshots_before = journal.list_permission_snapshots("ws_1")
        runs_before = journal.list_session_agent_runs("ws_1", "ses_1")
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        review = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items[0]
        await api.run_learning_review(review.review_id)
        evidence = journal.list_learning_review_evidence("ws_1", review.review_id)[0]
        candidate = journal.put_learning_candidate(
            "ws_1",
            LearningCandidate.from_draft(
                candidate_id="lcn_skill_manual",
                workspace_id="ws_1",
                origin_review_id=review.review_id,
                draft=LearningCandidateDraft(
                    candidate_type=LearningCandidateType.SKILL_CANDIDATE,
                    operation="set",
                    semantic_key="skill.testing.workflow",
                    proposed_scope="workspace",
                    proposed_payload=SkillCandidatePayload(
                        title="Run focused tests",
                        problem_pattern="A focused regression is needed.",
                        observed_steps=("Run the focused test file",),
                    ),
                    evidence_ids=(evidence.evidence_id,),
                    temporary_or_durable="durable",
                ),
                confidence_band=LearningConfidenceBand.MEDIUM,
                confidence_basis=("manual_test_fixture",),
                sensitivity=LearningSensitivity.NORMAL,
                expires_at=NOW.replace(day=31),
                now=NOW,
            ),
        )
        candidate = api.get_learning_candidate_view(candidate.candidate_id)
        assert candidate is not None
        result = api.accept_learning_candidate(_accept_command(candidate, "cmd_skill_accept"))
        assert result.value.outcome == "candidate_only"
        assert result.value.candidate.status.value == "accepted"
        assert journal.list_project_knowledge_heads("ws_1") == ()
        assert journal.get_memory_workspace_state("ws_1") is None
        assert journal.list_capability_grants("ws_1") == grants_before
        assert journal.list_permission_snapshots("ws_1") == snapshots_before
        assert journal.list_session_agent_runs("ws_1", "ses_1") == runs_before
        assert [event.event_type for event in journal.list_application_events("ws_1")][-1] == (
            "learning.candidate_accepted"
        )
    finally:
        session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("candidate_type", "semantic_key", "payload"),
    (
        (
            LearningCandidateType.WORKFLOW_FEEDBACK,
            "workflow.release_checks",
            WorkflowFeedbackCandidatePayload(
                workflow_name="release_checks",
                edit_summary="Reordered the verification step.",
                result_summary="The run completed with the expected checks.",
            ),
        ),
        (
            LearningCandidateType.ORCHESTRATION_POLICY_CANDIDATE,
            "orchestration.release_checks",
            OrchestrationPolicyCandidatePayload(
                trigger="task.accepted",
                workflow_name="release_checks",
                rule_summary="Use the verification workflow for release tasks.",
            ),
        ),
    ),
)
async def test_future_candidate_acceptance_remains_candidate_only(
    tmp_path, candidate_type, semantic_key, payload
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    marker = workspace / "keep.txt"
    marker.write_text("unchanged", encoding="utf-8")
    session, journal, api = _api(tmp_path)
    try:
        accepted = _accepted(api, journal, with_user_turn=True)
        grants_before = journal.list_capability_grants("ws_1")
        snapshots_before = journal.list_permission_snapshots("ws_1")
        runs_before = journal.list_session_agent_runs("ws_1", "ses_1")
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        review = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items[0]
        await api.run_learning_review(review.review_id)
        evidence = journal.list_learning_review_evidence("ws_1", review.review_id)[0]
        candidate = journal.put_learning_candidate(
            "ws_1",
            LearningCandidate.from_draft(
                candidate_id=f"lcn_{candidate_type.value}",
                workspace_id="ws_1",
                origin_review_id=review.review_id,
                draft=LearningCandidateDraft(
                    candidate_type=candidate_type,
                    operation="set",
                    semantic_key=semantic_key,
                    proposed_scope="workspace",
                    proposed_payload=payload,
                    evidence_ids=(evidence.evidence_id,),
                    temporary_or_durable="durable",
                ),
                confidence_band=LearningConfidenceBand.MEDIUM,
                confidence_basis=("manual_future_candidate_fixture",),
                sensitivity=LearningSensitivity.NORMAL,
                expires_at=NOW.replace(day=31),
                now=NOW,
            ),
        )
        view = api.get_learning_candidate_view(candidate.candidate_id)
        assert view is not None

        result = api.accept_learning_candidate(_accept_command(view, f"cmd_{candidate_type.value}"))

        assert result.value.outcome == "candidate_only"
        assert result.value.candidate.status is LearningCandidateStatus.ACCEPTED
        assert journal.list_project_knowledge_heads("ws_1") == ()
        assert journal.get_memory_workspace_state("ws_1") is None
        assert marker.read_text(encoding="utf-8") == "unchanged"
        assert not any(
            event.event_type == "memory.record_activated"
            for event in journal.list_application_events("ws_1")
        )
        assert journal.list_capability_grants("ws_1") == grants_before
        assert journal.list_permission_snapshots("ws_1") == snapshots_before
        assert journal.list_session_agent_runs("ws_1", "ses_1") == runs_before
    finally:
        session.close()
