"""Typed foreground commands for the Learning Inbox mutation boundary."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from morrow.core.domain import COMMAND_ID_PREFIX, validate_prefixed_id
from morrow.core.learning import (
    LEARNING_CANDIDATE_ID_PREFIX,
    LEARNING_WORKSPACE_ID_PREFIX,
    _clean_bounded,
    _utc,
)
from morrow.core.models import ProtocolModel, utc_now


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


__all__ = ["ExpireLearningCandidatesCommand", "RejectLearningCandidateCommand"]
