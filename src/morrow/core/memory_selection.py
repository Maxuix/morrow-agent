"""v12 durable contracts for deterministic Project Knowledge selection."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import (
    DIGEST_PATTERN,
    canonical_json_bytes,
    refuse_secret_material,
    require_payload_budget,
    validate_prefixed_id,
)
from morrow.core.learning import (
    LEARNING_KNOWLEDGE_ID_PREFIX,
    LEARNING_KNOWLEDGE_REVISION_ID_PREFIX,
    ProjectKnowledgeCategory,
    _utc,
)
from morrow.core.learning_safety import normalize_learning_text
from morrow.core.models import ProtocolModel, utc_now

MEMORY_SELECTION_ID_PREFIX = "msel"
MEMORY_SELECTION_MAX_ITEMS = 12
MEMORY_SELECTION_MAX_RENDERED_CHARS = 6_144
MEMORY_QUERY_MAX_CHARS = 4_096
MEMORY_QUERY_MAX_KEYS = 32
MEMORY_SELECTION_MAX_REASONS = 8
MEMORY_SEARCH_TERM_MAX_CHARS = 128
MEMORY_SEARCH_TERM_KINDS = frozenset({"word", "identifier", "cjk_bigram", "number", "path"})
MEMORY_WEIGHT_BANDS = frozenset({"high", "medium", "low"})

_SEMANTIC_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,31}$")
_MEMORY_QUERY_HIDDEN_CONTROLS = str.maketrans(
    "",
    "",
    "\u061c\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e"
    "\u2060\u2061\u2062\u2063\u2064\u2066\u2067\u2068\u2069\ufeff",
)


def _normalize_memory_query_goal(value: str) -> str:
    """Bound retrieval input without applying Learning-statement safety rules."""

    if not isinstance(value, str):
        raise TypeError("memory query goal must be a string")
    normalized = " ".join(value.translate(_MEMORY_QUERY_HIDDEN_CONTROLS).split())
    if not normalized:
        raise ValueError("memory query goal must not be empty")
    return normalized[:MEMORY_QUERY_MAX_CHARS]


class MemorySelectionReasonCode(StrEnum):
    EXPLICIT_KEY = "explicit_key"
    CATEGORY = "category"
    IDENTIFIER_OVERLAP = "identifier_overlap"
    COMMAND_PATH_OVERLAP = "command_path_overlap"
    LEXICAL_OVERLAP = "lexical_overlap"
    RECENT_CONFIRMATION = "recent_confirmation"
    CATEGORY_DIVERSITY = "category_diversity"
    BUDGET_OMITTED = "budget_omitted"


class MemorySearchTokenKind(StrEnum):
    WORD = "word"
    IDENTIFIER = "identifier"
    CJK_BIGRAM = "cjk_bigram"
    NUMBER = "number"
    PATH = "path"


class MemorySearchWeightBand(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class MemoryQuery(ProtocolModel):
    """Bounded, non-authoritative input to the deterministic selector."""

    workspace_id: str
    task_run_id: str | None = None
    turn_id: str | None = None
    task_goal: str = Field(min_length=1, max_length=MEMORY_QUERY_MAX_CHARS)
    requested_categories: tuple[ProjectKnowledgeCategory, ...] = ()
    explicit_semantic_keys: tuple[str, ...] = ()
    agent_role: Literal["foreground"] = "foreground"
    max_items: int = Field(default=MEMORY_SELECTION_MAX_ITEMS, ge=0, le=MEMORY_SELECTION_MAX_ITEMS)
    max_rendered_chars: int = Field(
        default=MEMORY_SELECTION_MAX_RENDERED_CHARS,
        ge=0,
        le=MEMORY_SELECTION_MAX_RENDERED_CHARS,
    )

    _valid_workspace = field_validator("workspace_id")(
        lambda value: validate_prefixed_id(value, "ws")
    )
    _valid_task = field_validator("task_run_id")(
        lambda value: None if value is None else validate_prefixed_id(value, "task")
    )
    _valid_turn = field_validator("turn_id")(
        lambda value: None if value is None else validate_prefixed_id(value, "turn")
    )
    _normalize_goal = field_validator("task_goal", mode="before")(_normalize_memory_query_goal)

    @field_validator("explicit_semantic_keys")
    @classmethod
    def valid_semantic_keys(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > MEMORY_QUERY_MAX_KEYS:
            raise ValueError("memory query contains too many explicit keys")
        if len(values) != len(set(values)):
            raise ValueError("memory query explicit keys must be unique")
        if any(not _SEMANTIC_KEY_PATTERN.fullmatch(value) for value in values):
            raise ValueError("memory query semantic key is invalid")
        return values

    @model_validator(mode="after")
    def bounded_payload(self) -> MemoryQuery:
        require_payload_budget(
            canonical_json_bytes(self.model_dump(mode="json")),
            MEMORY_QUERY_MAX_CHARS * 4,
            label="memory query",
        )
        return self


class MemorySelectionItem(ProtocolModel):
    """One immutable reference to a selected Knowledge revision."""

    selection_id: str
    workspace_id: str
    ordinal: int = Field(ge=1)
    record_kind: Literal["project_knowledge"] = "project_knowledge"
    record_id: str
    record_revision_id: str
    revision: int = Field(ge=1)
    reason_codes: tuple[MemorySelectionReasonCode, ...] = ()
    estimated_chars: int = Field(ge=0)
    rendered_content_digest: str

    _valid_selection = field_validator("selection_id")(
        lambda value: validate_prefixed_id(value, MEMORY_SELECTION_ID_PREFIX)
    )
    _valid_workspace = field_validator("workspace_id")(
        lambda value: validate_prefixed_id(value, "ws")
    )
    _valid_record = field_validator("record_id")(
        lambda value: validate_prefixed_id(value, LEARNING_KNOWLEDGE_ID_PREFIX)
    )
    _valid_revision_id = field_validator("record_revision_id")(
        lambda value: validate_prefixed_id(value, LEARNING_KNOWLEDGE_REVISION_ID_PREFIX)
    )
    _valid_digest = field_validator("rendered_content_digest")(
        lambda value: value if DIGEST_PATTERN.fullmatch(value) else _invalid_digest()
    )

    @field_validator("reason_codes")
    @classmethod
    def bounded_reasons(
        cls, values: tuple[MemorySelectionReasonCode, ...]
    ) -> tuple[MemorySelectionReasonCode, ...]:
        if len(values) > MEMORY_SELECTION_MAX_REASONS:
            raise ValueError("memory selection item has too many reason codes")
        if len(values) != len(set(values)):
            raise ValueError("memory selection item reason codes must be unique")
        return values

    @model_validator(mode="after")
    def safe_digest(self) -> MemorySelectionItem:
        refuse_secret_material(self.rendered_content_digest.encode(), label="memory selection item")
        return self


class MemorySelection(ProtocolModel):
    """Immutable selection header plus its ordered item references."""

    selection_id: str
    workspace_id: str
    query_digest: str
    source_memory_revision: int = Field(ge=0)
    selected_items: tuple[MemorySelectionItem, ...] = ()
    item_count: int = Field(ge=0, le=MEMORY_SELECTION_MAX_ITEMS)
    omitted_count: int = Field(ge=0)
    rendered_chars: int = Field(ge=0, le=MEMORY_SELECTION_MAX_RENDERED_CHARS)
    selection_digest: str
    created_at: datetime = Field(default_factory=utc_now)

    _valid_selection = field_validator("selection_id")(
        lambda value: validate_prefixed_id(value, MEMORY_SELECTION_ID_PREFIX)
    )
    _valid_workspace = field_validator("workspace_id")(
        lambda value: validate_prefixed_id(value, "ws")
    )
    _valid_digest = field_validator("query_digest", "selection_digest")(
        lambda value: value if DIGEST_PATTERN.fullmatch(value) else _invalid_digest()
    )
    _normalize_time = field_validator("created_at", mode="before")(_utc)

    @model_validator(mode="after")
    def consistent_items(self) -> MemorySelection:
        if self.item_count != len(self.selected_items):
            raise ValueError("memory selection item count does not match items")
        expected_ordinals = tuple(range(1, self.item_count + 1))
        actual_ordinals = tuple(item.ordinal for item in self.selected_items)
        if actual_ordinals != expected_ordinals:
            raise ValueError("memory selection item ordinals must be contiguous")
        if any(
            item.selection_id != self.selection_id or item.workspace_id != self.workspace_id
            for item in self.selected_items
        ):
            raise ValueError("memory selection item workspace or selection does not match")
        revision_ids = [item.record_revision_id for item in self.selected_items]
        if len(revision_ids) != len(set(revision_ids)):
            raise ValueError("memory selection cannot repeat a record revision")
        require_payload_budget(
            canonical_json_bytes(self.model_dump(mode="json")),
            32 * 1024,
            label="memory selection",
        )
        return self


class MemorySearchTerm(ProtocolModel):
    """Rebuildable lexical projection for one immutable Knowledge revision."""

    workspace_id: str
    knowledge_revision_id: str
    token_kind: MemorySearchTokenKind
    token: str = Field(min_length=1, max_length=MEMORY_SEARCH_TERM_MAX_CHARS)
    weight_band: MemorySearchWeightBand

    _valid_workspace = field_validator("workspace_id")(
        lambda value: validate_prefixed_id(value, "ws")
    )
    _valid_revision_id = field_validator("knowledge_revision_id")(
        lambda value: validate_prefixed_id(value, LEARNING_KNOWLEDGE_REVISION_ID_PREFIX)
    )
    _normalize_token = field_validator("token")(
        lambda value: normalize_learning_text(
            value, label="memory search term", maximum=MEMORY_SEARCH_TERM_MAX_CHARS
        ).casefold()
    )


def _invalid_digest() -> str:
    raise ValueError("memory selection digest must be a SHA-256 hex digest")


__all__ = [
    "MEMORY_QUERY_MAX_CHARS",
    "MEMORY_QUERY_MAX_KEYS",
    "MEMORY_SEARCH_TERM_MAX_CHARS",
    "MEMORY_SELECTION_ID_PREFIX",
    "MEMORY_SELECTION_MAX_ITEMS",
    "MEMORY_SELECTION_MAX_RENDERED_CHARS",
    "MemoryQuery",
    "MemorySearchTerm",
    "MemorySearchTokenKind",
    "MemorySearchWeightBand",
    "MemorySelection",
    "MemorySelectionItem",
    "MemorySelectionReasonCode",
]
