"""Small, availability-first project instruction loader."""

from __future__ import annotations

import unicodedata
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from morrow.core.domain import sha256_digest
from morrow.core.prompt import (
    PROMPT_MAX_PROJECT_SOURCE_BYTES,
    PROMPT_MAX_PROJECT_SOURCES,
    ProjectInstructionContent,
    ProjectInstructionSourceRef,
    project_source_metadata_digest,
    project_source_selection_digest,
)

PROJECT_INSTRUCTION_SOURCE_VERSION = "v1"
PROJECT_INSTRUCTION_RESOLVER_VERSION = "v1"
DEFAULT_PROJECT_INSTRUCTION_FILENAMES = (
    "AGENTS.override.md",
    "AGENTS.md",
    "CLAUDE.md",
)
PROJECT_INSTRUCTION_MAX_TOTAL_BYTES = 64 * 1024


class ProjectInstructionError(ValueError):
    """Stable error for an unavailable workspace root."""

    def __init__(self, code: str, *, path: str | None = None, scope: str | None = None) -> None:
        self.code = code
        self.path = path
        self.scope = scope
        super().__init__(code)

    @property
    def diagnostic(self) -> dict[str, str]:
        value = {"code": self.code}
        if self.path is not None:
            value["path"] = self.path
        if self.scope is not None:
            value["scope"] = self.scope
        return value


@dataclass(frozen=True, slots=True)
class ProjectInstructionResolution:
    """Instruction bodies used by one in-process prompt projection."""

    sources: tuple[ProjectInstructionContent, ...]
    resolver_version: str = PROJECT_INSTRUCTION_RESOLVER_VERSION
    selection_digest: str = field(default_factory=lambda: project_source_selection_digest([]))

    def __post_init__(self) -> None:
        if self.selection_digest != project_source_selection_digest(list(self.references)):
            raise ValueError("project instruction selection digest is invalid")

    @property
    def references(self) -> tuple[ProjectInstructionSourceRef, ...]:
        return tuple(item.reference for item in self.sources)

    @property
    def rendered_block(self) -> str:
        return render_project_instruction_block(self.sources)


class ProjectInstructionResolver:
    """Load one root context file once; task text never changes discovery."""

    def __init__(
        self,
        workspace_root: Path,
        *,
        filenames: Sequence[str] = DEFAULT_PROJECT_INSTRUCTION_FILENAMES,
        max_sources: int = PROMPT_MAX_PROJECT_SOURCES,
        max_file_bytes: int = PROMPT_MAX_PROJECT_SOURCE_BYTES,
        max_total_bytes: int = PROJECT_INSTRUCTION_MAX_TOTAL_BYTES,
    ) -> None:
        try:
            self.root = Path(workspace_root).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ProjectInstructionError("workspace_unavailable") from exc
        if not self.root.is_dir():
            raise ProjectInstructionError("workspace_unavailable")
        self.filenames = _unique_names(filenames)
        self.max_sources = max_sources
        self.max_file_bytes = min(max_file_bytes, PROMPT_MAX_PROJECT_SOURCE_BYTES)
        self.max_total_bytes = max_total_bytes

    @property
    def version(self) -> str:
        return PROJECT_INSTRUCTION_RESOLVER_VERSION

    def resolve(self) -> ProjectInstructionResolution:
        source = self._load_root_source()
        sources = () if source is None else (source,)
        return ProjectInstructionResolution(
            sources=sources,
            resolver_version=self.version,
            selection_digest=project_source_selection_digest([item.reference for item in sources]),
        )

    def rehydrate(
        self,
        references: Sequence[ProjectInstructionSourceRef] | object,
        *,
        expected_selection_digest: str | None = None,
        expected_resolver_version: str | None = None,
    ) -> ProjectInstructionResolution:
        """Reload current root context; stale instruction metadata never blocks recovery."""

        del references, expected_selection_digest, expected_resolver_version
        return self.resolve()

    def _load_root_source(self) -> ProjectInstructionContent | None:
        for filename in self.filenames:
            candidate = self.root / filename
            try:
                if not candidate.exists():
                    continue
                if not candidate.is_file():
                    _warn_skip(filename, "not a regular file")
                    continue
                raw = candidate.read_bytes()
            except OSError:
                _warn_skip(filename, "could not be read")
                continue
            if len(raw) > self.max_file_bytes:
                _warn_skip(filename, "exceeds the prompt context limit")
                continue
            try:
                text = raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                _warn_skip(filename, "is not UTF-8")
                continue
            if any(
                unicodedata.category(char) in {"Cc", "Cf"} and char not in {"\t", "\n", "\r"}
                for char in text
            ):
                _warn_skip(filename, "contains unsupported control text")
                continue
            text = unicodedata.normalize("NFC", text)
            normalized = text.encode("utf-8")
            metadata = {
                "source_id": filename,
                "version": PROJECT_INSTRUCTION_SOURCE_VERSION,
                "path": filename,
                "scope": ".",
                "content_sha256": sha256_digest(normalized),
                "byte_count": len(normalized),
            }
            reference = ProjectInstructionSourceRef(
                **metadata,
                digest=project_source_metadata_digest(metadata),
            )
            return ProjectInstructionContent(reference=reference, text=text)
        return None


def project_instruction_selection_digest(
    references: Sequence[ProjectInstructionSourceRef],
) -> str:
    return project_source_selection_digest(list(references))


def render_project_instruction_block(
    sources: Sequence[ProjectInstructionContent],
) -> str:
    blocks = [
        f"[scope={item.reference.scope}; source={item.reference.path}]\n{item.text}"
        for item in sources
    ]
    return "以下是当前工作目录的项目指令：\n" + "\n".join(blocks)


def _unique_names(values: Sequence[str]) -> tuple[str, ...]:
    names: list[str] = []
    for value in values:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 64
            or "/" in value
            or "\\" in value
            or "\x00" in value
        ):
            raise ValueError("project instruction filename is invalid")
        if value not in names:
            names.append(value)
    if not names:
        raise ValueError("at least one project instruction filename is required")
    return tuple(names)


def _warn_skip(filename: str, reason: str) -> None:
    warnings.warn(
        f"Skipping project instruction {filename}: {reason}",
        RuntimeWarning,
        stacklevel=3,
    )


__all__ = [
    "DEFAULT_PROJECT_INSTRUCTION_FILENAMES",
    "PROJECT_INSTRUCTION_MAX_TOTAL_BYTES",
    "PROJECT_INSTRUCTION_RESOLVER_VERSION",
    "PROJECT_INSTRUCTION_SOURCE_VERSION",
    "ProjectInstructionError",
    "ProjectInstructionResolution",
    "ProjectInstructionResolver",
    "project_instruction_selection_digest",
    "render_project_instruction_block",
]
