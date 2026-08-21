"""Generic Preference domain contracts and stable compatibility exports."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import validate_prefixed_id
from morrow.core.models import ProtocolModel, utc_now

PREFERENCE_ID_PREFIX = "pref"
PREFERENCE_EVIDENCE_ID_PREFIX = "pev"
PREFERENCE_REVIEW_JOB_ID_PREFIX = "prjob"
PREFERENCE_PROPOSAL_ID_PREFIX = "pprop"
PREFERENCE_WRITE_BATCH_ID_PREFIX = "pbat"

PREFERENCE_ENTRY_MAX_CHARS = 512
PREFERENCE_MAX_EVIDENCE_IDS = 16
PREFERENCE_MAX_OPERATIONS = 8
PREFERENCE_MAX_ACTIVE_SNAPSHOT_ENTRIES = 256
PREFERENCE_MAX_ACTIVE_SNAPSHOT_BYTES = 192 * 1024
PREFERENCE_MAX_EXCERPT_CHARS = 512
PREFERENCE_MAX_EXCERPT_BYTES = 2048
PREFERENCE_MAX_JSON_BYTES = 192 * 1024

_HEX_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_WORKSPACE_ID = re.compile(r"^ws_[A-Za-z0-9_-]{1,127}$")


class PreferenceScope(StrEnum):
    GLOBAL = "global"
    WORKSPACE = "workspace"
    SESSION = "session"


class PreferenceStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    DELETED = "deleted"


class PreferenceOperationKind(StrEnum):
    ADD = "add"
    REPLACE = "replace"
    REMOVE = "remove"


class PreferenceLifecycleKind(StrEnum):
    ENABLE = "enable"
    DISABLE = "disable"


def normalize_preference_statement(value: str) -> str:
    """Normalize a statement without assigning it a semantic category."""

    if not isinstance(value, str):
        raise ValueError("Preference statement must be text")
    if any(unicodedata.category(char) in {"Cc", "Cf"} and not char.isspace() for char in value):
        raise ValueError("Preference statement contains a control character")
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("Preference statement must not be empty")
    if len(normalized) > PREFERENCE_ENTRY_MAX_CHARS:
        raise ValueError("Preference statement exceeds 512 characters")
    return normalized


def _workspace_id(value: str) -> str:
    if not _WORKSPACE_ID.match(value):
        raise ValueError("workspace_id must be a bounded ws_ identifier")
    return value


def _digest(value: str, *, label: str) -> str:
    if not _HEX_DIGEST.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _aware(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("timestamp must be a valid ISO-8601 value") from exc
    if not isinstance(value, datetime):
        raise ValueError("timestamp must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _refs(values: tuple[str, ...], *, prefix: str, label: str) -> tuple[str, ...]:
    if len(values) > PREFERENCE_MAX_EVIDENCE_IDS:
        raise ValueError(f"{label} contains too many evidence references")
    cleaned = tuple(validate_prefixed_id(value, prefix) for value in values)
    if len(cleaned) != len(set(cleaned)):
        raise ValueError(f"{label} references must be unique")
    return cleaned


class PreferenceEntry(ProtocolModel):
    """One independently manageable Preference rule."""

    preference_id: str
    statement: str
    scope: PreferenceScope
    status: PreferenceStatus = PreferenceStatus.ACTIVE
    revision: int = Field(default=1, ge=1)
    evidence_ids: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    _valid_id = field_validator("preference_id")(
        lambda value: validate_prefixed_id(value, PREFERENCE_ID_PREFIX)
    )
    _clean_statement = field_validator("statement")(normalize_preference_statement)
    _valid_evidence = field_validator("evidence_ids")(
        lambda values: _refs(
            values, prefix=PREFERENCE_EVIDENCE_ID_PREFIX, label="Preference evidence"
        )
    )
    _normalize_time = field_validator("created_at", "updated_at", mode="before")(_aware)

    @model_validator(mode="after")
    def valid_timestamps(self) -> PreferenceEntry:
        if self.updated_at < self.created_at:
            raise ValueError("Preference updated_at must not precede created_at")
        return self


class PreferenceOperation(ProtocolModel):
    """A single generic add/replace/remove request."""

    operation: PreferenceOperationKind
    scope: PreferenceScope
    preference_id: str | None = None
    statement: str | None = None
    evidence_ids: tuple[str, ...] = ()

    _valid_id = field_validator("preference_id")(
        lambda value: None if value is None else validate_prefixed_id(value, PREFERENCE_ID_PREFIX)
    )
    _clean_statement = field_validator("statement")(
        lambda value: None if value is None else normalize_preference_statement(value)
    )
    _valid_evidence = field_validator("evidence_ids")(
        lambda values: _refs(
            values, prefix=PREFERENCE_EVIDENCE_ID_PREFIX, label="operation evidence"
        )
    )

    @model_validator(mode="after")
    def matches_kind(self) -> PreferenceOperation:
        if self.operation is PreferenceOperationKind.ADD:
            if self.preference_id is not None or self.statement is None:
                raise ValueError("add requires a statement and no target")
        elif self.operation is PreferenceOperationKind.REPLACE:
            if self.preference_id is None or self.statement is None:
                raise ValueError("replace requires one target and a statement")
        elif self.operation is PreferenceOperationKind.REMOVE:
            if self.preference_id is None or self.statement is not None:
                raise ValueError("remove requires one target and no statement")
        return self

    @property
    def target_id(self) -> str | None:
        return self.preference_id


class PreferenceLifecycleOperation(ProtocolModel):
    """An explicit enable/disable command, separate from learned operations."""

    operation: PreferenceLifecycleKind
    preference_id: str
    scope: PreferenceScope

    _valid_id = field_validator("preference_id")(
        lambda value: validate_prefixed_id(value, PREFERENCE_ID_PREFIX)
    )


_DOCUMENT_EXPORTS = frozenset(
    {
        "PreferenceDocument",
        "PreferenceEntriesPayload",
        "GlobalConfigV2",
        "WorkspacePreferenceDocumentV3",
        "FrozenPreferenceSummary",
        "PreferenceReviewSnapshot",
    }
)
_PERSISTENCE_EXPORTS = frozenset(
    {
        "PreferenceReviewJobStatus",
        "PreferenceReviewFailureCode",
        "PreferenceProposalStatus",
        "PreferenceWriteBatchStatus",
        "PreferenceSafetyRejectionCode",
        "PreferenceReviewJob",
        "PreferenceEvidence",
        "PreferenceProposal",
        "PreferenceWriteBatch",
        "preference_operation_fingerprint",
    }
)


def __getattr__(name: str):
    if name in _DOCUMENT_EXPORTS:
        from morrow.core import preference_documents

        return getattr(preference_documents, name)
    if name in _PERSISTENCE_EXPORTS:
        from morrow.core import preference_persistence_models

        return getattr(preference_persistence_models, name)
    raise AttributeError(name)


__all__ = [
    "PREFERENCE_ENTRY_MAX_CHARS",
    "PREFERENCE_EVIDENCE_ID_PREFIX",
    "PREFERENCE_ID_PREFIX",
    "PREFERENCE_MAX_ACTIVE_SNAPSHOT_BYTES",
    "PREFERENCE_MAX_ACTIVE_SNAPSHOT_ENTRIES",
    "PREFERENCE_MAX_EXCERPT_BYTES",
    "PREFERENCE_MAX_EXCERPT_CHARS",
    "PREFERENCE_MAX_OPERATIONS",
    "PREFERENCE_PROPOSAL_ID_PREFIX",
    "PREFERENCE_REVIEW_JOB_ID_PREFIX",
    "PREFERENCE_WRITE_BATCH_ID_PREFIX",
    "PreferenceEntry",
    "PreferenceLifecycleKind",
    "PreferenceLifecycleOperation",
    "PreferenceOperation",
    "PreferenceOperationKind",
    "PreferenceScope",
    "PreferenceStatus",
    "normalize_preference_statement",
] + sorted(_DOCUMENT_EXPORTS | _PERSISTENCE_EXPORTS)
