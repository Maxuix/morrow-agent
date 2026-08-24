"""Bounded contracts for executing a frozen Skill script.

These models describe a request and sanitized result only.  They do not grant
filesystem, network, credential, or process authority to a Skill package.
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum

from pydantic import Field, field_validator

from morrow.core.domain import DIGEST_PATTERN, ArtifactReference, validate_prefixed_id
from morrow.core.models import ProtocolModel

from .identity import validate_skill_id, validate_skv_id
from .trust import SourceKind

SCRIPT_MAX_ARGS = 64
SCRIPT_ARG_MAX_CHARS = 4096
SCRIPT_MAX_ENV_NAMES = 16
SCRIPT_MAX_INPUT_ARTIFACTS = 16
SCRIPT_MAX_OUTPUTS = 16
SCRIPT_PATH_MAX_CHARS = 512
SCRIPT_MAX_TIMEOUT_SECONDS = 90
SCRIPT_STDIO_MAX_CHARS = 8 * 1024
SCRIPT_OUTPUT_FILE_MAX_BYTES = 1 * 1024 * 1024
SCRIPT_TOTAL_OUTPUT_BYTES = 4 * 1024 * 1024
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class SkillScriptStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


def _relative_path(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{label} must be a non-empty relative path")
    normalized = unicodedata.normalize("NFC", value)
    if (
        normalized != value
        or "\\" in value
        or value.startswith("/")
        or len(value) > SCRIPT_PATH_MAX_CHARS
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError(f"{label} is not a safe relative path")
    return value


def _script_path(value: str) -> str:
    cleaned = _relative_path(value, label="script path")
    if not cleaned.startswith("scripts/") or cleaned == "scripts/":
        raise ValueError("script path must stay under scripts/")
    return cleaned


def _argv(values: tuple[str, ...]) -> tuple[str, ...]:
    if len(values) > SCRIPT_MAX_ARGS:
        raise ValueError("Skill script has too many argv entries")
    total = 0
    for value in values:
        if not isinstance(value, str) or not value or len(value) > SCRIPT_ARG_MAX_CHARS:
            raise ValueError("Skill script argv entries are invalid")
        if "\x00" in value or "\r" in value or "\n" in value:
            raise ValueError("Skill script argv entries must be single-line and NUL-free")
        total += len(value.encode("utf-8"))
    if total > 16 * 1024:
        raise ValueError("Skill script argv exceeds its byte budget")
    return values


class SkillScriptRequest(ProtocolModel):
    """Provider-independent request for one exact selected Skill version."""

    selection_id: str
    skill_id: str
    version_id: str
    tree_digest: str
    source_kind: SourceKind = SourceKind.GENERATED
    scope_id: str | None = None
    script_path: str
    argv: tuple[str, ...] = ()
    environment_names: tuple[str, ...] = ()
    input_artifact_ids: tuple[str, ...] = ()
    output_paths: tuple[str, ...] = ()
    timeout_seconds: float = Field(default=30.0, gt=0, le=SCRIPT_MAX_TIMEOUT_SECONDS)

    @field_validator("selection_id")
    @classmethod
    def valid_selection_id(cls, value: str) -> str:
        return validate_prefixed_id(value, "ssel")

    _valid_skill = field_validator("skill_id")(validate_skill_id)
    _valid_version = field_validator("version_id")(validate_skv_id)

    @field_validator("tree_digest")
    @classmethod
    def valid_tree_digest(cls, value: str) -> str:
        if DIGEST_PATTERN.fullmatch(value) is None:
            raise ValueError("Skill script tree digest must be a SHA-256 hex digest")
        return value

    @field_validator("scope_id")
    @classmethod
    def valid_scope_id(cls, value: str | None) -> str | None:
        return None if value is None else validate_prefixed_id(value, "ws")

    _valid_script = field_validator("script_path")(_script_path)
    _valid_argv = field_validator("argv")(_argv)

    @field_validator("environment_names")
    @classmethod
    def valid_environment_names(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > SCRIPT_MAX_ENV_NAMES or len(set(values)) != len(values):
            raise ValueError("Skill script environment names are invalid")
        if any(_ENV_NAME.fullmatch(value) is None for value in values):
            raise ValueError("Skill script environment names are invalid")
        return values

    @field_validator("input_artifact_ids")
    @classmethod
    def valid_input_artifacts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > SCRIPT_MAX_INPUT_ARTIFACTS or len(set(values)) != len(values):
            raise ValueError("Skill script input Artifact references are invalid")
        return tuple(validate_prefixed_id(value, "art") for value in values)

    @field_validator("output_paths")
    @classmethod
    def valid_output_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > SCRIPT_MAX_OUTPUTS or len(set(values)) != len(values):
            raise ValueError("Skill script output paths are invalid")
        normalized = tuple(_relative_path(value, label="output path") for value in values)
        for path in normalized:
            if any(candidate.startswith(f"{path}/") for candidate in normalized):
                raise ValueError("Skill script output paths cannot overlap")
        return normalized


class SkillScriptResult(ProtocolModel):
    """Bounded, redacted result suitable for a ToolExecutor envelope."""

    selection_id: str
    skill_id: str
    version_id: str
    tree_digest: str
    script_path: str
    status: SkillScriptStatus
    exit_code: int | None = None
    stdout: str = Field(default="", max_length=SCRIPT_STDIO_MAX_CHARS)
    stderr: str = Field(default="", max_length=SCRIPT_STDIO_MAX_CHARS)
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    output_artifact_refs: tuple[ArtifactReference, ...] = ()
    produced_output_paths: tuple[str, ...] = ()
    finding_codes: tuple[str, ...] = ()
    redaction_flags: tuple[str, ...] = ()
    redaction_count: int = Field(default=0, ge=0, le=100_000)
    duration_ms: int = Field(default=0, ge=0, le=120_000)

    @field_validator("selection_id")
    @classmethod
    def valid_selection_id(cls, value: str) -> str:
        return validate_prefixed_id(value, "ssel")

    _valid_skill = field_validator("skill_id")(validate_skill_id)
    _valid_version = field_validator("version_id")(validate_skv_id)

    @field_validator("tree_digest")
    @classmethod
    def valid_tree_digest(cls, value: str) -> str:
        if DIGEST_PATTERN.fullmatch(value) is None:
            raise ValueError("Skill script tree digest must be a SHA-256 hex digest")
        return value

    _valid_script = field_validator("script_path")(_script_path)

    @field_validator("produced_output_paths")
    @classmethod
    def valid_produced_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > SCRIPT_MAX_OUTPUTS:
            raise ValueError("Skill script produced too many output paths")
        return tuple(_relative_path(value, label="produced output path") for value in values)

    @field_validator("finding_codes", "redaction_flags")
    @classmethod
    def bounded_codes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > 16 or any(not value or len(value) > 64 for value in values):
            raise ValueError("Skill script result codes are invalid")
        return tuple(dict.fromkeys(values))


__all__ = [
    "SCRIPT_ARG_MAX_CHARS",
    "SCRIPT_MAX_ARGS",
    "SCRIPT_MAX_ENV_NAMES",
    "SCRIPT_MAX_INPUT_ARTIFACTS",
    "SCRIPT_MAX_OUTPUTS",
    "SCRIPT_MAX_TIMEOUT_SECONDS",
    "SCRIPT_OUTPUT_FILE_MAX_BYTES",
    "SCRIPT_PATH_MAX_CHARS",
    "SCRIPT_STDIO_MAX_CHARS",
    "SCRIPT_TOTAL_OUTPUT_BYTES",
    "SkillScriptRequest",
    "SkillScriptResult",
    "SkillScriptStatus",
]
