"""Durable contracts for the cross-store configuration promotion Saga."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import (
    COMMAND_ID_PREFIX,
    DIGEST_PATTERN,
    refuse_secret_material,
    require_payload_budget,
    validate_prefixed_id,
)
from morrow.core.learning import (
    LEARNING_CANDIDATE_ID_PREFIX,
    LEARNING_DECISION_ID_PREFIX,
    LearningScope,
)
from morrow.core.models import ProtocolModel, utc_now

PROMOTION_OPERATION_ID_PREFIX = "pop"
CONFIGURATION_ACTIVATION_ID_PREFIX = "act"
PROMOTION_JSON_MAX_BYTES = 8 * 1024
_TARGET_PATTERN = re.compile(r"^(preferences|profile)$")
_PATH_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,255}$")


class PromotionOperationState(StrEnum):
    PREPARED = "prepared"
    FINALIZED = "finalized"
    ABORTED = "aborted"
    NEEDS_RESOLUTION = "needs_resolution"


class PromotionFailureCode(StrEnum):
    STALE = "stale"
    CONFLICT = "conflict"
    VALIDATION = "validation"
    FILESYSTEM = "filesystem"
    YAML = "yaml"
    NEEDS_RECOVERY = "needs_recovery"
    INTERNAL = "internal"


class ConfigurationActivationOperation(StrEnum):
    SET = "set"
    APPEND = "append"
    REPLACE = "replace"
    REMOVE = "remove"


class ConfigurationActivationStatus(StrEnum):
    ACTIVE = "active"
    REVERSED = "reversed"
    SUPERSEDED = "superseded"


class PromotionOperation(ProtocolModel):
    operation_id: str
    command_id: str
    request_digest: str
    workspace_id: str
    candidate_id: str
    candidate_row_version: int = Field(ge=1)
    target: str
    scope: LearningScope
    path: str
    prepared_change_json: str
    prepared_change_digest: str
    state: PromotionOperationState
    before_revision: int | None = Field(default=None, ge=0)
    before_digest: str | None = None
    after_digest: str | None = None
    applied_revision: int | None = Field(default=None, ge=1)
    row_version: int = Field(default=1, ge=1)
    failure_code: PromotionFailureCode | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    prepared_at: datetime | None = None
    finalized_at: datetime | None = None

    @field_validator("operation_id")
    @classmethod
    def valid_operation_id(cls, value: str) -> str:
        return validate_prefixed_id(value, PROMOTION_OPERATION_ID_PREFIX)

    @field_validator("command_id")
    @classmethod
    def valid_command_id(cls, value: str) -> str:
        return validate_prefixed_id(value, COMMAND_ID_PREFIX)

    @field_validator("candidate_id")
    @classmethod
    def valid_candidate_id(cls, value: str) -> str:
        return validate_prefixed_id(value, LEARNING_CANDIDATE_ID_PREFIX)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, "ws")

    @field_validator("request_digest", "prepared_change_digest", "before_digest", "after_digest")
    @classmethod
    def valid_digest(cls, value: str | None) -> str | None:
        if value is not None and not DIGEST_PATTERN.fullmatch(value):
            raise ValueError("promotion digest must be a SHA-256 hex digest")
        return value

    @field_validator("target")
    @classmethod
    def valid_target(cls, value: str) -> str:
        if not _TARGET_PATTERN.fullmatch(value):
            raise ValueError("configuration promotion target is invalid")
        return value

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        if not _PATH_PATTERN.fullmatch(value):
            raise ValueError("configuration promotion path is invalid")
        return value

    @field_validator("prepared_change_json")
    @classmethod
    def valid_prepared_change_json(cls, value: str) -> str:
        payload = value.encode("utf-8")
        require_payload_budget(payload, PROMOTION_JSON_MAX_BYTES, label="prepared configuration")
        refuse_secret_material(payload, label="prepared configuration")
        if len(payload) < 2:
            raise ValueError("prepared configuration must not be empty")
        return value

    @model_validator(mode="after")
    def state_invariants(self) -> PromotionOperation:
        if self.state is PromotionOperationState.FINALIZED and self.applied_revision is None:
            raise ValueError("finalized promotion must have an applied revision")
        if self.state is not PromotionOperationState.FINALIZED and self.finalized_at is not None:
            raise ValueError("only finalized promotion may have finalized_at")
        if self.updated_at < self.created_at:
            raise ValueError("promotion updated_at must not precede created_at")
        return self


class ConfigurationActivation(ProtocolModel):
    activation_id: str
    workspace_id: str
    candidate_id: str
    decision_id: str
    operation_id: str
    target: str
    scope: LearningScope
    path: str
    operation: ConfigurationActivationOperation
    applied_revision: int = Field(ge=1)
    before_digest: str | None = None
    after_digest: str
    value_digest: str
    inverse_command_json: str
    inverse_command_digest: str
    supersedes_activation_id: str | None = None
    reverses_activation_id: str | None = None
    status: ConfigurationActivationStatus = ConfigurationActivationStatus.ACTIVE
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("activation_id")
    @classmethod
    def valid_activation_id(cls, value: str) -> str:
        return validate_prefixed_id(value, CONFIGURATION_ACTIVATION_ID_PREFIX)

    @field_validator("candidate_id")
    @classmethod
    def valid_candidate_id(cls, value: str) -> str:
        return validate_prefixed_id(value, LEARNING_CANDIDATE_ID_PREFIX)

    @field_validator("decision_id")
    @classmethod
    def valid_decision_id(cls, value: str) -> str:
        return validate_prefixed_id(value, LEARNING_DECISION_ID_PREFIX)

    @field_validator("operation_id")
    @classmethod
    def valid_operation_id(cls, value: str) -> str:
        return validate_prefixed_id(value, PROMOTION_OPERATION_ID_PREFIX)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, "ws")

    @field_validator("target")
    @classmethod
    def valid_target(cls, value: str) -> str:
        if not _TARGET_PATTERN.fullmatch(value):
            raise ValueError("configuration activation target is invalid")
        return value

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        if not _PATH_PATTERN.fullmatch(value):
            raise ValueError("configuration activation path is invalid")
        return value

    @field_validator("before_digest", "after_digest", "value_digest", "inverse_command_digest")
    @classmethod
    def valid_digest(cls, value: str | None) -> str | None:
        if value is not None and not DIGEST_PATTERN.fullmatch(value):
            raise ValueError("activation digest must be a SHA-256 hex digest")
        return value

    @field_validator("inverse_command_json")
    @classmethod
    def valid_inverse_command_json(cls, value: str) -> str:
        payload = value.encode("utf-8")
        require_payload_budget(payload, PROMOTION_JSON_MAX_BYTES, label="inverse configuration")
        refuse_secret_material(payload, label="inverse configuration")
        if len(payload) < 2:
            raise ValueError("inverse configuration must not be empty")
        return value

    @field_validator("supersedes_activation_id", "reverses_activation_id")
    @classmethod
    def valid_related_activation_id(cls, value: str | None) -> str | None:
        if value is not None:
            return validate_prefixed_id(value, CONFIGURATION_ACTIVATION_ID_PREFIX)
        return value

    @model_validator(mode="after")
    def timestamp_order(self) -> ConfigurationActivation:
        if self.updated_at < self.created_at:
            raise ValueError("activation updated_at must not precede created_at")
        return self


__all__ = [
    "CONFIGURATION_ACTIVATION_ID_PREFIX",
    "PROMOTION_OPERATION_ID_PREFIX",
    "ConfigurationActivation",
    "ConfigurationActivationOperation",
    "ConfigurationActivationStatus",
    "PromotionFailureCode",
    "PromotionOperation",
    "PromotionOperationState",
]
