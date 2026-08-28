"""S51.2 bounded Learning and Project Knowledge query projections."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.learning import LearningCandidateType, LearningScope
from morrow.core.learning_commands import (
    ExpireLearningCandidatesCommand,
    RejectLearningCandidateCommand,
)
from morrow.core.learning_memory import LearningCandidateDecisionKind, LearningConflictResolution
from test_stage5_configuration_promotion import _promotion_subjects
from test_stage5_review_pipeline import ContextReviewer, _accepted, _api


@pytest.mark.asyncio
async def test_learning_application_exposes_bounded_status_views_and_pure_preview(tmp_path):
    reviewer = ContextReviewer()
    session, journal, api = _api(tmp_path, reviewer=reviewer)
    try:
        accepted = _accepted(api, journal, with_user_turn=True)
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        review = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items[0]
        await api.run_learning_review(review.review_id)

        status = api.learning_status()
        assert status.pending_reviews == 0
        assert status.proposed_candidates == 1
        page = api.list_learning_candidate_views(
            candidate_type=LearningCandidateType.SKILL_CANDIDATE,
            limit=1,
        )
        assert len(page.items) == 1
        candidate = api.get_learning_candidate_view(page.items[0].candidate_id)
        assert candidate is not None
        assert len(candidate.evidence) == 2
        assert candidate.target.available is False

        preview = api.preview_learning_candidate_decision(page.items[0].candidate_id)
        assert preview.expected_row_version == page.items[0].row_version
        assert preview.available is True
        assert preview.reason == "candidate_only_acceptance"
        assert journal.get_learning_candidate("ws_1", page.items[0].candidate_id) is not None
        assert journal.get_project_knowledge_head_by_key("ws_1", "skill.release.workflow") is None
    finally:
        session.close()


def test_reject_preview_fails_closed_for_accept_only_arguments(tmp_path):
    _app, identity, handle, _journal, api, candidate = _promotion_subjects(tmp_path)
    try:
        invalid_arguments = (
            ("edit", candidate.proposed_payload.model_copy(update={"value": "English"})),
            ("scope", LearningScope.GLOBAL),
            ("conflict_resolution", LearningConflictResolution.REPLACE),
        )
        for name, value in invalid_arguments:
            with pytest.raises(ApplicationError) as error:
                api.preview_learning_candidate_decision(
                    candidate.candidate_id,
                    decision_intent=LearningCandidateDecisionKind.REJECT,
                    **{name: value},
                )
            assert error.value.code is ApplicationErrorCode.INVALID
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_inbox_queries_lazily_expire_due_candidates(tmp_path):
    reviewer = ContextReviewer()
    session, journal, api = _api(tmp_path, reviewer=reviewer)
    try:
        accepted = _accepted(api, journal, with_user_turn=True)
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        review = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items[0]
        await api.run_learning_review(review.review_id)
        candidate = api.list_learning_candidate_views(status="proposed").items[0]
        session.run_write(
            lambda executor: executor.execute(
                "UPDATE learning_candidates SET expires_at_unix = ? WHERE candidate_id = ?",
                (int(datetime(2025, 12, 31, tzinfo=UTC).timestamp()), candidate.candidate_id),
            )
        )

        assert api.list_learning_candidate_views(status="proposed").items == ()
        expired = journal.get_learning_candidate("ws_1", candidate.candidate_id)
        assert expired is not None and expired.status.value == "expired"
        decisions = journal.list_learning_candidate_decisions(
            "ws_1", candidate_id=candidate.candidate_id
        )
        assert len(decisions) == 1 and decisions[0].kind.value == "expire"
        assert journal.list_application_events("ws_1")[-1].event_type == (
            "learning.candidate_expired"
        )
    finally:
        session.close()


def test_learning_and_memory_query_boundaries_reject_invalid_filters(tmp_path):
    session, _journal, api = _api(tmp_path)
    try:
        with pytest.raises(ApplicationError) as status_error:
            api.list_learning_candidate_views(status="not-a-status")
        assert status_error.value.code is ApplicationErrorCode.INVALID
        with pytest.raises(ApplicationError) as category_error:
            api.list_project_knowledge(category="not-a-category")
        assert category_error.value.code is ApplicationErrorCode.INVALID
        with pytest.raises(ApplicationError) as cursor_error:
            api.list_learning_review_views(cursor="-1")
        assert cursor_error.value.code is ApplicationErrorCode.INVALID
        assert api.list_project_knowledge().items == ()
    finally:
        session.close()


@pytest.mark.asyncio
async def test_reject_is_replay_safe_and_never_suggest_creates_an_atomic_suppression(tmp_path):
    reviewer = ContextReviewer()
    session, journal, api = _api(tmp_path, reviewer=reviewer)
    try:
        accepted = _accepted(api, journal, with_user_turn=True)
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        review = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items[0]
        await api.run_learning_review(review.review_id)
        candidate = api.list_learning_candidate_views().items[0]

        command = RejectLearningCandidateCommand(
            workspace_id="ws_1",
            candidate_id=candidate.candidate_id,
            expected_row_version=candidate.row_version,
            command_id="cmd_reject_1",
            never_suggest=True,
        )
        result = api.learning.reject_candidate(command)
        assert result.value.candidate.status.value == "rejected"
        assert result.value.suppression is not None
        assert result.value.decision.kind.value == "reject_and_suppress"
        assert [event.event_type for event in journal.list_application_events("ws_1")][-2:] == [
            "learning.candidate_rejected",
            "learning.suppression_created",
        ]

        replay = api.learning.reject_candidate(command)
        assert replay.receipt is not None
        assert replay.receipt.disposition.value == "replay"
        assert replay.value.decision.decision_id == result.value.decision.decision_id
        view = api.get_learning_candidate_view(candidate.candidate_id)
        assert view is not None and len(view.suppressions) == 1
        with pytest.raises(ApplicationError) as stale:
            api.learning.reject_candidate(
                command.model_copy(update={"command_id": "cmd_reject_stale"})
            )
        assert stale.value.code is ApplicationErrorCode.STALE
    finally:
        session.close()


@pytest.mark.asyncio
async def test_expiry_uses_cutoff_is_bounded_and_replays_without_new_decisions(tmp_path):
    reviewer = ContextReviewer()
    session, journal, api = _api(tmp_path, reviewer=reviewer)
    try:
        accepted = _accepted(api, journal, with_user_turn=True)
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        review = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items[0]
        await api.run_learning_review(review.review_id)
        candidate = api.list_learning_candidate_views().items[0]
        past = datetime(2025, 12, 31, tzinfo=UTC)
        session.run_write(
            lambda executor: executor.execute(
                "UPDATE learning_candidates SET expires_at_unix = ? WHERE candidate_id = ?",
                (int(past.timestamp()), candidate.candidate_id),
            )
        )
        command = ExpireLearningCandidatesCommand(
            workspace_id="ws_1",
            cutoff=datetime(2026, 1, 1, tzinfo=UTC),
            limit=1,
            command_id="cmd_expire_1",
        )
        result = api.learning.expire_candidates(command)
        assert result.value.candidate_ids == (candidate.candidate_id,)
        assert (
            journal.get_learning_candidate("ws_1", candidate.candidate_id).status.value == "expired"
        )
        decision_count = len(
            journal.list_learning_candidate_decisions("ws_1", candidate_id=candidate.candidate_id)
        )
        replay = api.learning.expire_candidates(command)
        assert replay.receipt is not None
        assert replay.receipt.disposition.value == "replay"
        assert replay.value.candidate_ids == result.value.candidate_ids
        assert (
            len(
                journal.list_learning_candidate_decisions(
                    "ws_1", candidate_id=candidate.candidate_id
                )
            )
            == decision_count
        )
    finally:
        session.close()
