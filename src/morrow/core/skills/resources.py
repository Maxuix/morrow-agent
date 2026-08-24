"""Frozen Skill resource reference/result contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.models import ProtocolModel

SKILL_RESOURCE_INLINE_MAX_BYTES = 16 * 1024
SKILL_RESOURCE_MAX_BYTES = 4 * 1024 * 1024


class SkillResourceRequest(ProtocolModel):
    """A resource request addressed only through a frozen Skill selection."""

    selection_id: str
    relative_path: str = Field(min_length=1, max_length=512)
    max_bytes: int = Field(default=SKILL_RESOURCE_MAX_BYTES, gt=0, le=SKILL_RESOURCE_MAX_BYTES)

    @field_validator("selection_id")
    @classmethod
    def bounded_selection_id(cls, value: str) -> str:
        if any(char in value for char in "\x00\r\n/\\"):
            raise ValueError("Skill selection reference is invalid")
        return value

    @field_validator("relative_path")
    @classmethod
    def relative_only(cls, value: str) -> str:
        if "\x00" in value or "\\" in value or value.startswith("/"):
            raise ValueError("Skill resource path must be relative and POSIX")
        parts = value.split("/")
        if not value or any(part in ("", ".", "..") for part in parts):
            raise ValueError("Skill resource path contains an unsafe segment")
        return value


class SkillResourceResult(ProtocolModel):
    """Bounded inline bytes or an Artifact reference for a frozen resource."""

    selection_id: str
    skill_id: str
    version_id: str
    tree_digest: str
    relative_path: str
    mime_type: str
    byte_size: int = Field(ge=0)
    sha256: str
    content: bytes | None = None
    artifact_id: str | None = None
    disposition: Literal["inline", "artifact"]

    @model_validator(mode="after")
    def exactly_one_delivery(self) -> SkillResourceResult:
        if (self.content is None) == (self.artifact_id is None):
            raise ValueError("Skill resource must have exactly one delivery form")
        if self.disposition == "inline" and self.content is None:
            raise ValueError("inline Skill resource must include content")
        if self.disposition == "artifact" and self.artifact_id is None:
            raise ValueError("Artifact Skill resource must include an artifact reference")
        return self


__all__ = [
    "SKILL_RESOURCE_INLINE_MAX_BYTES",
    "SKILL_RESOURCE_MAX_BYTES",
    "SkillResourceRequest",
    "SkillResourceResult",
]
