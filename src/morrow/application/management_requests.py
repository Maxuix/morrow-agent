"""Strict requests for the existing Context/Learning/Skill command services."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from morrow.application.configuration import ConfigurationCommand, validate_profile_candidate
from morrow.application.preferences.tool import ManagePreferencesArguments
from morrow.core.domain import validate_prefixed_id
from morrow.core.learning_memory import LearningConflictResolution
from morrow.core.learning_payloads import CandidatePayload
from morrow.core.models import Profile, ProtocolModel


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


class ProfileSaveDocument(ProtocolModel):
    """Complete form snapshot; every persisted field is submitted explicitly."""

    name: str
    summary: str | None = None
    tech_stack: tuple[str, ...] = ()
    goals: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    conventions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid_snapshot(self) -> ProfileSaveDocument:
        self.to_profile()
        return self

    def to_profile(self) -> Profile:
        try:
            return validate_profile_candidate(
                Profile(
                    name=self.name,
                    summary=self.summary,
                    tech_stack=list(self.tech_stack),
                    goals=list(self.goals),
                    constraints=list(self.constraints),
                    conventions=list(self.conventions),
                )
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from None


class ProfileSaveRequest(CommandRequest):
    """One atomic save of the whole workspace Profile document."""

    expected_revision: int = Field(ge=0)
    profile: ProfileSaveDocument


class PreferenceDecisionRequest(CommandRequest):
    action: Literal["accept", "reject", "suppress"]
    expected_row_version: int = Field(ge=1)
    expected_document_revision: int = Field(ge=0)
    expected_target_revision: int | None = Field(default=None, ge=1)
    edit: str | None = Field(default=None, min_length=1, max_length=512)
    reason: str | None = Field(default=None, max_length=256)


class LearningDecisionRequest(CommandRequest):
    action: Literal["accept", "reject", "suppress"]
    expected_row_version: int = Field(ge=1)
    scope: Literal["workspace"] = "workspace"
    conflict_resolution: LearningConflictResolution = LearningConflictResolution.NONE
    final_payload: CandidatePayload | None = None
    reason: str = Field(default="rejected_by_user", max_length=256)


class KnowledgeLifecycleRequest(CommandRequest):
    action: Literal["enable", "disable", "dispute", "delete"]
    expected_row_version: int = Field(ge=1)


class SkillBindingRequest(CommandRequest):
    action: Literal["enable", "disable", "pin", "update", "rollback"]
    scope: Literal["global", "workspace"] = "workspace"
    expected_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    version_id: str | None = Field(default=None, pattern=r"^skv_[A-Za-z0-9_-]+$")


class SkillDraftRequest(CommandRequest):
    reason: str = Field(default="rejected_by_user", min_length=1, max_length=256)
    action: Literal["edit", "validate", "accept", "reject"]
    expected_row_version: int = Field(ge=1)
    skill_md: str | None = Field(default=None, min_length=1, max_length=65536)


class SkillDraftCreateRequest(CommandRequest):
    candidate_id: str = Field(pattern=r"^lcn_[A-Za-z0-9_-]+$")
