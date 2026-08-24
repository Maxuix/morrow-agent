"""Frozen Skill selection evidence and deterministic selection-plan contracts.

These models are deliberately smaller than a Skill package.  A selection is
an immutable reference to one managed version for one AgentRun; package text
and resources live behind the dedicated context/resource services.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.models import ProtocolModel

from .bindings import SkillSelectionMode
from .context import SkillContextEntry
from .trust import SourceKind

SKILL_SELECTION_MAX_COUNT = 64
SKILL_OMISSION_MAX_COUNT = 256
SKILL_REASON_MAX_CHARS = 128


class SkillSelection(ProtocolModel):
    """One exact Skill version frozen during AgentRun admission."""

    selection_id: str
    agent_run_id: str
    skill_id: str
    version_id: str
    scope: Literal["global", "workspace"] = "global"
    scope_id: str | None = None
    source_kind: SourceKind = SourceKind.IMPORTED
    selection_mode: SkillSelectionMode = SkillSelectionMode.EXPLICIT
    activation_reason: str = Field(default="", max_length=SKILL_REASON_MAX_CHARS)
    tree_digest: str = ""

    @field_validator("selection_id", "agent_run_id", "skill_id", "version_id")
    @classmethod
    def bounded_non_empty(cls, value: str) -> str:
        if (
            not value
            or not value.strip()
            or len(value) > 256
            or any(char in value for char in "\x00\r\n")
        ):
            raise ValueError("Skill selection identity is invalid")
        return value

    @field_validator("tree_digest")
    @classmethod
    def bounded_tree_digest(cls, value: str) -> str:
        if value and (len(value) != 64 or any(char not in "0123456789abcdef" for char in value)):
            raise ValueError("Skill selection tree digest is invalid")
        return value

    @field_validator("activation_reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) > SKILL_REASON_MAX_CHARS:
            raise ValueError("Skill activation reason is too long")
        return cleaned

    @model_validator(mode="after")
    def valid_scope(self) -> SkillSelection:
        if self.scope == "global" and self.scope_id is not None:
            raise ValueError("global Skill selection must not carry scope_id")
        if self.scope == "workspace" and not self.scope_id:
            raise ValueError("workspace Skill selection requires scope_id")
        return self


class SkillOmission(ProtocolModel):
    """A deterministic, bounded reason why a candidate was not selected."""

    skill_id: str
    scope: Literal["global", "workspace"]
    scope_id: str | None = None
    reason: str = Field(min_length=1, max_length=SKILL_REASON_MAX_CHARS)
    version_id: str | None = None

    @field_validator("skill_id")
    @classmethod
    def valid_skill_id(cls, value: str) -> str:
        if not value or len(value) > 128 or any(char in value for char in "\x00\r\n"):
            raise ValueError("Skill omission identity is invalid")
        return value

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Skill omission reason must not be empty")
        return cleaned

    @model_validator(mode="after")
    def valid_scope(self) -> SkillOmission:
        if self.scope == "global" and self.scope_id is not None:
            raise ValueError("global Skill omission must not carry scope_id")
        if self.scope == "workspace" and not self.scope_id:
            raise ValueError("workspace Skill omission requires scope_id")
        return self


@dataclass(frozen=True, slots=True)
class SkillSelectionPlan:
    """Process-local admission result, ready to persist in one transaction."""

    selections: tuple[SkillSelection, ...] = ()
    omissions: tuple[SkillOmission, ...] = ()
    contexts: tuple[SkillContextEntry, ...] = ()
    binding_digest: str | None = None
    catalog_digest: str | None = None

    @property
    def selected_count(self) -> int:
        return len(self.selections)

    @property
    def omitted_count(self) -> int:
        return len(self.omissions)

    @property
    def selection_ids(self) -> tuple[str, ...]:
        return tuple(item.selection_id for item in self.selections)

    @property
    def context_ids(self) -> tuple[str, ...]:
        return tuple(item.context_id for item in self.contexts)

    @property
    def selection_digest(self) -> str | None:
        if not self.selections and not self.omissions:
            return None
        return sha256_digest(
            canonical_json_bytes(
                {
                    "selections": [item.model_dump(mode="json") for item in self.selections],
                    # Omission reasons remain a process/doctor projection; the
                    # durable digest only needs the bounded count so a closed
                    # historical run can verify itself without a second table.
                    "omitted_count": len(self.omissions),
                }
            )
        )

    @property
    def context_digest(self) -> str | None:
        if not self.contexts:
            return None
        return sha256_digest(
            canonical_json_bytes([item.model_dump(mode="json") for item in self.contexts])
        )

    def __post_init__(self) -> None:
        if len(self.selections) > SKILL_SELECTION_MAX_COUNT:
            raise ValueError("AgentRun contains too many Skill selections")
        if len(self.omissions) > SKILL_OMISSION_MAX_COUNT:
            raise ValueError("AgentRun contains too many Skill omission records")
        selection_ids = self.selection_ids
        context_ids = self.context_ids
        if len(selection_ids) != len(set(selection_ids)):
            raise ValueError("Skill selection IDs must be unique")
        if len(context_ids) != len(set(context_ids)):
            raise ValueError("Skill context IDs must be unique")


__all__ = [
    "SKILL_OMISSION_MAX_COUNT",
    "SKILL_SELECTION_MAX_COUNT",
    "SkillOmission",
    "SkillSelection",
    "SkillSelectionPlan",
]
