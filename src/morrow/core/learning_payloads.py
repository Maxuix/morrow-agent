"""Strict discriminated payloads returned by the bounded Learning Reviewer."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import canonical_json_bytes, require_payload_budget, validate_prefixed_id
from morrow.core.learning import (
    LEARNING_CANDIDATE_PROPOSAL_MAX_BYTES,
    LEARNING_EVIDENCE_ID_PREFIX,
    LEARNING_MAX_CANDIDATES_PER_REVIEW,
    LEARNING_MAX_REFERENCE_IDS,
    LearningCandidateOperation,
    LearningCandidateType,
    LearningScope,
    _local_code,
    _semantic_key,
    _utc,
)
from morrow.core.learning_safety import normalize_learning_text
from morrow.core.models import ProtocolModel


class LearningPayload(ProtocolModel):
    """Strict base for every discriminated candidate payload."""


class PreferenceCandidatePayload(LearningPayload):
    candidate_type: Literal[LearningCandidateType.PREFERENCE] = LearningCandidateType.PREFERENCE
    path: Literal["language", "response_detail", "instructions"]
    value: str | tuple[str, ...]

    @field_validator("value")
    @classmethod
    def valid_value(cls, value: str | tuple[str, ...]) -> str | tuple[str, ...]:
        if isinstance(value, str):
            return normalize_learning_text(value, label="preference value", maximum=2_048)
        if len(value) > 32:
            raise ValueError("preference instructions contain too many items")
        return tuple(
            normalize_learning_text(item, label="preference instruction", maximum=512)
            for item in value
        )

    @model_validator(mode="after")
    def path_value_match(self) -> PreferenceCandidatePayload:
        if self.path == "instructions" and not isinstance(self.value, tuple):
            raise ValueError("instructions preference requires a tuple of strings")
        if self.path != "instructions" and not isinstance(self.value, str):
            raise ValueError("scalar preference requires a string value")
        if self.path == "response_detail" and self.value not in {
            "concise",
            "balanced",
            "detailed",
        }:
            raise ValueError("response_detail must be concise, balanced, or detailed")
        return self


class ProfileCandidatePayload(LearningPayload):
    candidate_type: Literal[LearningCandidateType.PROFILE] = LearningCandidateType.PROFILE
    path: Literal["name", "summary", "goals", "tech_stack", "constraints", "conventions"]
    value: str | tuple[str, ...]

    @field_validator("value")
    @classmethod
    def valid_value(cls, value: str | tuple[str, ...]) -> str | tuple[str, ...]:
        if isinstance(value, str):
            return normalize_learning_text(value, label="profile value", maximum=2_048)
        if len(value) > 64:
            raise ValueError("profile list value contains too many items")
        return tuple(
            normalize_learning_text(item, label="profile item", maximum=512) for item in value
        )

    @model_validator(mode="after")
    def path_value_match(self) -> ProfileCandidatePayload:
        if self.path in {"name", "summary"} and not isinstance(self.value, str):
            raise ValueError("profile scalar field requires a string value")
        if self.path in {"goals", "tech_stack", "constraints", "conventions"} and not isinstance(
            self.value, tuple
        ):
            raise ValueError("profile list field requires a tuple value")
        return self


class ProjectKnowledgeCategory(StrEnum):
    ARCHITECTURE = "architecture"
    CONVENTION = "convention"
    DECISION = "decision"
    ENVIRONMENT = "environment"
    DOMAIN = "domain"
    OTHER = "other"


class ProjectKnowledgeCandidatePayload(LearningPayload):
    candidate_type: Literal[LearningCandidateType.PROJECT_KNOWLEDGE] = (
        LearningCandidateType.PROJECT_KNOWLEDGE
    )
    category: ProjectKnowledgeCategory
    semantic_key: str
    statement: str
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    _valid_key = field_validator("semantic_key")(_semantic_key)
    _clean_statement = field_validator("statement")(
        lambda value: normalize_learning_text(value, label="knowledge statement", maximum=4_096)
    )
    _normalize_time = field_validator("valid_from", "valid_until", mode="before")(_utc)

    @model_validator(mode="after")
    def valid_window(self) -> ProjectKnowledgeCandidatePayload:
        if self.valid_from is not None and self.valid_until is not None:
            if self.valid_until <= self.valid_from:
                raise ValueError("knowledge valid_until must follow valid_from")
        return self


class SkillCandidatePayload(LearningPayload):
    candidate_type: Literal[LearningCandidateType.SKILL_CANDIDATE] = (
        LearningCandidateType.SKILL_CANDIDATE
    )
    title: str
    problem_pattern: str
    observed_steps: tuple[str, ...]
    tool_names: tuple[str, ...] = ()

    _clean_title = field_validator("title")(
        lambda value: normalize_learning_text(value, label="skill candidate title", maximum=256)
    )
    _clean_pattern = field_validator("problem_pattern")(
        lambda value: normalize_learning_text(value, label="skill problem pattern", maximum=512)
    )

    @field_validator("observed_steps")
    @classmethod
    def clean_steps(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not 1 <= len(values) <= 32:
            raise ValueError("skill candidate needs one to thirty-two observed steps")
        return tuple(
            normalize_learning_text(value, label="skill observed step", maximum=512)
            for value in values
        )

    @field_validator("tool_names")
    @classmethod
    def clean_tools(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > 32:
            raise ValueError("skill candidate contains too many tool names")
        return tuple(_local_code(value, label="skill tool name") for value in values)


class WorkflowFeedbackCandidatePayload(LearningPayload):
    candidate_type: Literal[LearningCandidateType.WORKFLOW_FEEDBACK] = (
        LearningCandidateType.WORKFLOW_FEEDBACK
    )
    workflow_name: str
    edit_summary: str
    result_summary: str | None = None

    _clean_workflow = field_validator("workflow_name")(
        lambda value: _local_code(value, label="workflow name")
    )
    _clean_edit = field_validator("edit_summary")(
        lambda value: normalize_learning_text(value, label="workflow edit summary", maximum=1_024)
    )
    _clean_result = field_validator("result_summary")(
        lambda value: (
            None
            if value is None
            else normalize_learning_text(value, label="workflow result summary", maximum=1_024)
        )
    )


class OrchestrationPolicyCandidatePayload(LearningPayload):
    candidate_type: Literal[LearningCandidateType.ORCHESTRATION_POLICY_CANDIDATE] = (
        LearningCandidateType.ORCHESTRATION_POLICY_CANDIDATE
    )
    trigger: str
    workflow_name: str
    rule_summary: str

    _clean_trigger = field_validator("trigger")(_semantic_key)
    _clean_workflow = field_validator("workflow_name")(
        lambda value: _local_code(value, label="orchestration workflow name")
    )
    _clean_rule = field_validator("rule_summary")(
        lambda value: normalize_learning_text(value, label="orchestration rule", maximum=1_024)
    )


type CandidatePayload = Annotated[
    PreferenceCandidatePayload
    | ProfileCandidatePayload
    | ProjectKnowledgeCandidatePayload
    | SkillCandidatePayload
    | WorkflowFeedbackCandidatePayload
    | OrchestrationPolicyCandidatePayload,
    Field(discriminator="candidate_type"),
]


class LearningCandidateDraft(ProtocolModel):
    candidate_type: LearningCandidateType
    operation: LearningCandidateOperation
    semantic_key: str
    proposed_scope: LearningScope
    proposed_payload: CandidatePayload
    evidence_ids: tuple[str, ...]
    temporary_or_durable: Literal["temporary", "durable"]

    _valid_key = field_validator("semantic_key")(_semantic_key)

    @field_validator("evidence_ids")
    @classmethod
    def valid_evidence_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not 1 <= len(values) <= LEARNING_MAX_REFERENCE_IDS:
            raise ValueError("candidate draft requires one to sixteen evidence IDs")
        cleaned = tuple(
            validate_prefixed_id(value, LEARNING_EVIDENCE_ID_PREFIX) for value in values
        )
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("candidate draft evidence IDs must be unique")
        return cleaned

    @model_validator(mode="after")
    def matching_payload_and_scope(self) -> LearningCandidateDraft:
        if self.proposed_payload.candidate_type is not self.candidate_type:
            raise ValueError("candidate draft type does not match its payload")
        if self.candidate_type is LearningCandidateType.PREFERENCE:
            if self.proposed_scope not in {LearningScope.GLOBAL, LearningScope.WORKSPACE}:
                raise ValueError("preference candidate scope must be global or workspace")
        elif self.proposed_scope is not LearningScope.WORKSPACE:
            raise ValueError("this candidate type is workspace scoped")
        payload_budget = canonical_json_bytes(self.proposed_payload.model_dump(mode="json"))
        require_payload_budget(
            payload_budget, LEARNING_CANDIDATE_PROPOSAL_MAX_BYTES, label="candidate proposal"
        )
        return self

    @property
    def payload(self) -> CandidatePayload:
        return self.proposed_payload


class CandidateDraftBatch(ProtocolModel):
    drafts: tuple[LearningCandidateDraft, ...] = ()

    @field_validator("drafts")
    @classmethod
    def bounded_drafts(
        cls, values: tuple[LearningCandidateDraft, ...]
    ) -> tuple[LearningCandidateDraft, ...]:
        if len(values) > LEARNING_MAX_CANDIDATES_PER_REVIEW:
            raise ValueError("a learning review may return at most three candidate drafts")
        return values


__all__ = [
    "CandidateDraftBatch",
    "CandidatePayload",
    "LearningCandidateDraft",
    "LearningPayload",
    "OrchestrationPolicyCandidatePayload",
    "PreferenceCandidatePayload",
    "ProfileCandidatePayload",
    "ProjectKnowledgeCandidatePayload",
    "ProjectKnowledgeCategory",
    "SkillCandidatePayload",
    "WorkflowFeedbackCandidatePayload",
]
