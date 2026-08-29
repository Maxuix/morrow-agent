"""YAML and frozen-snapshot Preference document contracts."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import canonical_json_bytes, validate_prefixed_id
from morrow.core.models import ModelRef, ProtocolModel, ProviderConfig, utc_now
from morrow.core.preference_models import (
    PREFERENCE_ID_PREFIX,
    PREFERENCE_MAX_ACTIVE_SNAPSHOT_BYTES,
    PREFERENCE_MAX_ACTIVE_SNAPSHOT_ENTRIES,
    PreferenceEntry,
    PreferenceScope,
    PreferenceStatus,
    _aware,
    normalize_preference_statement,
)
from morrow.core.runtime_policy import RuntimePolicyOverrides
from morrow.core.state_schema import (
    GLOBAL_CONFIG_SCHEMA_VERSION,
    WORKSPACE_PREFERENCE_SCHEMA_VERSION,
)


class PreferenceDocument(ProtocolModel):
    """A generic YAML-authoritative Preference document for one durable scope."""

    schema_version: int = Field(default=3, ge=1)
    scope: Literal["global", "workspace"]
    revision: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=utc_now)
    entries: tuple[PreferenceEntry, ...] = ()

    _normalize_time = field_validator("updated_at", mode="before")(_aware)

    @model_validator(mode="after")
    def valid_entries(self) -> PreferenceDocument:
        ids = [entry.preference_id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("Preference IDs must be unique within one document")
        if any(entry.scope.value != self.scope for entry in self.entries):
            raise ValueError("Preference entry scope must match its document")
        return self


class PreferenceEntriesPayload(ProtocolModel):
    """Nested global-config payload; ``entries`` is the only active field."""

    entries: tuple[PreferenceEntry, ...] = ()

    @model_validator(mode="after")
    def global_scope_only(self) -> PreferenceEntriesPayload:
        if any(entry.scope is not PreferenceScope.GLOBAL for entry in self.entries):
            raise ValueError("global Preference entries must use global scope")
        ids = [entry.preference_id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("global Preference IDs must be unique")
        return self


class GlobalConfig(ProtocolModel):
    """Current global config aggregate."""

    schema_version: Literal[GLOBAL_CONFIG_SCHEMA_VERSION] = GLOBAL_CONFIG_SCHEMA_VERSION
    revision: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=utc_now)
    preferences: PreferenceEntriesPayload = Field(default_factory=PreferenceEntriesPayload)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    active_model: ModelRef | None = None
    runtime_policy: RuntimePolicyOverrides | None = None

    _normalize_time = field_validator("updated_at", mode="before")(_aware)

    @model_validator(mode="after")
    def active_model_is_registered(self) -> GlobalConfig:
        if self.active_model:
            provider = self.providers.get(self.active_model.provider_id)
            if provider is None or self.active_model.model_id not in provider.models:
                raise ValueError("active_model must refer to a registered provider model")
        return self


class WorkspacePreferenceDocument(ProtocolModel):
    """Current workspace Preference document, independent of Profile."""

    schema_version: Literal[WORKSPACE_PREFERENCE_SCHEMA_VERSION] = (
        WORKSPACE_PREFERENCE_SCHEMA_VERSION
    )
    revision: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=utc_now)
    state: Literal["present", "cleared"] = "present"
    entries: tuple[PreferenceEntry, ...] | None = None

    _normalize_time = field_validator("updated_at", mode="before")(_aware)

    @model_validator(mode="after")
    def payload_matches_state(self) -> WorkspacePreferenceDocument:
        if (self.state == "present") != (self.entries is not None):
            raise ValueError("workspace Preference entries must match envelope state")
        if self.entries is not None:
            ids = [entry.preference_id for entry in self.entries]
            if len(ids) != len(set(ids)):
                raise ValueError("workspace Preference IDs must be unique")
            if any(entry.scope is not PreferenceScope.WORKSPACE for entry in self.entries):
                raise ValueError("workspace Preference entries must use workspace scope")
        return self


class FrozenPreferenceSummary(ProtocolModel):
    """Small immutable summary used for a delayed Reviewer target snapshot."""

    preference_id: str
    statement: str
    status: PreferenceStatus
    scope: PreferenceScope
    entry_revision: int = Field(ge=1)
    document_revision: int = Field(ge=0)

    _valid_id = field_validator("preference_id")(
        lambda value: validate_prefixed_id(value, PREFERENCE_ID_PREFIX)
    )
    _clean_statement = field_validator("statement")(normalize_preference_statement)


class PreferenceReviewSnapshot(ProtocolModel):
    """Bounded frozen Active snapshot persisted with a Review job."""

    entries: tuple[FrozenPreferenceSummary, ...] = ()
    global_document_revision: int = Field(default=0, ge=0)
    workspace_document_revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def bounded(self) -> PreferenceReviewSnapshot:
        if len(self.entries) > PREFERENCE_MAX_ACTIVE_SNAPSHOT_ENTRIES:
            raise ValueError("Preference Review snapshot contains too many entries")
        encoded = canonical_json_bytes(self.model_dump(mode="json"))
        if len(encoded) > PREFERENCE_MAX_ACTIVE_SNAPSHOT_BYTES:
            raise ValueError("Preference Review snapshot exceeds its byte budget")
        ids = [entry.preference_id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("Preference Review snapshot IDs must be unique")
        return self

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.model_dump(mode="json"))).hexdigest()

    @property
    def serialized_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


__all__ = [
    "FrozenPreferenceSummary",
    "GlobalConfig",
    "PreferenceDocument",
    "PreferenceEntriesPayload",
    "PreferenceReviewSnapshot",
    "WorkspacePreferenceDocument",
]
