"""YAML-authoritative Skill bindings and Extension document contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import AliasChoices, ConfigDict, Field, field_validator, model_validator

from morrow.core.domain import canonical_json_bytes, validate_prefixed_id
from morrow.core.mcp import MCP_MAX_SERVER_BYTES, McpServerDefinition
from morrow.core.models import ProtocolModel, utc_now

from .identity import validate_skill_id, validate_skv_id
from .trust import SourceKind

EXTENSION_SCHEMA_VERSION = 1
EXTENSION_MAX_BINDINGS = 256
EXTENSION_MAX_MCP_SERVERS = 64
EXTENSION_MAX_MCP_BYTES = 16 * 1024


class SkillSelectionMode(StrEnum):
    EXPLICIT = "explicit"
    WORKSPACE_DEFAULT = "workspace_default"
    DESCRIPTION_MATCH = "description_match"


class ExtensionMcpSection(ProtocolModel):
    """Typed MCP desired-state slot; execution remains owned by later subplans."""

    servers: tuple[McpServerDefinition, ...] = ()

    @model_validator(mode="after")
    def bounded(self) -> ExtensionMcpSection:
        if len(self.servers) > EXTENSION_MAX_MCP_SERVERS:
            raise ValueError("Extension MCP section contains too many servers")
        if len(canonical_json_bytes(self.model_dump(mode="json"))) > EXTENSION_MAX_MCP_BYTES:
            raise ValueError("Extension MCP section exceeds its byte budget")
        if any(
            len(canonical_json_bytes(server.model_dump(mode="json"))) > MCP_MAX_SERVER_BYTES
            for server in self.servers
        ):
            raise ValueError("Extension MCP server exceeds its byte budget")
        ids = tuple(server.server_id for server in self.servers)
        if len(ids) != len(set(ids)):
            raise ValueError("MCP server ids must be unique within one Extension document")
        return self


class SkillBinding(ProtocolModel):
    """One desired Skill state; the Extension YAML document is its authority."""

    skill_id: str
    scope: Literal["global", "workspace"]
    scope_id: str | None = None
    source_kind: SourceKind | None = None
    enabled: bool = False
    pinned_version_id: str | None = None
    selection_mode: SkillSelectionMode = SkillSelectionMode.EXPLICIT
    revision: int = Field(default=0, ge=0)

    @field_validator("skill_id")
    @classmethod
    def valid_skill_id(cls, value: str) -> str:
        return validate_skill_id(value)

    @field_validator("pinned_version_id")
    @classmethod
    def valid_pinned_version(cls, value: str | None) -> str | None:
        return validate_skv_id(value) if value is not None else None

    @field_validator("scope_id")
    @classmethod
    def valid_scope_id(cls, value: str | None) -> str | None:
        if value is not None:
            return validate_prefixed_id(value, "ws")
        return value

    @model_validator(mode="after")
    def valid_scope(self) -> SkillBinding:
        if self.scope == "global" and self.scope_id is not None:
            raise ValueError("global Skill binding must not carry a scope_id")
        if self.scope == "workspace" and self.scope_id is None:
            raise ValueError("workspace Skill binding requires a scope_id")
        if (
            self.selection_mode is SkillSelectionMode.WORKSPACE_DEFAULT
            and self.scope != "workspace"
        ):
            raise ValueError("workspace_default bindings must be workspace-scoped")
        return self


class ExtensionDocument(ProtocolModel):
    """Common shape shared by independent global and workspace YAML documents."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_version: Literal[EXTENSION_SCHEMA_VERSION] = EXTENSION_SCHEMA_VERSION
    scope: Literal["global", "workspace"]
    scope_id: str | None = None
    revision: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=utc_now)
    bindings: tuple[SkillBinding, ...] = Field(
        default_factory=tuple,
        validation_alias=AliasChoices("bindings", "skills", "skill_bindings"),
        serialization_alias="skills",
    )
    mcp: ExtensionMcpSection = Field(default_factory=ExtensionMcpSection)

    @field_validator("scope_id")
    @classmethod
    def valid_document_scope_id(cls, value: str | None) -> str | None:
        if value is not None:
            return validate_prefixed_id(value, "ws")
        return value

    @field_validator("updated_at", mode="before")
    @classmethod
    def aware_timestamp(cls, value: datetime | str) -> datetime:
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("Extension updated_at is invalid") from exc
        if not isinstance(value, datetime):
            raise ValueError("Extension updated_at is invalid")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Extension updated_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def valid_bindings(self) -> ExtensionDocument:
        if len(self.bindings) > EXTENSION_MAX_BINDINGS:
            raise ValueError("Extension document contains too many Skill bindings")
        seen: set[str] = set()
        for binding in self.bindings:
            key = binding.skill_id
            if key in seen:
                raise ValueError("Skill bindings must be unique within one Extension document")
            seen.add(key)
            if binding.scope != self.scope or binding.scope_id != self.scope_id:
                raise ValueError("Skill binding scope must match its Extension document")
        for server in self.mcp.servers:
            if server.scope != self.scope or server.scope_id != self.scope_id:
                raise ValueError("MCP server scope must match its Extension document")
        return self

    @property
    def skill_bindings(self) -> tuple[SkillBinding, ...]:
        return self.bindings

    @property
    def skills(self) -> tuple[SkillBinding, ...]:
        return self.bindings


