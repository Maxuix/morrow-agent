"""Normalize official MCP SDK result objects into safe local DTOs."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from dataclasses import dataclass

from mcp.types import (
    AudioContent,
    BlobResourceContents,
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    TextResourceContents,
)

from morrow.core.domain import refuse_secret_material
from morrow.core.mcp import (
    MCP_RESULT_MAX_BYTES,
    MCP_RESULT_MAX_ITEMS,
    MCP_RESULT_MAX_TEXT_BYTES,
    McpArtifactRef,
    McpEmbeddedResourceRef,
    McpNormalizedResult,
    McpResourceLocator,
)

MAX_BINARY_BYTES = 8 * 1024 * 1024


class McpResultNormalizationError(ValueError):
    """Stable result error with no SDK payload or provider text."""


ArtifactPublisher = Callable[[bytes, str | None, str], McpArtifactRef]


def _bounded_text(value: str) -> tuple[str | None, bool]:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= MCP_RESULT_MAX_TEXT_BYTES:
        try:
            refuse_secret_material(encoded, label="MCP result text")
        except ValueError:
            return None, False
        return value, False
    clipped = encoded[:MCP_RESULT_MAX_TEXT_BYTES].decode("utf-8", errors="ignore")
    try:
        refuse_secret_material(clipped.encode("utf-8"), label="MCP result text")
    except ValueError:
        return None, True
    return clipped, True


@dataclass(frozen=True)
class McpResultNormalizer:
    """Convert each accepted content variant without retaining SDK objects."""

    publish_artifact: ArtifactPublisher | None = None
    max_binary_bytes: int = MAX_BINARY_BYTES

    def __post_init__(self) -> None:
        if self.max_binary_bytes < 1 or self.max_binary_bytes > 64 * 1024 * 1024:
            raise ValueError("MCP binary result budget is invalid")

    def normalize(self, result: CallToolResult) -> McpNormalizedResult:
        if not isinstance(result, CallToolResult):
            raise McpResultNormalizationError("MCP result type is invalid")
        text_parts: list[str] = []
        image_refs: list[McpArtifactRef] = []
        audio_refs: list[McpArtifactRef] = []
        resource_links: list[McpResourceLocator] = []
        embedded_refs: list[McpEmbeddedResourceRef] = []
        omitted = 0
        truncated = False

        for item in result.content[:MCP_RESULT_MAX_ITEMS]:
            if isinstance(item, TextContent):
                text, was_truncated = _bounded_text(item.text)
                if text is None:
                    omitted += 1
                else:
                    text_parts.append(text)
                truncated = truncated or was_truncated
            elif isinstance(item, (ImageContent, AudioContent)):
                ref = self._binary_ref(
                    item.data,
                    item.mime_type,
                    "image" if isinstance(item, ImageContent) else "audio",
                )
                if ref is None:
                    omitted += 1
                elif isinstance(item, ImageContent):
                    image_refs.append(ref)
                else:
                    audio_refs.append(ref)
            elif isinstance(item, ResourceLink):
                try:
                    locator = McpResourceLocator(
                        uri=item.uri,
                        name=item.name,
                        title=item.title,
                        description=item.description,
                        mime_type=item.mime_type,
                        size=item.size,
                    )
                    refuse_secret_material(
                        locator.model_dump_json().encode(), label="MCP resource link"
                    )
                except ValueError:
                    omitted += 1
                else:
                    resource_links.append(locator)
            elif isinstance(item, EmbeddedResource):
                ref = self._embedded_ref(item)
                if ref is None:
                    omitted += 1
                else:
                    embedded_refs.append(ref)
            else:
                omitted += 1
        omitted += max(0, len(result.content) - MCP_RESULT_MAX_ITEMS)

        structured = result.structured_content
        if structured is not None:
            try:
                encoded = json.dumps(
                    structured,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                if len(encoded) > MCP_RESULT_MAX_BYTES:
                    raise ValueError("structured result exceeds budget")
                refuse_secret_material(encoded, label="MCP structured result")
            except (TypeError, ValueError, OverflowError):
                structured = None
                omitted += 1

        try:
            return McpNormalizedResult(
                is_error=bool(result.is_error),
                text_parts=tuple(text_parts),
                image_refs=tuple(image_refs),
                audio_refs=tuple(audio_refs),
                resource_links=tuple(resource_links),
                embedded_resource_refs=tuple(embedded_refs),
                structured_content=structured,
                omitted_count=omitted,
                truncated=truncated,
            )
        except ValueError as exc:
            raise McpResultNormalizationError("MCP normalized result exceeded its budget") from exc

    def _binary_ref(self, encoded: str, mime_type: str | None, role: str) -> McpArtifactRef | None:
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            return None
        if len(content) > self.max_binary_bytes or self.publish_artifact is None:
            return None
        try:
            return self.publish_artifact(content, mime_type, role)
        except Exception:
            return None

    def _embedded_ref(self, item: EmbeddedResource) -> McpEmbeddedResourceRef | None:
        resource = item.resource
        if isinstance(resource, TextResourceContents):
            content, _ = _bounded_text(resource.text)
            if content is None:
                return None
            raw = content.encode("utf-8")
            mime_type = resource.mime_type
        elif isinstance(resource, BlobResourceContents):
            try:
                raw = base64.b64decode(resource.blob, validate=True)
            except (ValueError, binascii.Error):
                return None
            if len(raw) > self.max_binary_bytes:
                return None
            mime_type = resource.mime_type
        else:
            return None
        if self.publish_artifact is None:
            return None
        try:
            reference = self.publish_artifact(raw, mime_type, "embedded_resource")
            return McpEmbeddedResourceRef(
                uri=resource.uri,
                artifact_id=reference.artifact_id,
                mime_type=mime_type,
                byte_size=len(raw),
            )
        except Exception:
            return None


__all__ = ["ArtifactPublisher", "McpResultNormalizationError", "McpResultNormalizer"]
