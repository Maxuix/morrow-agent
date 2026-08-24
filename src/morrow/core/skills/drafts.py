"""Bounded generated Skill Draft and validation contracts.

Drafts are review evidence, not a second Skill authority. The immutable managed
package and the YAML Binding remain the only authorities after acceptance.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import (
    DIGEST_PATTERN,
    canonical_json_bytes,
    require_payload_budget,
    validate_prefixed_id,
)
from morrow.core.models import ProtocolModel, utc_now

from .identity import validate_display_version, validate_skill_id, validate_skv_id

DRAFT_ID_PREFIX = "sdr"
VALIDATION_ID_PREFIX = "sdv"
DRAFT_MAX_BYTES = 32 * 1024
DRAFT_MAX_EVIDENCE_REFS = 16
DRAFT_MAX_FINDINGS = 64
DRAFT_MAX_DEPENDENCIES = 32
DRAFT_MAX_SCRIPTS = 64
DRAFT_MAX_SAMPLES = 32
DRAFT_PACKAGE_REF_MAX_CHARS = 512
DRAFT_REASON_MAX_CHARS = 256
_TOKEN = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")


class SkillDraftStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class SkillFindingSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


def _digest(value: str, *, label: str) -> str:
    if not DIGEST_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must be a SHA-256 hex digest")
    return value


def _ref(value: str, *, prefix: str, label: str) -> str:
    try:
        return validate_prefixed_id(value, prefix)
    except ValueError as exc:
        raise ValueError(f"{label} is invalid") from exc


def _relative_ref(value: str) -> str:
    cleaned = value.strip().replace("\\", "/")
    if (
        not cleaned
        or cleaned.startswith("/")
        or "\x00" in cleaned
        or any(part in {"", ".", ".."} for part in cleaned.split("/"))
        or len(cleaned) > DRAFT_PACKAGE_REF_MAX_CHARS
    ):
        raise ValueError("draft package reference is invalid")
    return cleaned


class SkillValidationFinding(ProtocolModel):
    """A code-only finding; matched secrets and raw text never enter the report."""

    code: str = Field(min_length=1, max_length=64)
    severity: SkillFindingSeverity
    path: str | None = None

    @field_validator("code")
    @classmethod
    def valid_code(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if _TOKEN.fullmatch(cleaned) is None:
            raise ValueError("validation finding code is invalid")
        return cleaned

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: str | None) -> str | None:
        return None if value is None else _relative_ref(value)


class SkillDraftValidationReport(ProtocolModel):
    """Deterministic, bounded package facts suitable for operator review."""

    validation_id: str
    draft_id: str
    revision: int = Field(ge=1, le=1024)
    validator_version: str = Field(default="stage6-v1", min_length=1, max_length=64)
    valid: bool
    findings: tuple[SkillValidationFinding, ...] = ()
    requested_permissions: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ()
    scripts: tuple[str, ...] = ()
    sample_fixtures: tuple[str, ...] = ()
    tree_digest: str
    file_count: int = Field(ge=0, le=4096)
    total_bytes: int = Field(ge=0, le=16 * 1024 * 1024)
    report_digest: str
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("validation_id")
    @classmethod
    def valid_validation_id(cls, value: str) -> str:
        return _ref(value, prefix=VALIDATION_ID_PREFIX, label="validation ID")

    @field_validator("draft_id")
    @classmethod
    def valid_draft_id(cls, value: str) -> str:
        return _ref(value, prefix=DRAFT_ID_PREFIX, label="draft ID")

    _valid_tree = field_validator("tree_digest")(
        lambda value: _digest(value, label="validation tree digest")
    )
    _valid_report = field_validator("report_digest")(
        lambda value: _digest(value, label="validation report digest")
    )

    @field_validator(
        "requested_permissions", "dependencies", "platforms", "scripts", "sample_fixtures"
    )
    @classmethod
    def bounded_lists(cls, values: tuple[str, ...], info) -> tuple[str, ...]:
        limits = {
            "requested_permissions": 16,
            "dependencies": DRAFT_MAX_DEPENDENCIES,
            "platforms": 16,
            "scripts": DRAFT_MAX_SCRIPTS,
            "sample_fixtures": DRAFT_MAX_SAMPLES,
        }
        if len(values) > limits[info.field_name]:
            raise ValueError(f"{info.field_name} exceeds its budget")
        cleaned = tuple(" ".join(item.split())[:256] for item in values)
        if any(not item for item in cleaned):
            raise ValueError(f"{info.field_name} contains an empty item")
        return cleaned

    @field_validator("findings")
    @classmethod
    def bounded_findings(
        cls, values: tuple[SkillValidationFinding, ...]
    ) -> tuple[SkillValidationFinding, ...]:
        if len(values) > DRAFT_MAX_FINDINGS:
            raise ValueError("validation report contains too many findings")
        return values

    @model_validator(mode="after")
    def bounded(self) -> SkillDraftValidationReport:
        require_payload_budget(
            canonical_json_bytes(self.model_dump(mode="json")),
            DRAFT_MAX_BYTES,
            label="validation report",
        )
        if self.valid and any(
            item.severity is SkillFindingSeverity.ERROR for item in self.findings
        ):
            raise ValueError("valid validation report cannot contain error findings")
        return self


class SkillDraft(ProtocolModel):
    """One immutable Draft revision and its generated package reference."""

    draft_id: str
    workspace_id: str
    candidate_id: str
    root_draft_id: str
    parent_draft_id: str | None = None
    revision: int = Field(ge=1, le=1024)
    status: SkillDraftStatus = SkillDraftStatus.DRAFT
    skill_id: str
    name: str = Field(min_length=1, max_length=128)
    display_version: str | None = None
    scope_id: str
    candidate_fingerprint: str
    evidence_refs: tuple[str, ...] = ()
    package_ref: str
    tree_digest: str
    file_count: int = Field(ge=0, le=4096)
    total_bytes: int = Field(ge=0, le=16 * 1024 * 1024)
    validation_id: str | None = None
    accepted_version_id: str | None = None
    acceptance_command_id: str | None = None
    rejection_reason: str | None = None
    row_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("draft_id", "root_draft_id", "parent_draft_id")
    @classmethod
    def valid_draft_refs(cls, value: str | None, info) -> str | None:
        return None if value is None else _ref(value, prefix=DRAFT_ID_PREFIX, label=info.field_name)

    @field_validator("workspace_id", "scope_id")
    @classmethod
    def valid_workspace(cls, value: str) -> str:
        return validate_prefixed_id(value, "ws")

    @field_validator("candidate_id")
    @classmethod
    def valid_candidate(cls, value: str) -> str:
        return _ref(value, prefix="lcn", label="candidate ID")

    _valid_skill = field_validator("skill_id")(validate_skill_id)
    _valid_candidate_digest = field_validator("candidate_fingerprint")(
        lambda value: _digest(value, label="candidate fingerprint")
    )
    _valid_tree_digest = field_validator("tree_digest")(
        lambda value: _digest(value, label="draft tree digest")
    )

    @field_validator("display_version")
    @classmethod
    def valid_display(cls, value: str | None) -> str | None:
        return validate_display_version(value)

    @field_validator("validation_id")
    @classmethod
    def valid_validation(cls, value: str | None) -> str | None:
        return (
            None
            if value is None
            else _ref(value, prefix=VALIDATION_ID_PREFIX, label="validation ID")
        )

    @field_validator("accepted_version_id")
    @classmethod
    def valid_version(cls, value: str | None) -> str | None:
        return None if value is None else validate_skv_id(value)

    @field_validator("acceptance_command_id")
    @classmethod
    def valid_command(cls, value: str | None) -> str | None:
        return None if value is None else _ref(value, prefix="cmd", label="acceptance command ID")

    @field_validator("evidence_refs")
    @classmethod
    def bounded_evidence(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > DRAFT_MAX_EVIDENCE_REFS:
            raise ValueError("draft evidence references exceed their budget")
        if len(values) != len(set(values)):
            raise ValueError("draft evidence references must be unique")
        return tuple(_relative_ref(value) for value in values)

    @field_validator("package_ref")
    @classmethod
    def valid_package_ref(cls, value: str) -> str:
        return _relative_ref(value)

    @field_validator("rejection_reason")
    @classmethod
    def bounded_rejection(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned or len(cleaned) > DRAFT_REASON_MAX_CHARS:
            raise ValueError("draft rejection reason is invalid")
        return cleaned

    @model_validator(mode="after")
    def valid_state(self) -> SkillDraft:
        if self.workspace_id != self.scope_id:
            raise ValueError("generated Skill Draft must be workspace scoped")
        if self.root_draft_id == self.draft_id and self.parent_draft_id is not None:
            raise ValueError("root Draft cannot have a parent")
        if self.status is SkillDraftStatus.ACCEPTED and self.accepted_version_id is None:
            raise ValueError("accepted Draft must reference an immutable version")
        if self.status is not SkillDraftStatus.ACCEPTED and self.accepted_version_id is not None:
            raise ValueError("only accepted Drafts may reference an immutable version")
        if self.status is SkillDraftStatus.REJECTED and self.rejection_reason is None:
            raise ValueError("rejected Draft must have a reason")
        if self.status is not SkillDraftStatus.REJECTED and self.rejection_reason is not None:
            raise ValueError("only rejected Drafts may have a rejection reason")
        require_payload_budget(
            canonical_json_bytes(self.model_dump(mode="json")), DRAFT_MAX_BYTES, label="Skill Draft"
        )
        return self


class SkillDraftDiff(ProtocolModel):
    """Digest-only file comparison; Draft content is not copied into query rows."""

    draft_id: str
    parent_draft_id: str | None = None
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    unchanged_count: int = Field(default=0, ge=0, le=4096)

    @field_validator("draft_id", "parent_draft_id")
    @classmethod
    def valid_refs(cls, value: str | None, info) -> str | None:
        return None if value is None else _ref(value, prefix=DRAFT_ID_PREFIX, label=info.field_name)


__all__ = [
    "DRAFT_ID_PREFIX",
    "DRAFT_MAX_BYTES",
    "SkillDraft",
    "SkillDraftDiff",
    "SkillDraftStatus",
    "SkillDraftValidationReport",
    "SkillFindingSeverity",
    "SkillValidationFinding",
    "VALIDATION_ID_PREFIX",
]
