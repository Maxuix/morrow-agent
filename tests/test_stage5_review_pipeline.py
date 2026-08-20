"""Subplan 50 accepted Outcome, Review, and candidate pipeline coverage."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.api import OperationalApplicationService
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import (
    DurableConversationRecord,
    DurableSession,
    DurableTurn,
    TaskRunStatus,
)
from morrow.core.learning import (
    CandidateDraftBatch,
    LearningCandidateType,
    LearningMode,
    LearningReviewStatus,
    LearningScope,
    LearningSuppression,
    LearningSuppressionStatus,
    PreferenceCandidatePayload,
)
from morrow.core.learning_payloads import LearningCandidateDraft
from morrow.core.models import ModelRef
from morrow.testing import FixedClock, FixedIdSource

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class ContextReviewer:
    def __init__(self) -> None:
        self.contexts = []

    async def review(self, context, *, model: ModelRef, timeout_seconds: float):
        del model, timeout_seconds
        self.contexts.append(context)
        evidence = next(
            item for item in context.evidence if item.authority.value == "user_explicit_persistent"
        )
        return CandidateDraftBatch(
            drafts=(
                LearningCandidateDraft(
                    candidate_type=LearningCandidateType.PREFERENCE,
                    operation="set",
                    semantic_key="preference.language",
                    proposed_scope="workspace",
                    proposed_payload=PreferenceCandidatePayload(
                        path="language",
                        value="zh-CN",
                    ),
                    evidence_ids=(evidence.evidence_id,),
                    temporary_or_durable="durable",
                ),
            )
        )


def _api(tmp_path, *, reviewer=None):
    store = OperationalStore(tmp_path / "state", clock=FixedClock(NOW), maintenance_timeout=0)
    session = store.initialize()
    journal = SqliteOperationalJournal(session)
    journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
    api = OperationalApplicationService(
        journal=journal,
        workspace_id="ws_1",
        id_source=FixedIdSource(),
        clock=lambda: NOW,
        learning_reviewer=reviewer,
        learning_model=ModelRef(provider_id="test", model_id="reviewer"),
    )
    return session, journal, api


def _accepted(api, journal, *, with_user_turn=False):
    task = api.task_new("ses_1", command_id="cmd_new").value
    if with_user_turn:
        journal.create_turn(
            "ws_1",
            DurableTurn(
                turn_id="turn_1",
                session_id="ses_1",
                task_run_id=task.task_run_id,
                client_message_id="client_1",
                created_at=NOW,
            ),
        )
        journal.append_records(
            "ws_1",
            (
                DurableConversationRecord(
                    record_id="rec_1",
                    session_id="ses_1",
                    conversation_position=1,
                    kind="message",
                    payload={"role": "user", "content": "以后默认使用中文回答"},
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
    return api.task_accept(
        ready.task_run_id,
        command_id="cmd_accept",
        expected_row_version=ready.row_version,
    )


def test_accept_atomically_requests_one_review_and_replays_without_duplicates(tmp_path):
    session, journal, api = _api(tmp_path)
    try:
        accepted = _accepted(api, journal)
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        reviews = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items
        assert len(reviews) == 1
        assert reviews[0].review_version == 1
        assert reviews[0].status is LearningReviewStatus.PENDING

        replay = api.task_accept(
            accepted.value.task_run_id,
            command_id="cmd_accept",
            expected_row_version=accepted.value.row_version - 1,
        )
        assert replay.receipt is not None
        assert replay.receipt.disposition.value == "replay"
        assert len(api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items) == 1
        assert [event.event_type for event in journal.list_application_events("ws_1")][-2:] == [
            "task.accepted",
            "learning.review_requested",
        ]
    finally:
        session.close()


def test_policy_off_skips_review_and_unresolved_outcome_is_not_requested(tmp_path):
    session, journal, api = _api(tmp_path)
    try:
        api.set_learning_mode(LearningMode.OFF, command_id="cmd_policy")
        accepted = _accepted(api, journal)
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        assert api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items == ()
        assert journal.list_application_events("ws_1")[-1].event_type == (
            "learning.review_not_requested"
        )
    finally:
        session.close()


@pytest.mark.asyncio
async def test_runner_builds_bounded_context_and_persists_one_candidate(tmp_path):
    reviewer = ContextReviewer()
    session, journal, api = _api(tmp_path, reviewer=reviewer)
    try:
        accepted = _accepted(api, journal, with_user_turn=True)
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        review = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items[0]
        result = await api.run_learning_review(review.review_id)
        assert result.review.status is LearningReviewStatus.COMPLETED
        assert len(result.candidate_ids) == 1
        candidate = api.get_learning_candidate(result.candidate_ids[0])
        assert candidate is not None
        assert candidate.candidate_type is LearningCandidateType.PREFERENCE
        assert len(reviewer.contexts) == 1
        user_evidence = [
            item for item in reviewer.contexts[0].evidence if item.source_kind.value == "user_turn"
        ]
        assert user_evidence and user_evidence[0].excerpt_redacted == "以后默认使用中文回答"
        assert [event.event_type for event in journal.list_application_events("ws_1")][-3:] == [
            "learning.review_started",
            "learning.candidate_proposed",
            "learning.review_completed",
        ]
    finally:
        session.close()


def test_explicit_review_supersedes_previous_and_is_idempotent(tmp_path):
    session, journal, api = _api(tmp_path)
    try:
        accepted = _accepted(api, journal)
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        first = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items[0]
        requested = api.request_learning_review(
            outcome.outcome_id,
            expected_latest_version=first.review_version,
            command_id="cmd_review_again",
        )
        assert requested.value.review_version == 2
        assert requested.value.supersedes_review_id == first.review_id
        assert api.get_learning_review(first.review_id).status is LearningReviewStatus.SUPERSEDED
        replay = api.request_learning_review(
            outcome.outcome_id,
            expected_latest_version=first.review_version,
            command_id="cmd_review_again",
        )
        assert replay.receipt.disposition.value == "replay"
        assert replay.value.review_id == requested.value.review_id
        with pytest.raises(ApplicationError) as stale:
            api.request_learning_review(
                outcome.outcome_id,
                expected_latest_version=1,
                command_id="cmd_review_stale",
            )
        assert stale.value.code is ApplicationErrorCode.STALE
    finally:
        session.close()


@pytest.mark.asyncio
async def test_re_review_deduplicates_and_attaches_new_evidence(tmp_path):
    reviewer = ContextReviewer()
    session, journal, api = _api(tmp_path, reviewer=reviewer)
    try:
        accepted = _accepted(api, journal, with_user_turn=True)
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        first = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items[0]
        first_result = await api.run_learning_review(first.review_id)
        candidate_id = first_result.candidate_ids[0]
        second = api.request_learning_review(
            outcome.outcome_id,
            expected_latest_version=first.review_version,
            command_id="cmd_review_again",
        ).value
        second_result = await api.run_learning_review(second.review_id)
        assert second_result.candidate_ids == ()
        assert second_result.duplicate_count == 1
        candidate = api.get_learning_candidate(candidate_id)
        assert candidate is not None
        assert len(candidate.evidence_ids) == 2
    finally:
        session.close()


@pytest.mark.asyncio
async def test_active_suppression_prevents_candidate_creation(tmp_path):
    reviewer = ContextReviewer()
    session, journal, api = _api(tmp_path, reviewer=reviewer)
    try:
        accepted = _accepted(api, journal, with_user_turn=True)
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        journal.put_learning_suppression(
            "ws_1",
            LearningSuppression(
                suppression_id="lsp_1",
                workspace_id="ws_1",
                candidate_type=LearningCandidateType.PREFERENCE,
                scope=LearningScope.WORKSPACE,
                semantic_key="preference.language",
                reason="user declined this preference",
                status=LearningSuppressionStatus.ACTIVE,
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        review = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items[0]
        result = await api.run_learning_review(review.review_id)
        assert result.candidate_ids == ()
        assert result.suppressed_count == 1
        assert api.list_learning_candidates().items == ()
    finally:
        session.close()


@pytest.mark.asyncio
async def test_reviewer_failure_is_retryable_without_changing_accepted_task(tmp_path):
    class FailingReviewer:
        async def review(self, context, *, model, timeout_seconds):
            del context, model, timeout_seconds
            raise RuntimeError("provider failure")

    session, journal, api = _api(tmp_path, reviewer=FailingReviewer())
    try:
        accepted = _accepted(api, journal)
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        review = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items[0]
        failed = await api.run_learning_review(review.review_id)
        assert failed.review.status is LearningReviewStatus.FAILED
        assert failed.review.failure_code.value == "provider_unavailable"
        assert api.get_task(accepted.value.task_run_id).status is TaskRunStatus.ACCEPTED
        retried = await api.run_learning_review(review.review_id)
        assert retried.review.status is LearningReviewStatus.FAILED
        latest = api.get_learning_review(review.review_id)
        assert latest.attempt_count == 2
    finally:
        session.close()


def test_accept_review_event_failure_rolls_back_task_outcome_and_review(tmp_path, monkeypatch):
    session, journal, api = _api(tmp_path)
    try:
        task = api.task_new("ses_1", command_id="cmd_new").value
        ready = api.tasks._transition(
            task,
            TaskRunStatus.READY_FOR_ACCEPTANCE,
            reason="answer",
            turn_id=None,
            command_id=None,
        )
        original = api._event

        def fail_learning_event(txn, **kwargs):
            if kwargs["event_type"] == "learning.review_requested":
                raise RuntimeError("injected learning event failure")
            return original(txn, **kwargs)

        monkeypatch.setattr(api, "_event", fail_learning_event)
        with pytest.raises(ApplicationError) as error:
            api.task_accept(
                ready.task_run_id,
                command_id="cmd_accept_rollback",
                expected_row_version=ready.row_version,
            )
        assert error.value.code is ApplicationErrorCode.UNAVAILABLE
        assert api.get_task(task.task_run_id).status is TaskRunStatus.READY_FOR_ACCEPTANCE
        assert api.list_outcomes(task.task_run_id) == ()
        assert api.list_learning_reviews().items == ()
        assert journal.list_application_events("ws_1")[-1].event_type == "task.created"
    finally:
        session.close()
