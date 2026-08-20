"""Typed foreground commands for the Learning Inbox mutation boundary."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, TypeAdapter, field_validator

from morrow.core.domain import COMMAND_ID_PREFIX, validate_prefixed_id
from morrow.core.learning import (
    LEARNING_CANDIDATE_ID_PREFIX,
    LEARNING_KNOWLEDGE_ID_PREFIX,
    LEARNING_WORKSPACE_ID_PREFIX,
    _clean_bounded,
    _utc,
)
from morrow.core.learning_memory import LearningConflictResolution
from morrow.core.learning_payloads import CandidatePayload
from morrow.core.models import ProtocolModel, utc_now

_CANDIDATE_PAYLOAD_ADAPTER = TypeAdapter(CandidatePayload)


class RejectLearningCandidateCommand(ProtocolModel):
    workspace_id: str
    candidate_id: str
    expected_row_version: int = Field(ge=1)
    command_id: str
    never_suggest: bool = False
    reason: str = "rejected_by_user"

    _valid_workspace = field_validator("workspace_id")(
        lambda value: validate_prefixed_id(value, LEARNING_WORKSPACE_ID_PREFIX)
    )
    _valid_candidate = field_validator("candidate_id")(
        lambda value: validate_prefixed_id(value, LEARNING_CANDIDATE_ID_PREFIX)
    )
    _valid_command = field_validator("command_id")(
        lambda value: validate_prefixed_id(value, COMMAND_ID_PREFIX)
    )
    _clean_reason = field_validator("reason")(
        lambda value: _clean_bounded(value, label="candidate rejection reason", maximum=256)
    )


class ExpireLearningCandidatesCommand(ProtocolModel):
    workspace_id: str
    cutoff: datetime = Field(default_factory=utc_now)
    limit: int = Field(default=100, ge=1, le=500)
    command_id: str

    _valid_workspace = field_validator("workspace_id")(
        lambda value: validate_prefixed_id(value, LEARNING_WORKSPACE_ID_PREFIX)
    )
    _valid_command = field_validator("command_id")(
        lambda value: validate_prefixed_id(value, COMMAND_ID_PREFIX)
    )
    _normalize_cutoff = field_validator("cutoff", mode="before")(_utc)


class AcceptLearningCandidateCommand(ProtocolModel):
    workspace_id: str
    candidate_id: str
    expected_row_version: int = Field(ge=1)
    command_id: str
    scope: str | None = None
    conflict_resolution: LearningConflictResolution = LearningConflictResolution.NONE

    _valid_workspace = field_validator("workspace_id")(
        lambda value: validate_prefixed_id(value, LEARNING_WORKSPACE_ID_PREFIX)
    )
    _valid_candidate = field_validator("candidate_id")(
        lambda value: validate_prefixed_id(value, LEARNING_CANDIDATE_ID_PREFIX)
    )
    _valid_command = field_validator("command_id")(
        lambda value: validate_prefixed_id(value, COMMAND_ID_PREFIX)
    )
    _clean_scope = field_validator("scope")(
        lambda value: (
            None if value is None else _clean_bounded(value, label="learning scope", maximum=32)
        )
    )


class EditAndAcceptLearningCandidateCommand(AcceptLearningCandidateCommand):
    final_payload: CandidatePayload

    _validate_final_payload = field_validator("final_payload", mode="before")(
        lambda value: _CANDIDATE_PAYLOAD_ADAPTER.validate_python(
            value.model_dump(mode="json") if hasattr(value, "model_dump") else value,
            strict=False,
        )
    )


class _ProjectKnowledgeCommand(ProtocolModel):
    workspace_id: str
    knowledge_id: str
    expected_row_version: int = Field(ge=1)
    command_id: str

    _valid_workspace = field_validator("workspace_id")(
        lambda value: validate_prefixed_id(value, LEARNING_WORKSPACE_ID_PREFIX)
    )
    _valid_knowledge = field_validator("knowledge_id")(
        lambda value: validate_prefixed_id(value, LEARNING_KNOWLEDGE_ID_PREFIX)
    )
    _valid_command = field_validator("command_id")(
        lambda value: validate_prefixed_id(value, COMMAND_ID_PREFIX)
    )


class DisableProjectKnowledgeCommand(_ProjectKnowledgeCommand):
    pass


class EnableProjectKnowledgeCommand(_ProjectKnowledgeCommand):
    pass


class DeleteProjectKnowledgeCommand(_ProjectKnowledgeCommand):
    pass


class MarkProjectKnowledgeDisputedCommand(_ProjectKnowledgeCommand):
    pass


__all__ = [
    "AcceptLearningCandidateCommand",
    "DeleteProjectKnowledgeCommand",
    "DisableProjectKnowledgeCommand",
    "EditAndAcceptLearningCandidateCommand",
    "EnableProjectKnowledgeCommand",
    "ExpireLearningCandidatesCommand",
    "MarkProjectKnowledgeDisputedCommand",
    "RejectLearningCandidateCommand",
]
