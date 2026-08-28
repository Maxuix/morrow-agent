"""Subplan 50 accepted Outcome, Review, and candidate pipeline coverage."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import morrow.interfaces.cli as cli_module
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.api import OperationalApplicationService
from morrow.application.learning.candidate_pipeline import LearningCandidatePipeline
from morrow.application.learning.context import LearningContextBuilder
from morrow.application.preferences.worker import ReviewWorker
from morrow.application.tasks import TaskOutcomeAssembler
from morrow.core.application import (
    ApplicationCommandResult,
    ApplicationError,
    ApplicationErrorCode,
    QueryPage,
)
from morrow.core.domain import (
    DurableConversationRecord,
    DurableSession,
    DurableTaskRun,
    DurableTurn,
    TaskRunStatus,
    sha256_digest,
)
from morrow.core.learning import (
    CandidateDraftBatch,
    LearningCandidateType,
    LearningEvidence,
    LearningEvidenceActor,
    LearningEvidenceAuthority,
    LearningEvidenceExplicitness,
    LearningEvidencePolarity,
    LearningEvidenceSourceKind,
    LearningMode,
    LearningReviewStatus,
    LearningScope,
    LearningSuppression,
    LearningSuppressionStatus,
    PreferenceCandidatePayload,
)
from morrow.core.learning_payloads import LearningCandidateDraft, SkillCandidatePayload
from morrow.core.learning_ports import LEARNING_CONTEXT_MAX_RENDERED_CHARS
from morrow.core.models import ModelRef
from morrow.testing import FixedClock, FixedIdSource, ScriptedLearningReviewer

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class ContextReviewer:
    def __init__(self) -> None:
        self.contexts = []

    async def review(self, context, *, model: ModelRef, timeout_seconds: float):
        del model, timeout_seconds
        self.contexts.append(context)
        evidence = tuple(
            item
            for item in context.evidence
            if item.source_kind
            in {
                LearningEvidenceSourceKind.TASK_OUTCOME,
                LearningEvidenceSourceKind.USER_TURN,
            }
        )
        return CandidateDraftBatch(
            drafts=(
                LearningCandidateDraft(
                    candidate_type=LearningCandidateType.SKILL_CANDIDATE,
                    operation="set",
                    semantic_key="skill.release.workflow",
                    proposed_scope="workspace",
                    proposed_payload=SkillCandidatePayload(
                        title="Release workflow",
                        problem_pattern="A repeatable release task was completed.",
                        observed_steps=("Run release checks",),
                    ),
                    evidence_ids=tuple(item.evidence_id for item in evidence),
                    temporary_or_durable="durable",
                ),
            )
        )


class EmptyReviewer:
    def __init__(self) -> None:
        self.contexts = []

    async def review(self, context, *, model: ModelRef, timeout_seconds: float):
        del model, timeout_seconds
        self.contexts.append(context)
        return CandidateDraftBatch()


def _api(tmp_path, *, reviewer=None, review_worker=None):
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
        review_worker=review_worker,
    )
    return session, journal, api


def _accepted(api, journal, *, with_user_turn=False, user_content="以后默认使用中文回答"):
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
                    payload={"role": "user", "content": user_content},
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


class WakeRecorder:
    def __init__(self) -> None:
        self.calls = 0

    def wake(self) -> None:
        self.calls += 1


def test_task_accept_wakes_only_after_the_atomic_learning_request(tmp_path):
    wake = WakeRecorder()
    session, journal, api = _api(tmp_path, review_worker=wake)
    try:
        accepted = _accepted(api, journal)
        assert accepted.value.status is TaskRunStatus.ACCEPTED
        assert wake.calls == 1
        review = api.list_learning_reviews(
            task_outcome_id=api.list_outcomes(accepted.value.task_run_id)[0].outcome_id
        ).items[0]
        assert review.status is LearningReviewStatus.PENDING
    finally:
        session.close()


def test_task_accept_requests_learning_even_when_tool_failures_are_recorded(tmp_path, monkeypatch):
    session, journal, api = _api(tmp_path)
    original_build = TaskOutcomeAssembler.build

    def build_with_failed_tool(self, *args, **kwargs):
        return original_build(self, *args, **kwargs).model_copy(
            update={"unresolved_items": ("apply_patch:failed",)}
        )

    monkeypatch.setattr(TaskOutcomeAssembler, "build", build_with_failed_tool)
    try:
        accepted = _accepted(api, journal)
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        assert outcome.unresolved_items == ("apply_patch:failed",)
        reviews = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items
        assert len(reviews) == 1
        assert reviews[0].status is LearningReviewStatus.PENDING
    finally:
        session.close()


@pytest.mark.asyncio
async def test_worker_routes_legacy_learning_review_without_foreground_execution(tmp_path):
    session, journal, api = _api(tmp_path, reviewer=ScriptedLearningReviewer())
    try:
        accepted = _accepted(api, journal)
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        review = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items[0]
        worker = ReviewWorker(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=journal.now,
            learning_runner=api.learning_review_runner,
            model=ModelRef(provider_id="test", model_id="reviewer"),
        )

        result = await worker.drain_once()

        assert result.status == "completed"
        assert result.learning_review_id == review.review_id
        assert result.job is None
        assert api.get_learning_review(review.review_id).status is LearningReviewStatus.COMPLETED
    finally:
        session.close()


def test_preference_v2_flag_blocks_new_legacy_preference_drafts():
    pipeline = LearningCandidatePipeline(
        journal=object(),
        workspace_id="ws_1",
        id_source=FixedIdSource(),
        clock=lambda: NOW,
        events=None,
        preference_v2_enabled=True,
    )
    draft = LearningCandidateDraft(
        candidate_type=LearningCandidateType.PREFERENCE,
        operation="set",
        semantic_key="preference.language",
        proposed_scope=LearningScope.WORKSPACE,
        proposed_payload=PreferenceCandidatePayload(path="language", value="zh-CN"),
        evidence_ids=("lev_one",),
        temporary_or_durable="durable",
    )

    assert pipeline._eligible_draft(draft, {}, None) is None


def test_skill_candidate_accepts_the_evidence_the_extractor_actually_produces():
    pipeline = LearningCandidatePipeline(
        journal=object(),
        workspace_id="ws_1",
        id_source=FixedIdSource(),
        clock=lambda: NOW,
        events=None,
    )
    evidence = (
        LearningEvidence(
            evidence_id="lev_outcome",
            workspace_id="ws_1",
            origin_review_id="lrv_1",
            task_run_id="task_1",
            source_kind=LearningEvidenceSourceKind.TASK_OUTCOME,
            source_id="out_1",
            actor=LearningEvidenceActor.SYSTEM,
            authority=LearningEvidenceAuthority.DETERMINISTIC_TASK_FACT,
            explicitness=LearningEvidenceExplicitness.INFERRED,
            polarity=LearningEvidencePolarity.POSITIVE,
            content_digest="a" * 64,
            observed_at=NOW,
            created_at=NOW,
        ),
        LearningEvidence(
            evidence_id="lev_user",
            workspace_id="ws_1",
            origin_review_id="lrv_1",
            task_run_id="task_1",
            source_kind=LearningEvidenceSourceKind.USER_TURN,
            source_id="turn_1",
            actor=LearningEvidenceActor.USER,
            authority=LearningEvidenceAuthority.BEHAVIORAL_SIGNAL,
            explicitness=LearningEvidenceExplicitness.INFERRED,
            polarity=LearningEvidencePolarity.POSITIVE,
            content_digest="b" * 64,
            observed_at=NOW,
            created_at=NOW,
        ),
    )
    draft = LearningCandidateDraft(
        candidate_type=LearningCandidateType.SKILL_CANDIDATE,
        operation="set",
        semantic_key="skill.release.workflow",
        proposed_scope=LearningScope.WORKSPACE,
        proposed_payload=SkillCandidatePayload(
            title="Release workflow",
            problem_pattern="A repeatable release task was completed.",
            observed_steps=("Run release checks",),
        ),
        evidence_ids=tuple(item.evidence_id for item in evidence),
        temporary_or_durable="durable",
    )

    eligible = pipeline._eligible_draft(
        draft,
        {item.evidence_id: item for item in evidence},
        SimpleNamespace(task_status=TaskRunStatus.ACCEPTED),
    )

    assert eligible is not None
    assert eligible[0] is LearningCandidateType.SKILL_CANDIDATE


@pytest.mark.asyncio
async def test_example_language_does_not_downgrade_a_repeatable_skill_request(tmp_path):
    reviewer = ContextReviewer()
    session, journal, api = _api(tmp_path, reviewer=reviewer)
    try:
        accepted = _accepted(
            api,
            journal,
            with_user_turn=True,
            user_content="以后每次发布都运行版本检查，例如 v1.2.3。",
        )
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        review = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items[0]

        result = await api.run_learning_review(review.review_id)

        assert len(result.candidate_ids) == 1
        candidate = api.get_learning_candidate(result.candidate_ids[0])
        assert candidate is not None
        assert candidate.candidate_type is LearningCandidateType.SKILL_CANDIDATE
        user = next(
            item
            for item in reviewer.contexts[0].evidence
            if item.source_kind is LearningEvidenceSourceKind.USER_TURN
        )
        assert user.authority is LearningEvidenceAuthority.BEHAVIORAL_SIGNAL
        assert user.explicitness is LearningEvidenceExplicitness.BEHAVIORAL
        assert user.polarity is LearningEvidencePolarity.NEUTRAL
    finally:
        session.close()


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


@pytest.mark.asyncio
async def test_review_evidence_stays_with_the_accepted_task_after_a_later_task(tmp_path):
    reviewer = EmptyReviewer()
    session, journal, api = _api(tmp_path, reviewer=reviewer)
    try:
        accepted = _accepted(api, journal, with_user_turn=True)
        journal.append_records(
            "ws_1",
            (
                DurableConversationRecord(
                    record_id="rec_a_terminal",
                    session_id="ses_1",
                    conversation_position=2,
                    kind="terminal",
                    payload={"finish_reason": "error", "interrupted_call_ids": []},
                ),
            ),
        )
        later = api.task_new("ses_1", command_id="cmd_new_later").value
        journal.create_turn(
            "ws_1",
            DurableTurn(
                turn_id="turn_2",
                session_id="ses_1",
                task_run_id=later.task_run_id,
                client_message_id="client_2",
                created_at=NOW,
            ),
        )
        journal.append_records(
            "ws_1",
            (
                DurableConversationRecord(
                    record_id="rec_b_user",
                    session_id="ses_1",
                    conversation_position=3,
                    kind="message",
                    payload={"role": "user", "content": "later task content must stay isolated"},
                ),
                DurableConversationRecord(
                    record_id="rec_b_terminal",
                    session_id="ses_1",
                    conversation_position=4,
                    kind="terminal",
                    payload={"finish_reason": "error", "interrupted_call_ids": []},
                ),
            ),
        )
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        review = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items[0]
        result = await api.run_learning_review(review.review_id)
        assert result.review.status is LearningReviewStatus.COMPLETED
        user_excerpts = [
            item.excerpt_redacted
            for item in reviewer.contexts[0].evidence
            if item.source_kind.value == "user_turn"
        ]
        assert user_excerpts == ["以后默认使用中文回答"]
    finally:
        session.close()


@pytest.mark.asyncio
async def test_long_user_turn_is_digest_preserved_and_excerpt_bounded(tmp_path):
    reviewer = EmptyReviewer()
    session, journal, api = _api(tmp_path, reviewer=reviewer)
    try:
        content = "以后默认使用中文回答 " + ("补充说明 " * 200)
        accepted = _accepted(api, journal, with_user_turn=True, user_content=content)
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        review = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items[0]
        result = await api.run_learning_review(review.review_id)
        assert result.review.status is LearningReviewStatus.COMPLETED
        evidence = api.list_learning_evidence(review_id=review.review_id)
        user = next(item for item in evidence if item.source_kind.value == "user_turn")
        assert user.excerpt_redacted is not None
        assert 0 < len(user.excerpt_redacted) <= 512
        assert user.content_digest == sha256_digest(content)
    finally:
        session.close()


def test_context_builder_projects_large_outcomes_into_the_rendered_budget(tmp_path):
    session, journal, api = _api(tmp_path)
    try:
        accepted = _accepted(api, journal)
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        review = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items[0]
        huge = outcome.model_copy(
            update={
                "summary": "s" * 2_000,
                "changed_paths": tuple(f"src/path_{index}" for index in range(128)),
                "validation_facts": tuple("validation " * 50 for _ in range(64)),
                "side_effects": tuple("side effect " * 50 for _ in range(64)),
                "unresolved_items": tuple("unresolved " * 50 for _ in range(64)),
                "completion_basis": tuple("basis " * 50 for _ in range(64)),
                "feedback": tuple("feedback " * 50 for _ in range(64)),
            }
        )
        context = LearningContextBuilder(journal=journal, workspace_id="ws_1").build(
            review=review,
            outcome=huge,
            policy=api.learning_policy_status().policy,
            evidence=(),
        )
        assert len(context.model_dump_json().encode("utf-8")) <= LEARNING_CONTEXT_MAX_RENDERED_CHARS
        assert len(context.task_outcome.validation_facts) <= 8
    finally:
        session.close()


def test_cancel_running_review_releases_its_foreground_lease(tmp_path):
    session, journal, api = _api(tmp_path)
    try:
        accepted = _accepted(api, journal)
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        review = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items[0]
        claimed = api.learning_review_runner._claim(review.review_id, expected_row_version=None)
        cancelled = api.cancel_learning_review(review.review_id)
        assert claimed.status is LearningReviewStatus.RUNNING
        assert cancelled.review.status is LearningReviewStatus.FAILED
        assert cancelled.review.failure_code.value == "cancelled"
        assert cancelled.review.lease_id is None
    finally:
        session.close()


def test_headless_task_accept_prints_pending_review_id(monkeypatch, capsys, tmp_path):
    task = DurableTaskRun(task_run_id="task_1", session_id="ses_1", workspace_id="ws_1")
    outcome = SimpleNamespace(outcome_id="out_1")
    review = SimpleNamespace(review_id="lrv_1", status=LearningReviewStatus.PENDING)

    class FakeApi:
        def task_accept(self, task_run_id, *, command_id, expected_row_version):
            assert (task_run_id, command_id, expected_row_version) == ("task_1", None, None)
            return ApplicationCommandResult(task, None)

        def list_outcomes(self, task_run_id):
            assert task_run_id == "task_1"
            return (outcome,)

        def list_learning_reviews(self, *, task_outcome_id):
            assert task_outcome_id == "out_1"
            return QueryPage((review,))

    monkeypatch.setattr(
        cli_module,
        "_state_services",
        lambda **_kwargs: (None, "handle", FakeApi(), None, None),
    )
    monkeypatch.setattr(cli_module, "_close_state", lambda _handle: None)
    cli_module._task_command(
        lambda api, task_run_id, command_id, expected_row_version: api.task_accept(
            task_run_id,
            command_id=command_id,
            expected_row_version=expected_row_version,
        ),
        "task_1",
        None,
        "ws_1",
        tmp_path,
        None,
        None,
        show_learning_review=True,
    )
    assert "已排入 Learning Review：lrv_1" in capsys.readouterr().out


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
        assert candidate.candidate_type is LearningCandidateType.SKILL_CANDIDATE
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
        assert len(candidate.evidence_ids) == 4
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
                candidate_type=LearningCandidateType.SKILL_CANDIDATE,
                scope=LearningScope.WORKSPACE,
                semantic_key="skill.release.workflow",
                reason="user declined this workflow",
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
        retried = await api.retry_learning_review(review.review_id)
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
