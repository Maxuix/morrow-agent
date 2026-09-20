"""Simplified custom Agent saving: few primary fields, lossless advanced fields.

D05: the editor exposes name, purpose, prompt, tool scope (all/custom), model
and thinking degree. The internal definition ID is derived deterministically
from the name; old Skills, request limits and derivation provenance are
preserved when an existing definition is re-saved. ``save and available`` is
one application command: desired-source validation, YAML write, publication,
enablement and an availability proof — each step idempotent so a retry after a
crash between media converges on the same exact available version (T11).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal

from morrow.core.agent_definitions import AgentDefinitionSource, ToolRequirement
from morrow.core.execution_selections import GenerationChoice
from morrow.core.models import ModelRef

# Tools whose explicit denial read-only presets declare; quick saves mirror the
# convention so nodes can always narrow these for custom agents.
_DANGEROUS_TOOLS = ("bash", "edit", "write", "promote_sandbox_changes")

# The fixed execution superset "all available" expands to (task 2.7): the
# declared union is narrowed to task-approved tools at compile/freeze and to
# provably available tools at admission.
_ALL_TOOL_NAMES = ("read", "ls", "find", "grep", "edit", "write", "bash", "promote_sandbox_changes")


@dataclass(frozen=True)
class AgentQuickSaveFields:
    """Primary editor inputs; advanced fields stay owned by their stored source."""

    name: str
    purpose: str = ""
    prompt: str = ""
    tools: Literal["all"] | tuple[str, ...] = "all"
    model: ModelRef | None = None
    generation: GenerationChoice | None = None


@dataclass(frozen=True)
class AgentQuickSaveResult:
    definition_id: str
    source: AgentDefinitionSource
    version: object
    source_revision: int
    enabled: bool
    available_version_id: str


def derive_definition_id(name: str) -> str:
    """Deterministic internal ID from the display name; never collides silently."""

    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = "".join(ch if ch.isalnum() else "-" for ch in folded.lower()).strip("-")
    slug = "-".join(part for part in slug.split("-") if part)[:63].strip("-")
    if not slug or not slug[0].isalpha():
        from morrow.core.domain import sha256_digest

        slug = f"agent-{slug}" if slug else f"agent-{sha256_digest(name.encode('utf-8'))[:8]}"
    return slug[:63]


def build_quick_save_source(
    fields: AgentQuickSaveFields,
    *,
    existing: AgentDefinitionSource | None,
    catalog,
) -> AgentDefinitionSource:
    """Build the full source from primary fields, preserving stored advanced fields."""

    definition_id = derive_definition_id(fields.name)
    if fields.tools == "all":
        names = set(_ALL_TOOL_NAMES) | set(catalog.allowed_tools)
        requirements = tuple(
            ToolRequirement(name=name, requirement="optional") for name in sorted(names)
        )
        ceiling = "write"
    else:
        selected = tuple(dict.fromkeys(fields.tools))
        if not selected:
            raise ValueError("select at least one tool or choose all available tools")
        requirements = tuple(
            ToolRequirement(name=name, requirement="required") for name in sorted(selected)
        ) + tuple(
            ToolRequirement(name=name, requirement="forbidden")
            for name in _DANGEROUS_TOOLS
            if name not in selected
        )
        ceiling = (
            "write"
            if any(catalog.tool_access.get(name) == "write" for name in selected)
            else "read"
        )
    payload = {}
    if existing is not None and existing.definition_id == definition_id:
        # Lossless preservation: Skills, request limits and derivation provenance
        # are advanced fields the simple editor never clears (D05/T11).
        payload.update(
            skill_version_ids=existing.skill_version_ids,
            max_agent_generation_requests=existing.max_agent_generation_requests,
            derived_from_version_id=existing.derived_from_version_id,
            derived_from_definition_id=existing.derived_from_definition_id,
            derived_from_source_hash=existing.derived_from_source_hash,
        )
    return AgentDefinitionSource(
        definition_id=definition_id,
        name=fields.name,
        description=fields.purpose,
        role_prompt=fields.prompt,
        tool_requirements=requirements,
        access_mode_ceiling=ceiling,
        model_selection=fields.model if fields.model is not None else "invoking_active",
        generation_selection=fields.generation,
        **payload,
    )
