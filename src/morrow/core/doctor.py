"""Bounded, read-only operational diagnosis contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import (
    WORKSPACE_ID_PREFIX,
    canonical_json_bytes,
    refuse_secret_material,
    validate_prefixed_id,
)
from morrow.core.models import ProtocolModel, utc_now

DOCTOR_REPORT_MAX_BYTES = 32 * 1024


class DoctorHealth(StrEnum):
    OK = "ok"
    NEEDS_RECOVERY = "needs_recovery"
    NEEDS_REPAIR = "needs_repair"
    READ_ONLY = "read_only"
    FUTURE_SCHEMA = "future_schema"


class DoctorSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DoctorIssue(ProtocolModel):
    code: str = Field(min_length=1, max_length=64)
    severity: DoctorSeverity
    summary: str = Field(min_length=1, max_length=240)
    count: int = Field(default=1, ge=1)

    @field_validator("code")
    @classmethod
    def valid_code(cls, value: str) -> str:
        cleaned = value.strip().lower().replace(" ", "_")
        if not cleaned or any(
            char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in cleaned
        ):
            raise ValueError("doctor issue code is invalid")
        return cleaned

    @field_validator("summary")
    @classmethod
    def clean_summary(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("doctor issue summary must not be empty")
        return cleaned


class DoctorReport(ProtocolModel):
    workspace_id: str
    health: DoctorHealth
    schema_version: int | None = None
    checks: tuple[str, ...] = ()
    counts: dict[str, int] = Field(default_factory=dict)
    issues: tuple[DoctorIssue, ...] = ()
    generated_at: datetime = Field(default_factory=utc_now)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, WORKSPACE_ID_PREFIX)

    @model_validator(mode="after")
    def enforce_budget_and_redaction(self) -> DoctorReport:
        payload = canonical_json_bytes(self.model_dump(mode="json"))
        if len(payload) > DOCTOR_REPORT_MAX_BYTES:
            raise ValueError("doctor report exceeds its durable budget")
        refuse_secret_material(payload, label="doctor report")
        return self

    def json_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))
