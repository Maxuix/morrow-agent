"""Reference-only prompt evidence and in-process prompt projections.

The durable models in this module intentionally contain metadata only.  Prompt
and project-instruction bodies live in the short-lived projection dataclasses;
they are never part of an AgentRun snapshot or an operational event.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from re import Pattern

from pydantic import Field, field_validator, model_validator

from morrow.core.models import ProtocolModel

PROMPT_DIGEST_PATTERN = re.compile(r"^[a-f0-9]{64}$")
PROMPT_TOKEN_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")
PROMPT_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
PROMPT_RELATIVE_PATH_PATTERN = re.compile(r"^[^/\\\x00]+(?:/[^/\\\x00]+)*$")

PROMPT_MAX_PROJECT_SOURCES = 32
PROMPT_MAX_PROJECT_SOURCE_BYTES = 16 * 1024
PROMPT_MAX_PROJECT_TOTAL_BYTES = 64 * 1024


def _clean_token(value: str, *, label: str, pattern: Pattern[str], maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or not pattern.fullmatch(value)
    ):
        raise ValueError(f"{label} is invalid")
    return value


def _clean_relative(value: str, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or not PROMPT_RELATIVE_PATH_PATTERN.fullmatch(value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or unicodedata.normalize("NFC", value) != value
        or any(unicodedata.category(char) in {"Cc", "Cf"} for char in value)
    ):
        raise ValueError(f"{label} must be a normalized workspace-relative path")
    return value


def project_source_selection_digest(
    references: tuple[ProjectInstructionSourceRef, ...] | list[ProjectInstructionSourceRef],
) -> str:
    """Hash the ordered, metadata-only source list used by durable evidence."""
    payload = json.dumps(
        [reference.model_dump(mode="json") for reference in references],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def project_source_reference_digest(reference: ProjectInstructionSourceRef) -> str:
    """Hash one source's canonical metadata, excluding its body."""
    return project_source_metadata_digest(
        {
            "source_id": reference.source_id,
            "version": reference.version,
            "path": reference.path,
            "scope": reference.scope,
            "content_sha256": reference.content_sha256,
            "byte_count": reference.byte_count,
        }
    )


def project_source_metadata_digest(metadata: dict[str, object]) -> str:
    """Hash a source metadata mapping without ever requiring source content."""
    normalized = {
        key: metadata[key]
        for key in (
            "source_id",
            "version",
            "path",
            "scope",
            "content_sha256",
            "byte_count",
        )
    }
    payload = json.dumps(
        normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class ProjectInstructionSourceRef(ProtocolModel):
    """One hash-frozen, workspace-relative project-instruction source."""

    source_id: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=32)
    path: str = Field(min_length=1, max_length=512)
    scope: str = Field(min_length=1, max_length=512)
    content_sha256: str
    byte_count: int = Field(ge=0, le=PROMPT_MAX_PROJECT_SOURCE_BYTES)
    digest: str

    @field_validator("source_id")
    @classmethod
    def valid_source_id(cls, value: str) -> str:
        return _clean_token(
            value, label="project instruction source ID", pattern=PROMPT_TOKEN_PATTERN, maximum=64
        )

    @field_validator("version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        return _clean_token(
            value, label="project instruction version", pattern=PROMPT_VERSION_PATTERN, maximum=32
        )

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        return _clean_relative(value, label="project instruction path")

    @field_validator("scope")
    @classmethod
    def valid_scope(cls, value: str) -> str:
        if value == ".":
            return value
        return _clean_relative(value, label="project instruction scope")

    @field_validator("content_sha256", "digest")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        if not isinstance(value, str) or not PROMPT_DIGEST_PATTERN.fullmatch(value):
            raise ValueError("project instruction digest must be a SHA-256 hex digest")
        return value

    @model_validator(mode="after")
    def valid_metadata_digest(self) -> ProjectInstructionSourceRef:
        expected_path = self.source_id if self.scope == "." else f"{self.scope}/{self.source_id}"
        if self.path != expected_path:
            raise ValueError("project instruction path does not match its scope")
        if project_source_reference_digest(self) != self.digest:
            raise ValueError("project instruction metadata digest is invalid")
        return self


