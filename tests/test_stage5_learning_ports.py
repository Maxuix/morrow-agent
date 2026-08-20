"""Stage 5 context and test-double boundary contracts."""

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from morrow.core.domain import TaskOutcome, TaskOutcomeTrigger, TaskRunStatus
from morrow.core.learning import (
    CandidateDraftBatch,
    LearningEvidence,
    LearningEvidenceActor,
    LearningEvidenceAuthority,
    LearningEvidenceExplicitness,
    LearningEvidencePolarity,
    LearningEvidenceSourceKind,
    LearningPolicy,
    LearningScope,
)
from morrow.core.learning_ports import LearningContext
from morrow.core.models import ModelRef
from morrow.testing import FixedLearningClock, FixedLearningIdSource, ScriptedLearningReviewer

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _outcome(*, workspace_id: str = "ws_1") -> TaskOutcome:
    return TaskOutcome(
        outcome_id="out_1",
        workspace_id=workspace_id,
        session_id="ses_1",
        task_run_id="task_1",
        version=1,
        trigger=TaskOutcomeTrigger.ACCEPTANCE,
        task_status=TaskRunStatus.ACCEPTED,
        summary="任务已接受。",
        created_at=NOW,
    )


def _evidence(index: int) -> LearningEvidence:
    return LearningEvidence(
        evidence_id=f"lev_{index}",
        workspace_id="ws_1",
        origin_review_id="lrv_1",
        task_run_id="task_1",
        source_kind=LearningEvidenceSourceKind.USER_TURN,
        source_id=f"turn_{index}",
        actor=LearningEvidenceActor.USER,
        authority=LearningEvidenceAuthority.USER_EXPLICIT_PERSISTENT,
        explicitness=LearningEvidenceExplicitness.EXPLICIT,
        polarity=LearningEvidencePolarity.POSITIVE,
        scope_hint=LearningScope.WORKSPACE,
        content_digest="a" * 64,
        observed_at=NOW,
        created_at=NOW,
    )


def test_learning_context_is_workspace_scoped_and_budgeted():
    context = LearningContext(
        workspace_id="ws_1",
        task_outcome=_outcome(),
        policy=LearningPolicy.default_for("ws_1", now=NOW),
        candidate_budget=3,
        rendered_char_budget=1024,
    )
    assert context.workspace_id == "ws_1"

    with pytest.raises(ValidationError):
        LearningContext(
            workspace_id="ws_1",
            task_outcome=_outcome(workspace_id="ws_2"),
            policy=LearningPolicy.default_for("ws_1", now=NOW),
            candidate_budget=1,
            rendered_char_budget=1024,
        )

    with pytest.raises(ValidationError):
        LearningContext(
            workspace_id="ws_1",
            task_outcome=_outcome(),
            evidence=tuple(_evidence(index) for index in range(33)),
            policy=LearningPolicy.default_for("ws_1", now=NOW),
            candidate_budget=1,
            rendered_char_budget=1024,
        )
    with pytest.raises(ValidationError):
        LearningContext(
            workspace_id="ws_1",
            task_outcome=_outcome(),
            policy=LearningPolicy.default_for("ws_1", now=NOW),
            candidate_budget=4,
            rendered_char_budget=1024,
        )


def test_learning_fixtures_are_deterministic_and_reviewer_is_no_tool():
    assert FixedLearningClock(NOW).now() == NOW
    ids = FixedLearningIdSource()
    assert ids.new_id("lrv") == "lrv_1"
    assert ids.new_id("lrv") == "lrv_2"

    reviewer = ScriptedLearningReviewer([CandidateDraftBatch()])
    context = LearningContext(
        workspace_id="ws_1",
        task_outcome=_outcome(),
        policy=LearningPolicy.default_for("ws_1", now=NOW),
        candidate_budget=1,
        rendered_char_budget=1024,
    )
    result = asyncio.run(
        reviewer.review(
            context,
            model=ModelRef(provider_id="test", model_id="test"),
            timeout_seconds=1.0,
        )
    )
    assert result.drafts == ()
    assert reviewer.calls == [context]
