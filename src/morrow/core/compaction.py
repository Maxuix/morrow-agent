"""Safe, immutable contracts for Pi-style context compaction.

Compaction is a model-context projection operation.  It never rewrites the
authoritative ConversationLog; the entry below records the boundary and the
bounded summary used to rebuild that projection.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import ConfigDict, Field, field_validator, model_validator

from morrow.core.domain import canonical_json_bytes, refuse_secret_material, sha256_digest
from morrow.core.models import ModelCost, ModelRef, ModelUsage, ProtocolModel, utc_now

COMPACTION_ENTRY_ID_PREFIX = "cmp"
COMPACTION_SUMMARY_MAX_CHARS = 2_048
COMPACTION_ITEM_MAX_CHARS = 512
COMPACTION_FILE_MAX_CHARS = 512
COMPACTION_MAX_ITEMS = 64
COMPACTION_MAX_FILES = 256
COMPACTION_ENTRY_MAX_BYTES = 32 * 1024
_ID_PATTERN = re.compile(r"^cmp_[A-Za-z0-9_.:-]{1,128}$")


class TokenAccountingBasis(StrEnum):
    PROVIDER_USAGE = "provider_usage"
    PI_ESTIMATOR = "pi_estimator"


class TokenAccounting(ProtocolModel):
    """Bounded context-window evidence; estimates are not billing facts."""

    basis: TokenAccountingBasis
    context_tokens: int = Field(ge=0)
    context_window_tokens: int | None = Field(default=None, gt=0)
    reserve_tokens: int = Field(gt=0)
    keep_recent_tokens: int = Field(gt=0)

    @model_validator(mode="after")
    def valid_window(self) -> TokenAccounting:
        if (
            self.context_window_tokens is not None
            and self.reserve_tokens >= self.context_window_tokens
        ):
            raise ValueError("reserve_tokens must be below context_window_tokens")
        return self

    @property
    def threshold_tokens(self) -> int | None:
        if self.context_window_tokens is None:
            return None
        return self.context_window_tokens - self.reserve_tokens

    @property
    def should_compact(self) -> bool:
        # This intentionally mirrors Pi's strict `>` boundary.
        threshold = self.threshold_tokens
        return threshold is not None and self.context_tokens > threshold


def _bounded_items(
    value: Any, *, label: str, maximum: int = COMPACTION_MAX_ITEMS
) -> tuple[str, ...]:
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, (list, tuple)):
        values = tuple(value)
    else:
        raise ValueError(f"{label} must be a string or list of strings")
    if len(values) > maximum:
        raise ValueError(f"{label} contains too many items")
    normalized: list[str] = []
    for item in values:
        if not isinstance(item, str):
            raise ValueError(f"{label} items must be strings")
        item = item.strip()
        if not item:
            continue
        if len(item) > COMPACTION_ITEM_MAX_CHARS:
            raise ValueError(f"{label} item is too long")
        refuse_secret_material(item, label=label)
        if item not in normalized:
            normalized.append(item)
    return tuple(normalized)


class CompactionSummary(ProtocolModel):
    """The structured, model-generated memory aid used by the next context."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    goal: str = Field(default="", max_length=COMPACTION_SUMMARY_MAX_CHARS)
    constraints_preferences: tuple[str, ...] = ()
    progress_done: tuple[str, ...] = ()
    progress_in_progress: tuple[str, ...] = ()
    progress_blocked: tuple[str, ...] = ()
    key_decisions: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    critical_context: tuple[str, ...] = ()
    files_read: tuple[str, ...] = ()
    files_modified: tuple[str, ...] = ()

    @field_validator(
        "constraints_preferences",
        "progress_done",
        "progress_in_progress",
        "progress_blocked",
        "key_decisions",
        "next_steps",
        "critical_context",
        mode="before",
    )
    @classmethod
    def normalize_items(cls, value, info):
        return _bounded_items(value, label=info.field_name)

    @field_validator("files_read", "files_modified", mode="before")
    @classmethod
    def normalize_files(cls, value, info):
        values = _bounded_items(value, label=info.field_name, maximum=COMPACTION_MAX_FILES)
        if any(len(item) > COMPACTION_FILE_MAX_CHARS for item in values):
            raise ValueError(f"{info.field_name} contains an overlong path")
        return values

    @field_validator("goal")
    @classmethod
    def safe_goal(cls, value: str) -> str:
        cleaned = value.strip()
        refuse_secret_material(cleaned, label="compaction goal")
        return cleaned

    @model_validator(mode="after")
    def bounded_render(self) -> CompactionSummary:
        if len(self.render().encode("utf-8")) > COMPACTION_ENTRY_MAX_BYTES:
            raise ValueError("compaction summary exceeds its durable budget")
        return self

    @classmethod
    def from_provider_text(cls, text: str) -> CompactionSummary:
        """Recover one bounded summary from common model JSON presentation mistakes."""
        if not isinstance(text, str):
            raise ValueError("compaction response must be text")
        candidate = text.strip()
        decoder = json.JSONDecoder()

        def without_trailing_commas(value: str) -> str:
            # Repair only commas immediately before a container close. This is intentionally not
            # a JSON5 parser: quotes, comments, and scalar types are never reinterpreted.
            repaired: list[str] = []
            in_string = False
            escaped = False
            for index, char in enumerate(value):
                if in_string:
                    repaired.append(char)
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                    repaired.append(char)
                    continue
                if char == ",":
                    following = index + 1
                    while following < len(value) and value[following].isspace():
                        following += 1
                    if following < len(value) and value[following] in "}]":
                        continue
                repaired.append(char)
            return "".join(repaired)

        def validate(payload: dict[str, Any]) -> CompactionSummary:
            # Provider-only normalization: durable CompactionSummary validation itself continues
            # to reject extras, so a corrupt checkpoint cannot gain the model-output tolerance.
            recognized = {key: value for key, value in payload.items() if key in cls.model_fields}
            for field_name in cls.model_fields:
                if field_name != "goal" and recognized.get(field_name, ...) is None:
                    recognized[field_name] = ()
            if recognized.get("goal", ...) is None:
                recognized["goal"] = ""
            summary = cls.model_validate(recognized, strict=True)
            if not any(summary.model_dump().values()):
                raise ValueError("compaction response contains no summary content")
            return summary

        for variant in (candidate, without_trailing_commas(candidate)):
            try:
                payload = json.loads(variant)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                return validate(payload)

        # Accept a wrapped object, but never guess between an example and the actual summary.
        # Advance past complete objects so nested dictionaries are not alternative summaries.
        for variant in (candidate, without_trailing_commas(candidate)):
            objects = []
            offset = 0
            for _ in range(64):
                start = variant.find("{", offset)
                if start < 0:
                    break
                try:
                    payload, end = decoder.raw_decode(variant, start)
                except json.JSONDecodeError:
                    offset = start + 1
                    continue
                if isinstance(payload, dict):
                    objects.append(payload)
                offset = end
            if len(objects) > 1:
                raise ValueError("compaction response contains multiple JSON objects")
            if objects:
                return validate(objects[0])
        raise ValueError("compaction response must contain one valid JSON object")

    def render(self) -> str:
        """Render Pi's stable section headings without adding a chat record."""

        def section(title: str, values: tuple[str, ...]) -> str:
            body = "\n".join(f"- {item}" for item in values) or "- None recorded"
            return f"## {title}\n{body}"

        return "\n\n".join(
            (
                section("Goal", (self.goal,) if self.goal else ()),
                section("Constraints & Preferences", self.constraints_preferences),
                section("Progress (Done)", self.progress_done),
                section("Progress (In Progress)", self.progress_in_progress),
                section("Progress (Blocked)", self.progress_blocked),
                section("Key Decisions", self.key_decisions),
                section("Next Steps", self.next_steps),
                section("Critical Context", self.critical_context),
                section("Files Read", self.files_read),
                section("Files Modified", self.files_modified),
            )
        )


