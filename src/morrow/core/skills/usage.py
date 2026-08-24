"""Observational, bounded Skill usage facts and comparison projections."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import (
    DIGEST_PATTERN,
    ArtifactReference,
    canonical_json_bytes,
    validate_prefixed_id,
)
from morrow.core.models import ProtocolModel, utc_now

from .identity import validate_skill_id, validate_skv_id

USAGE_ID_PREFIX = "sug"
USAGE_MAX_ARTIFACT_REFS = 16
USAGE_MAX_METRIC = 10**9


class SkillUsageStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


class SkillComparisonStatus(StrEnum):
    INSUFFICIENT_DATA = "insufficient_data"
    DESCRIPTIVE = "descriptive"


def _digest(value: str, *, label: str) -> str:
    if not DIGEST_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must be a SHA-256 hex digest")
    return value


class SkillUsage(ProtocolModel):
    """Facts emitted after a terminal run; never an activation authority."""

    usage_id: str
    workspace_id: str
    agent_run_id: str
    task_run_id: str | None = None
    selection_id: str | None = None
    skill_id: str
    version_id: str
    activation_reason: str
    status: SkillUsageStatus
    user_correction: bool = False
    input_tokens: int = Field(default=0, ge=0, le=USAGE_MAX_METRIC)
    output_tokens: int = Field(default=0, ge=0, le=USAGE_MAX_METRIC)
    duration_ms: int = Field(default=0, ge=0, le=USAGE_MAX_METRIC)
    tool_call_count: int = Field(default=0, ge=0, le=USAGE_MAX_METRIC)
    artifact_refs: tuple[ArtifactReference, ...] = ()
    facts_digest: str
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("usage_id")
    @classmethod
    def valid_usage(cls, value: str) -> str:
        return validate_prefixed_id(value, USAGE_ID_PREFIX)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace(cls, value: str) -> str:
        return validate_prefixed_id(value, "ws")

    @field_validator("agent_run_id")
    @classmethod
    def valid_run(cls, value: str) -> str:
        return validate_prefixed_id(value, "arun")

    @field_validator("task_run_id")
    @classmethod
    def valid_task(cls, value: str | None) -> str | None:
        return None if value is None else validate_prefixed_id(value, "task")

    @field_validator("selection_id")
    @classmethod
    def valid_selection(cls, value: str | None) -> str | None:
        return None if value is None else validate_prefixed_id(value, "ssel")

    _valid_skill = field_validator("skill_id")(validate_skill_id)
    _valid_version = field_validator("version_id")(validate_skv_id)
    _valid_facts = field_validator("facts_digest")(
        lambda value: _digest(value, label="Skill usage facts digest")
    )

    @field_validator("activation_reason")
    @classmethod
    def bounded_reason(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned or len(cleaned) > 256:
            raise ValueError("Skill usage activation reason is invalid")
        return cleaned

    @field_validator("artifact_refs")
    @classmethod
    def bounded_artifacts(
        cls, values: tuple[ArtifactReference, ...]
    ) -> tuple[ArtifactReference, ...]:
        if len(values) > USAGE_MAX_ARTIFACT_REFS:
            raise ValueError("Skill usage contains too many Artifact references")
        keys = {(item.artifact_id, item.role) for item in values}
        if len(keys) != len(values):
            raise ValueError("Skill usage Artifact references must be unique")
        return values

    @model_validator(mode="after")
    def facts_are_bounded(self) -> SkillUsage:
        if len(canonical_json_bytes(self.model_dump(mode="json"))) > 16 * 1024:
            raise ValueError("Skill usage facts exceed their byte budget")
        return self


class SkillUsageMetrics(ProtocolModel):
    sample_count: int = Field(ge=0, le=100_000)
    success_count: int = Field(ge=0, le=100_000)
    average_duration_ms: int | None = Field(default=None, ge=0, le=USAGE_MAX_METRIC)
    average_tool_calls: int | None = Field(default=None, ge=0, le=USAGE_MAX_METRIC)

    @model_validator(mode="after")
    def counts_match(self) -> SkillUsageMetrics:
        if self.success_count > self.sample_count:
            raise ValueError("Skill usage success count exceeds sample count")
        return self


class SkillUsageComparison(ProtocolModel):
    """A descriptive comparison; it never declares a superior version."""

    skill_id: str
    left_version_id: str
    right_version_id: str
    status: SkillComparisonStatus
    reason: str
    left: SkillUsageMetrics
    right: SkillUsageMetrics

    _valid_skill = field_validator("skill_id")(validate_skill_id)
    _valid_left = field_validator("left_version_id")(validate_skv_id)
    _valid_right = field_validator("right_version_id")(validate_skv_id)

    @field_validator("reason")
    @classmethod
    def bounded_reason(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned or len(cleaned) > 256:
            raise ValueError("Skill usage comparison reason is invalid")
        return cleaned


__all__ = [
    "SkillComparisonStatus",
    "SkillUsage",
    "SkillUsageComparison",
    "SkillUsageMetrics",
    "SkillUsageStatus",
    "USAGE_ID_PREFIX",
]
