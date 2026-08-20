"""Small UI command messages shared by the REPL command adapter."""

from __future__ import annotations

from dataclasses import dataclass

from morrow.core.learning_memory import LearningConflictResolution
from morrow.core.recovery import RecoveryResolution


@dataclass
class CommandResult:
    lines: list[str]
    action: str | None = None
    value: object | None = None


@dataclass(frozen=True)
class RecoveryCommandRequest:
    report_id: str
    resolution: RecoveryResolution
    item_id: str | None = None


@dataclass(frozen=True)
class LearningCandidateCommandRequest:
    candidate_id: str
    expected_row_version: int
    scope: str | None = None
    conflict_resolution: LearningConflictResolution = LearningConflictResolution.NONE
    final_payload: object | None = None
    never_suggest: bool = False
    reason: str = "rejected_by_user"


@dataclass(frozen=True)
class LearningPromotionRecoveryRequest:
    operation_id: str
    action: str


@dataclass(frozen=True)
class KnowledgeLifecycleCommandRequest:
    knowledge_id: str
    expected_row_version: int
    operation: str


__all__ = [
    "CommandResult",
    "KnowledgeLifecycleCommandRequest",
    "LearningCandidateCommandRequest",
    "LearningPromotionRecoveryRequest",
    "RecoveryCommandRequest",
]