class CompactionEntry(ProtocolModel):
    """Immutable compaction boundary and summary metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    entry_id: str
    session_id: str
    task_run_id: str | None = None
    agent_run_id: str | None = None
    model: ModelRef
    summary: CompactionSummary
    first_retained_sequence: int = Field(ge=1)
    source_start_sequence: int = Field(ge=1)
    source_end_sequence: int = Field(ge=1)
    tokens_before: int = Field(ge=0)
    estimated_tokens_after: int = Field(ge=0)
    accounting: TokenAccounting
    prompt_digest: str = Field(min_length=64, max_length=64)
    source_digest: str = Field(min_length=64, max_length=64)
    summary_digest: str = Field(min_length=64, max_length=64)
    instructions: str = Field(default="", max_length=512)
    read_files: tuple[str, ...] = ()
    modified_files: tuple[str, ...] = ()
    usage: ModelUsage = Field(default_factory=ModelUsage.unavailable)
    cost: ModelCost = Field(default_factory=ModelCost.unavailable)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("entry_id")
    @classmethod
    def valid_entry_id(cls, value: str) -> str:
        if not _ID_PATTERN.fullmatch(value):
            raise ValueError("compaction entry ID is invalid")
        return value

    @field_validator("prompt_digest", "source_digest", "summary_digest")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("compaction digest must be a SHA-256 hex digest")
        return value

    @field_validator("instructions")
    @classmethod
    def safe_instructions(cls, value: str) -> str:
        cleaned = value.strip()
        refuse_secret_material(cleaned, label="compaction instructions")
        return cleaned

    @field_validator("read_files", "modified_files", mode="before")
    @classmethod
    def safe_files(cls, value, info):
        return _bounded_items(value, label=info.field_name, maximum=COMPACTION_MAX_FILES)

    @model_validator(mode="after")
    def valid_boundary(self) -> CompactionEntry:
        if self.source_end_sequence >= self.first_retained_sequence:
            raise ValueError("compaction source must end before the retained boundary")
        if self.source_end_sequence < self.source_start_sequence:
            raise ValueError("compaction source range is invalid")
        expected = sha256_digest(canonical_json_bytes(self.summary.model_dump(mode="json")))
        if self.summary_digest != expected:
            raise ValueError("compaction summary digest is invalid")
        if len(canonical_json_bytes(self.model_dump(mode="json"))) > COMPACTION_ENTRY_MAX_BYTES:
            raise ValueError("compaction entry exceeds its durable budget")
        return self


__all__ = [
    "COMPACTION_ENTRY_ID_PREFIX",
    "COMPACTION_ENTRY_MAX_BYTES",
    "COMPACTION_SUMMARY_MAX_CHARS",
    "CompactionEntry",
    "CompactionSummary",
    "TokenAccounting",
    "TokenAccountingBasis",
]