class PromptProfileEvidence(ProtocolModel):
    """Reference-only prompt profile and project-source evidence."""

    profile_id: str = Field(min_length=1, max_length=64)
    profile_version: str = Field(min_length=1, max_length=32)
    profile_digest: str
    role_prompt_digest: str | None = None
    project_instruction_resolver_version: str | None = None
    project_instruction_sources: tuple[ProjectInstructionSourceRef, ...] = ()
    project_instruction_selection_digest: str | None = None

    @field_validator("profile_id")
    @classmethod
    def valid_profile_id(cls, value: str) -> str:
        return _clean_token(
            value, label="prompt profile ID", pattern=PROMPT_TOKEN_PATTERN, maximum=64
        )

    @field_validator("profile_version", "project_instruction_resolver_version")
    @classmethod
    def valid_version_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean_token(
            value, label="prompt version", pattern=PROMPT_VERSION_PATTERN, maximum=32
        )

    @field_validator("profile_digest", "role_prompt_digest", "project_instruction_selection_digest")
    @classmethod
    def valid_digest_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not PROMPT_DIGEST_PATTERN.fullmatch(value):
            raise ValueError("prompt evidence digest must be a SHA-256 hex digest")
        return value

    @field_validator("project_instruction_sources")
    @classmethod
    def bounded_sources(
        cls, value: tuple[ProjectInstructionSourceRef, ...]
    ) -> tuple[ProjectInstructionSourceRef, ...]:
        if len(value) > PROMPT_MAX_PROJECT_SOURCES:
            raise ValueError("prompt evidence contains too many project sources")
        keys = [(item.path, item.scope) for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("prompt evidence project sources must be unique")
        if sum(item.byte_count for item in value) > PROMPT_MAX_PROJECT_TOTAL_BYTES:
            raise ValueError("prompt evidence project sources exceed the byte budget")
        order = [
            (0 if item.scope == "." else len(item.scope.split("/")), item.path) for item in value
        ]
        if order != sorted(order):
            raise ValueError("prompt evidence project sources are not in scope order")
        return value

    @model_validator(mode="after")
    def complete_project_evidence(self) -> PromptProfileEvidence:
        if self.project_instruction_sources and (
            self.project_instruction_resolver_version is None
            or self.project_instruction_selection_digest is None
        ):
            raise ValueError("project instruction evidence is incomplete")
        if self.project_instruction_resolver_version is not None and (
            self.project_instruction_selection_digest is None
        ):
            raise ValueError("project instruction selection digest is missing")
        if self.project_instruction_resolver_version is None and (
            self.project_instruction_sources
            or self.project_instruction_selection_digest is not None
        ):
            raise ValueError("project instruction resolver evidence is incomplete")
        if self.project_instruction_resolver_version is not None and (
            self.project_instruction_selection_digest
            != project_source_selection_digest(list(self.project_instruction_sources))
        ):
            raise ValueError("project instruction selection digest is invalid")
        return self


@dataclass(frozen=True, slots=True)
class ProjectInstructionContent:
    """Verified source text paired with reference-only metadata in memory."""

    reference: ProjectInstructionSourceRef
    text: str

    @property
    def source(self) -> ProjectInstructionSourceRef:
        """Compatibility name for callers that call sources ``source``."""
        return self.reference


@dataclass(frozen=True, slots=True)
class PromptProjection:
    """The exact prompt bodies used for one in-process AgentRun projection."""

    evidence: PromptProfileEvidence
    role_prompt: str | None = None
    project_instructions: tuple[ProjectInstructionContent, ...] = ()
    # Opaque process-local provenance; never serialized or used as durable evidence.
    provenance: object | None = None

    @property
    def project_sources(self) -> tuple[ProjectInstructionContent, ...]:
        return self.project_instructions

    @property
    def project_instruction_sources(self) -> tuple[ProjectInstructionSourceRef, ...]:
        return tuple(item.reference for item in self.project_instructions)

    @property
    def project_instruction_block(self) -> str:
        return "\n".join(item.text for item in self.project_instructions)


# Names used by the application layer and future AgentDefinition composition.
DirectCodingPromptProjection = PromptProjection
PromptEvidence = PromptProfileEvidence


__all__ = [
    "DirectCodingPromptProjection",
    "PROMPT_DIGEST_PATTERN",
    "PROMPT_MAX_PROJECT_SOURCE_BYTES",
    "PROMPT_MAX_PROJECT_SOURCES",
    "PROMPT_MAX_PROJECT_TOTAL_BYTES",
    "ProjectInstructionContent",
    "ProjectInstructionSourceRef",
    "project_source_metadata_digest",
    "project_source_reference_digest",
    "project_source_selection_digest",
    "PromptEvidence",
    "PromptProfileEvidence",
    "PromptProjection",
]