class GlobalExtensionDocument(ExtensionDocument):
    scope: Literal["global"] = "global"
    scope_id: None = None


class WorkspaceExtensionDocument(ExtensionDocument):
    scope: Literal["workspace"] = "workspace"
    scope_id: str

    @field_validator("scope_id")
    @classmethod
    def workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, "ws")


class SkillValidationReport(ProtocolModel):
    """Bounded facts shown before a local package is installed."""

    valid: bool
    skill_id: str | None = None
    name: str | None = None
    display_version: str | None = None
    source_kind: SourceKind = SourceKind.IMPORTED
    tree_digest: str | None = None
    file_count: int = Field(default=0, ge=0)
    total_bytes: int = Field(default=0, ge=0)
    requested_permissions: tuple[str, ...] = ()
    scripts: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @field_validator("skill_id")
    @classmethod
    def valid_skill_id(cls, value: str | None) -> str | None:
        return validate_skill_id(value) if value is not None else None

    @field_validator("display_version")
    @classmethod
    def valid_display_version(cls, value: str | None) -> str | None:
        from .identity import validate_display_version

        return validate_display_version(value)


class SkillLifecycleResult(ProtocolModel):
    """Sanitized result shared by lifecycle commands and their replays."""

    operation: str
    command_id: str
    skill_id: str
    scope: Literal["global", "workspace"]
    scope_id: str | None = None
    version_id: str | None = None
    status: Literal["applied", "replayed", "needs_resolution", "rejected"] = "applied"
    enabled: bool | None = None
    pinned_version_id: str | None = None
    message: str = ""

    @field_validator("command_id")
    @classmethod
    def valid_command_id(cls, value: str) -> str:
        return validate_prefixed_id(value, "cmd")

    @field_validator("skill_id")
    @classmethod
    def valid_skill_id(cls, value: str) -> str:
        return validate_skill_id(value)

    @field_validator("version_id", "pinned_version_id")
    @classmethod
    def valid_version_id(cls, value: str | None) -> str | None:
        return validate_skv_id(value) if value is not None else None

    @model_validator(mode="after")
    def valid_scope(self) -> SkillLifecycleResult:
        if self.scope == "global" and self.scope_id is not None:
            raise ValueError("global Skill lifecycle result must not carry a scope_id")
        if self.scope == "workspace" and self.scope_id is None:
            raise ValueError("workspace Skill lifecycle result requires a scope_id")
        if self.scope_id is not None:
            validate_prefixed_id(self.scope_id, "ws")
        return self


__all__ = [
    "EXTENSION_SCHEMA_VERSION",
    "ExtensionDocument",
    "ExtensionMcpSection",
    "GlobalExtensionDocument",
    "SkillBinding",
    "SkillLifecycleResult",
    "SkillSelectionMode",
    "SkillValidationReport",
    "WorkspaceExtensionDocument",
]
