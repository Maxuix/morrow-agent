"""Frozen Skill resource reads with bounded inline/Artifact delivery."""

from __future__ import annotations

import mimetypes
import unicodedata
from collections.abc import Callable

from morrow.adapters.skills.managed_store import SkillPackageError
from morrow.core.domain import sha256_digest
from morrow.core.skills.resources import (
    SKILL_RESOURCE_INLINE_MAX_BYTES,
    SKILL_RESOURCE_MAX_BYTES,
    SkillResourceRequest,
    SkillResourceResult,
)
from morrow.core.skills.selection import SkillSelection


class SkillResourceError(ValueError):
    """A frozen Skill resource cannot be delivered safely."""


class SkillResourceService:
    """Read only through a persisted selection/version/tree identity."""

    def __init__(
        self,
        package_store,
        *,
        artifact_publisher: Callable[..., object] | None = None,
        inline_max_bytes: int = SKILL_RESOURCE_INLINE_MAX_BYTES,
        max_bytes: int = SKILL_RESOURCE_MAX_BYTES,
    ) -> None:
        self.package_store = package_store
        self.artifact_publisher = artifact_publisher
        self.inline_max_bytes = max(1, min(inline_max_bytes, SKILL_RESOURCE_INLINE_MAX_BYTES))
        self.max_bytes = max(1, min(max_bytes, SKILL_RESOURCE_MAX_BYTES))

    def read(
        self,
        selection: SkillSelection,
        relative_path: str,
        *,
        max_bytes: int | None = None,
    ) -> SkillResourceResult:
        try:
            request = SkillResourceRequest(
                selection_id=selection.selection_id,
                relative_path=relative_path,
                max_bytes=self.max_bytes if max_bytes is None else max_bytes,
            )
        except ValueError as exc:
            raise SkillResourceError("Skill resource request is invalid") from exc
        if request.max_bytes > self.max_bytes:
            raise SkillResourceError("Skill resource byte limit exceeds policy")
        try:
            frozen = self.package_store.read_frozen_file(
                skill_id=selection.skill_id,
                version_id=selection.version_id,
                source_kind=selection.source_kind,
                scope_id=selection.scope_id,
                relative_path=request.relative_path,
                expected_tree_digest=selection.tree_digest,
            )
        except (SkillPackageError, OSError, ValueError) as exc:
            raise SkillResourceError("frozen Skill resource is unavailable") from exc
        content = frozen.content
        if len(content) > request.max_bytes:
            raise SkillResourceError("Skill resource exceeds the requested byte limit")
        mime_type = _mime_type(request.relative_path)
        inline = _is_text_mime(mime_type) and len(content) <= self.inline_max_bytes
        if inline:
            return SkillResourceResult(
                selection_id=selection.selection_id,
                skill_id=selection.skill_id,
                version_id=selection.version_id,
                tree_digest=selection.tree_digest,
                relative_path=request.relative_path,
                mime_type=mime_type,
                byte_size=len(content),
                sha256=sha256_digest(content),
                content=content,
                disposition="inline",
            )
        artifact_id = self._publish_artifact(content, selection, request.relative_path, mime_type)
        return SkillResourceResult(
            selection_id=selection.selection_id,
            skill_id=selection.skill_id,
            version_id=selection.version_id,
            tree_digest=selection.tree_digest,
            relative_path=request.relative_path,
            mime_type=mime_type,
            byte_size=len(content),
            sha256=sha256_digest(content),
            artifact_id=artifact_id,
            disposition="artifact",
        )

    read_resource = read

    def _publish_artifact(
        self,
        content: bytes,
        selection: SkillSelection,
        relative_path: str,
        mime_type: str,
    ) -> str:
        if self.artifact_publisher is None:
            raise SkillResourceError("Artifact delivery is unavailable for this Skill resource")
        try:
            value = self.artifact_publisher(
                content,
                selection=selection,
                relative_path=relative_path,
                mime_type=mime_type,
            )
            artifact_id = getattr(value, "artifact_id", value)
            if not isinstance(artifact_id, str) or not artifact_id:
                raise ValueError
            return artifact_id
        except Exception as exc:
            raise SkillResourceError("Skill resource Artifact publication failed") from exc


def _mime_type(relative_path: str) -> str:
    normalized = unicodedata.normalize("NFC", relative_path)
    guessed, _ = mimetypes.guess_type(normalized)
    if guessed is None:
        return "application/octet-stream"
    return guessed


def _is_text_mime(value: str) -> bool:
    return (
        value.startswith("text/")
        or value
        in {
            "application/json",
            "application/xml",
            "application/javascript",
            "application/yaml",
            "application/x-yaml",
        }
        or value.endswith("+json")
        or value.endswith("+xml")
    )


__all__ = ["SkillResourceError", "SkillResourceService"]
