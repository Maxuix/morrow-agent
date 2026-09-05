"""Strict requests for the existing Context/Learning/Skill command services."""

from typing import Literal

from pydantic import Field, field_validator

from morrow.application.configuration import ConfigurationCommand
from morrow.application.preferences.tool import ManagePreferencesArguments
from morrow.core.domain import validate_prefixed_id
from morrow.core.learning_memory import LearningConflictResolution
from morrow.core.learning_payloads import CandidatePayload
from morrow.core.models import ProtocolModel


class CommandRequest(ProtocolModel):
    command_id: str

    @field_validator("command_id")
    @classmethod
    def valid_command(cls, value):
        return validate_prefixed_id(value, "cmd")


class PreferenceWriteRequest(CommandRequest):
    arguments: ManagePreferencesArguments

    @field_validator("arguments", mode="before")
    @classmethod
    def parse_arguments(cls, value):
        return ManagePreferencesArguments.model_validate(value, strict=False)

    @field_validator("arguments")
    @classmethod
    def require_revision(cls, value):
        if value.expected_revision is None:
            raise ValueError("expected_revision is required")
        return value


class ProfileWriteRequest(CommandRequest):
    command: ConfigurationCommand
    expected_revision: int = Field(ge=0)


class PreferenceDecisionRequest(CommandRequest):
    action: Literal["accept", "reject", "suppress"]
    expected_row_version: int = Field(ge=1)
    expected_document_revision: int = Field(ge=0)
    expected_target_revision: int | None = Field(default=None, ge=1)
    edit: str | None = Field(default=None, min_length=1, max_length=512)


class LearningDecisionRequest(CommandRequest):
    action: Literal["accept", "reject", "suppress"]
    expected_row_version: int = Field(ge=1)
    scope: Literal["workspace"] = "workspace"
    conflict_resolution: LearningConflictResolution = LearningConflictResolution.NONE
    final_payload: CandidatePayload | None = None


class KnowledgeLifecycleRequest(CommandRequest):
    action: Literal["enable", "disable", "dispute", "delete"]
    expected_row_version: int = Field(ge=1)


class SkillBindingRequest(CommandRequest):
    action: Literal["enable", "disable", "pin", "update", "rollback"]
    scope: Literal["global", "workspace"] = "workspace"
    expected_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    version_id: str | None = Field(default=None, pattern=r"^skv_[A-Za-z0-9_-]+$")


class SkillDraftRequest(CommandRequest):
    action: Literal["edit", "validate", "accept", "reject"]
    expected_row_version: int = Field(ge=1)
    skill_md: str | None = Field(default=None, min_length=1, max_length=65536)


class SkillDraftCreateRequest(CommandRequest):
    candidate_id: str = Field(pattern=r"^lcn_[A-Za-z0-9_-]+$")


class WorkflowFeedbackRequest(CommandRequest):
    workflow_run_id: str = Field(pattern=r"^wrun_[A-Za-z0-9_-]+$")
    kind: Literal[
        "too_complex",
        "missing_exploration",
        "reviewer_useful",
        "reviewer_not_useful",
        "model_expensive",
        "prefer_template",
        "avoid_template",
    ]
    template: Literal["direct", "explore_implement_verify"] | None = None


class WorkflowPolicyDecisionRequest(CommandRequest):
    action: Literal["accept", "reject"]
    expected_row_version: int = Field(ge=1)


class WorkflowEvaluationRequest(CommandRequest):
    multi_run_id: str = Field(pattern=r"^wrun_[A-Za-z0-9_-]+$")
    direct_run_id: str | None = Field(default=None, pattern=r"^wrun_[A-Za-z0-9_-]+$")
    direct_estimated_requests: int | None = Field(default=None, ge=0, le=1000000)
    direct_quality: int | None = Field(default=None, ge=0, le=4)
    multi_quality: int | None = Field(default=None, ge=0, le=4)
