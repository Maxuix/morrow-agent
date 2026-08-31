"""Versioned leaf definitions; no runtime, provider health or conversation ownership."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import canonical_json_bytes, refuse_secret_material, sha256_digest
from morrow.core.models import ModelRef, ProtocolModel

DefinitionId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
OpaqueId = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")]
VersionId = Annotated[str, Field(pattern=r"^adev_[A-Za-z0-9_-]+$")]
WorkspaceId = Annotated[str, Field(pattern=r"^ws_[A-Za-z0-9_-]+$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class ToolRequirement(ProtocolModel):
    name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")]
    requirement: Literal["required", "optional", "forbidden"]


class AgentDefinitionSource(ProtocolModel):
    definition_id: DefinitionId
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2048)
    role_prompt: str = Field(min_length=1, max_length=8192)
    skill_version_ids: tuple[OpaqueId, ...] = Field(default=(), max_length=64)
    tool_requirements: tuple[ToolRequirement, ...] = Field(default=(), max_length=128)
    access_mode_ceiling: Literal["read", "write"] = "read"
    max_agent_generation_requests: int | None = Field(default=None, gt=0, strict=True)
    model_selection: ModelRef | Literal["invoking_active"] = "invoking_active"

    @field_validator("name", "description", "role_prompt")
    @classmethod
    def safe_text(cls, value: str) -> str:
        value = unicodedata.normalize("NFC", value)
        if len(value.encode("utf-8")) > 8192 or any(
            unicodedata.category(c) in {"Cc", "Cf"} and c not in "\n\r\t" for c in value
        ):
            raise ValueError("definition text exceeds its bounds")
        refuse_secret_material(value, label="AgentDefinition", profile="workflow_value_sensitive")
        return value

    @field_validator("name", "role_prompt")
    @classmethod
    def nonblank_text(cls, value):
        if not value.strip():
            raise ValueError("definition text must not be blank")
        return value

    @model_validator(mode="after")
    def safe_definition(self):
        refuse_secret_material(
            canonical_json_bytes(self.model_dump(mode="json")),
            label="AgentDefinition",
            profile="workflow_value_sensitive",
        )
        return self

    @field_validator("skill_version_ids")
    @classmethod
    def ordered_skills(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("definition Skill versions must be unique")
        return tuple(sorted(value))

    @field_validator("tool_requirements")
    @classmethod
    def ordered_tools(cls, value):
        names = [item.name for item in value]
        if len(names) != len(set(names)):
            raise ValueError("conflicting or duplicate tool declarations; forbidden wins")
        return tuple(sorted(value, key=lambda item: item.name))

    @property
    def content_hash(self) -> str:
        return sha256_digest(canonical_json_bytes(self.model_dump(mode="json")))


class AgentDefinitionDocument(ProtocolModel):
    schema_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0, strict=True)
    definitions: tuple[AgentDefinitionSource, ...] = Field(default=(), max_length=128)

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_schema(cls, value):
        if type(value) is not int:
            raise ValueError("definition schema version must be an integer")
        return value

    @field_validator("definitions")
    @classmethod
    def user_definitions(cls, value):
        ids = [item.definition_id for item in value]
        if len(ids) != len(set(ids)) or any(i.startswith("builtin_") for i in ids):
            raise ValueError("definition IDs must be unique and cannot replace built-ins")
        return tuple(sorted(value, key=lambda item: item.definition_id))


class AgentDefinitionVersion(ProtocolModel):
    version_id: VersionId
    workspace_id: WorkspaceId
    version: int = Field(ge=1, strict=True)
    source: AgentDefinitionSource
    content_hash: Digest
    origin: Literal["user", "builtin"] = "user"
    source_revision: int = Field(ge=0, strict=True)
    created_at: datetime

    @model_validator(mode="after")
    def valid_hash(self):
        if self.content_hash != self.source.content_hash:
            raise ValueError("AgentDefinition version hash mismatch")
        if self.source.definition_id.startswith("builtin_") != (self.origin == "builtin"):
            raise ValueError("AgentDefinition origin mismatch")
        return self


class AgentDefinitionHead(ProtocolModel):
    workspace_id: WorkspaceId
    definition_id: DefinitionId
    version_id: VersionId
    source_revision: int = Field(ge=0, strict=True)
    source_hash: Digest
    enabled: bool = Field(default=True, strict=True)
    row_version: int = Field(ge=1, strict=True)


class AgentDefinitionRevocation(ProtocolModel):
    workspace_id: WorkspaceId
    version_id: VersionId
    reason: str = Field(min_length=1, max_length=256)
    command_id: OpaqueId
    created_at: datetime

    @field_validator("reason")
    @classmethod
    def safe_reason(cls, value):
        value = " ".join(value.split())
        refuse_secret_material(value, label="revocation reason")
        if not value:
            raise ValueError("revocation reason is required")
        return value
