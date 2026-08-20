"""Shared result DTOs for Learning candidate decisions."""

from __future__ import annotations

from typing import Literal

from morrow.core.learning import LearningCandidate, LearningScope
from morrow.core.learning_memory import LearningCandidateDecision
from morrow.core.models import ProtocolModel


class LearningPromotionResult(ProtocolModel):
    candidate: LearningCandidate
    decision: LearningCandidateDecision
    outcome: Literal[
        "activated",
        "confirmed",
        "superseded",
        "enabled",
        "candidate_only",
        "reversed",
    ]
    knowledge_id: str | None = None
    knowledge_revision_id: str | None = None
    revision: int | None = None
    memory_revision: int | None = None
    target: str | None = None
    scope: LearningScope | None = None
    path: str | None = None
    activation_id: str | None = None


__all__ = ["LearningPromotionResult"]
