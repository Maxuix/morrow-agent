"""SDK-independent DTOs for bounded MCP result projections."""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import canonical_json_bytes, refuse_secret_material, require_payload_budget
from morrow.core.models import ProtocolModel

MCP_RESULT_MAX_BYTES = 128 * 1024
MCP_RESULT_MAX_TEXT_BYTES = 64 * 1024
MCP_RESULT_MAX_ITEMS = 64
MCP_RESULT_MAX_LINK_BYTES = 4096
MCP_RESULT_MAX_STRUCTURED_DEPTH = 12
_URI_PATTERN = re.compile(r"^\S{1,2048}$")


def _clean_text(value: str, *, limit: int, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    if len(value.encode("utf-8")) > limit:
        raise ValueError(f"{label} exceeds its byte budget")
    return value


class McpResourceLocator(ProtocolModel):
    """A link only; this DTO never fetches the referenced resource."""

    uri: str
    name: str = Field(min_length=1, max_length=256)
    title: str | None = Field(default=None, max_length=256)
    description: str | None = Field(default=None, max_length=1024)
    mime_type: str | None = Field(default=None, max_length=128)
    size: int | None = Field(default=None, ge=0)

    @field_validator("uri")
    @classmethod
    def valid_uri(cls, value: str) -> str:
        if not _URI_PATTERN.fullmatch(value):
            raise ValueError("MCP resource URI is invalid")
        return value

    @field_validator("name", "title", "description", "mime_type")
    @classmethod
    def bounded_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if any(ord(char) < 32 for char in value):
            raise ValueError("MCP resource metadata contains a control character")
        return value


class McpArtifactRef(ProtocolModel):
    """Reference to bytes imported into the local ArtifactStore."""

    artifact_id: str = Field(min_length=1, max_length=128)
    role: str = Field(min_length=1, max_length=64)
    mime_type: str | None = Field(default=None, max_length=128)
    byte_size: int = Field(ge=0)

    @field_validator("artifact_id", "role", "mime_type")
    @classmethod
    def safe_metadata(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if any(char in value for char in "\x00\r\n"):
            raise ValueError("MCP artifact metadata is invalid")
        return value


class McpEmbeddedResourceRef(ProtocolModel):
    """Reference for an embedded resource whose bytes are stored locally."""

    uri: str
    artifact_id: str = Field(min_length=1, max_length=128)
    mime_type: str | None = Field(default=None, max_length=128)
    byte_size: int = Field(ge=0)

    @field_validator("uri")
    @classmethod
    def valid_uri(cls, value: str) -> str:
        if not _URI_PATTERN.fullmatch(value):
            raise ValueError("MCP embedded resource URI is invalid")
        return value


def _check_structured(value: Any, *, depth: int = 0) -> None:
    if depth > MCP_RESULT_MAX_STRUCTURED_DEPTH:
        raise ValueError("MCP structured result is too deep")
    if value is None or isinstance(value, (str, bool, int, float)):
        if isinstance(value, float) and (value != value or value in {float("inf"), float("-inf")}):
            raise ValueError("MCP structured result contains a non-finite number")
        return
    if isinstance(value, list):
        if len(value) > MCP_RESULT_MAX_ITEMS:
            raise ValueError("MCP structured result has too many items")
        for item in value:
            _check_structured(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > MCP_RESULT_MAX_ITEMS:
            raise ValueError("MCP structured result has too many fields")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 256:
                raise ValueError("MCP structured result field name is invalid")
            _check_structured(item, depth=depth + 1)
        return
    raise ValueError("MCP structured result contains an unsupported value")


class McpNormalizedResult(ProtocolModel):
    """Safe projection crossing from an MCP handler into ordinary ToolExecutor."""

    is_error: bool = False
    text_parts: tuple[str, ...] = ()
    image_refs: tuple[McpArtifactRef, ...] = ()
    audio_refs: tuple[McpArtifactRef, ...] = ()
    resource_links: tuple[McpResourceLocator, ...] = ()
    embedded_resource_refs: tuple[McpEmbeddedResourceRef, ...] = ()
    structured_content: Any = None
    omitted_count: int = Field(default=0, ge=0)
    truncated: bool = False

    @field_validator("text_parts")
    @classmethod
    def bounded_text(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > MCP_RESULT_MAX_ITEMS:
            raise ValueError("MCP result has too many text parts")
        return tuple(
            _clean_text(value, limit=MCP_RESULT_MAX_TEXT_BYTES, label="MCP result text")
            for value in values
        )

    @field_validator("image_refs", "audio_refs", "resource_links", "embedded_resource_refs")
    @classmethod
    def bounded_items(cls, values: tuple[object, ...]) -> tuple[object, ...]:
        if len(values) > MCP_RESULT_MAX_ITEMS:
            raise ValueError("MCP result has too many content items")
        return values

    @field_validator("structured_content")
    @classmethod
    def bounded_structured_content(cls, value: Any) -> Any:
        if value is not None:
            _check_structured(value)
            payload = canonical_json_bytes(value)
            if len(payload) > MCP_RESULT_MAX_BYTES:
                raise ValueError("MCP structured result exceeds its byte budget")
            refuse_secret_material(payload, label="MCP structured result")
        return value

    @model_validator(mode="after")
    def bounded_payload(self) -> McpNormalizedResult:
        payload = canonical_json_bytes(self.model_dump(mode="json"))
        require_payload_budget(payload, MCP_RESULT_MAX_BYTES, label="MCP normalized result")
        return self


__all__ = [
    "MCP_RESULT_MAX_BYTES",
    "MCP_RESULT_MAX_ITEMS",
    "MCP_RESULT_MAX_LINK_BYTES",
    "MCP_RESULT_MAX_STRUCTURED_DEPTH",
    "MCP_RESULT_MAX_TEXT_BYTES",
    "McpArtifactRef",
    "McpEmbeddedResourceRef",
    "McpNormalizedResult",
    "McpResourceLocator",
]
