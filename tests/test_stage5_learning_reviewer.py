from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from morrow.adapters.models.learning_reviewer import ModelLearningReviewer
from morrow.core.domain import TaskOutcome, TaskOutcomeTrigger, TaskRunStatus
from morrow.core.learning import (
    LearningEvidence,
    LearningEvidenceActor,
    LearningEvidenceAuthority,
    LearningEvidenceExplicitness,
    LearningEvidencePolarity,
    LearningEvidenceSourceKind,
    LearningPolicy,
)
from morrow.core.learning_ports import LearningContext, LearningReviewerError
from morrow.core.models import ModelErrorCode, ModelRef

NOW = datetime(2026, 1, 1, tzinfo=UTC)
MODEL = ModelRef(provider_id="test", model_id="reviewer")


class RecordingProvider:
    def __init__(self, responses, *, delay: float = 0.0) -> None:
        self.responses = list(responses)
        self.delay = delay
        self.calls = []

    async def complete(self, model, messages):
        self.calls.append((model, list(messages)))
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


def _context(*, evidence_ids: tuple[str, ...] = ("lev_1",), candidate_budget: int = 3):
    outcome = TaskOutcome(
        outcome_id="out_1",
        workspace_id="ws_1",
        session_id="ses_1",
        task_run_id="task_1",
        version=1,
        trigger=TaskOutcomeTrigger.ACCEPTANCE,
        task_status=TaskRunStatus.ACCEPTED,
        summary="任务已接受。",
        created_at=NOW,
    )
    evidence = tuple(
        LearningEvidence(
            evidence_id=evidence_id,
            workspace_id="ws_1",
            origin_review_id="lrv_1",
            task_run_id="task_1",
            source_kind=LearningEvidenceSourceKind.USER_TURN,
            source_id=f"turn_{index}",
            actor=LearningEvidenceActor.USER,
            authority=LearningEvidenceAuthority.USER_EXPLICIT_PERSISTENT,
            explicitness=LearningEvidenceExplicitness.EXPLICIT,
            polarity=LearningEvidencePolarity.POSITIVE,
            content_digest="a" * 64,
            observed_at=NOW,
            created_at=NOW,
        )
        for index, evidence_id in enumerate(evidence_ids, start=1)
    )
    return LearningContext(
        workspace_id="ws_1",
        task_outcome=outcome,
        evidence=evidence,
        policy=LearningPolicy.default_for("ws_1", now=NOW),
        candidate_budget=candidate_budget,
        rendered_char_budget=4096,
    )


def _valid_response(evidence_id: str = "lev_1") -> str:
    return (
        '{"drafts":[{"candidate_type":"preference","operation":"set",'
        '"semantic_key":"preference.language","proposed_scope":"workspace",'
        '"proposed_payload":{"candidate_type":"preference","path":"language",'
        f'"value":"zh"}},"evidence_ids":["{evidence_id}"],'
        '"temporary_or_durable":"durable"}]}'
    )


@pytest.mark.asyncio
async def test_model_learning_reviewer_uses_two_explicit_no_tool_messages():
    provider = RecordingProvider([_valid_response()])
    reviewer = ModelLearningReviewer(provider)

    result = await reviewer.review(_context(), model=MODEL, timeout_seconds=1.0)

    assert len(result.drafts) == 1
    assert reviewer.last_repair_used is False
    assert len(provider.calls) == 1
    _, messages = provider.calls[0]
    assert [message.role for message in messages] == ["system", "user"]
    assert "stage5-v1" in messages[0].content
    assert '"schema_version":"stage5-learning-v1"' in messages[1].content
    assert "lev_1" in messages[1].content
    assert not any(getattr(message, "tool_calls", ()) for message in messages)


@pytest.mark.asyncio
async def test_model_learning_reviewer_repairs_once_with_same_bounded_context():
    provider = RecordingProvider(["not json", _valid_response()])
    reviewer = ModelLearningReviewer(provider)

    result = await reviewer.review(_context(), model=MODEL, timeout_seconds=1.0)

    assert len(result.drafts) == 1
    assert reviewer.last_repair_used is True
    assert len(provider.calls) == 2
    first_prompt = provider.calls[0][1][1].content
    repair_prompt = provider.calls[1][1][1].content
    assert "repair" not in first_prompt
    assert "invalid_json" in repair_prompt
    assert "CandidateDraftBatch" in repair_prompt
    assert "lev_1" in repair_prompt
    assert "not json" not in repair_prompt


@pytest.mark.asyncio
async def test_model_learning_reviewer_rejects_invented_evidence_after_one_repair():
    provider = RecordingProvider([_valid_response("lev_missing"), _valid_response("lev_missing")])
    reviewer = ModelLearningReviewer(provider)

    with pytest.raises(LearningReviewerError) as error:
        await reviewer.review(_context(), model=MODEL, timeout_seconds=1.0)

    assert error.value.code is ModelErrorCode.INVALID_RESPONSE
    assert error.value.category == "repair_failed"
    assert len(provider.calls) == 2
    assert "lev_missing" not in provider.calls[1][1][1].content


@pytest.mark.asyncio
async def test_model_learning_reviewer_maps_timeout_without_repair():
    provider = RecordingProvider([_valid_response()], delay=0.05)
    reviewer = ModelLearningReviewer(provider)

    with pytest.raises(LearningReviewerError) as error:
        await reviewer.review(_context(), model=MODEL, timeout_seconds=0.001)

    assert error.value.code is ModelErrorCode.TIMEOUT
    assert len(provider.calls) == 1
    assert reviewer.last_repair_used is False


@pytest.mark.asyncio
async def test_model_learning_reviewer_rejects_oversized_request_before_provider_call():
    provider = RecordingProvider([_valid_response()])
    reviewer = ModelLearningReviewer(provider, request_char_limit=256)

    with pytest.raises(LearningReviewerError) as error:
        await reviewer.review(_context(), model=MODEL, timeout_seconds=1.0)

    assert error.value.category == "request_budget"
    assert provider.calls == []
