"""Fixed General/Explore/Review preset catalog: identity, role and tool policy.

The three workflow presets are system-provided ``AgentDefinitionSource`` fixtures
with stable definition IDs. Their prompts, descriptions and tool policies are
fixed by product decision D04; users only set model/generation preferences in a
separate overlay. Materialization into a workspace is an explicit, idempotent
application command (see application/agent_definitions/presets.py); reading this
    catalog never writes. The six built-in ``builtin_*`` fixtures have stable IDs
    used by the current role catalog.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from morrow.core.agent_definitions import AgentDefinitionSource, DefinitionId, ToolRequirement
from morrow.core.execution_selections import GenerationChoice
from morrow.core.models import ModelRef, ProtocolModel

AgentPresetRole = Literal["general", "explore", "review"]

PRESET_GENERAL_ID = "builtin_general"
PRESET_EXPLORE_ID = "builtin_explore"
PRESET_REVIEW_ID = "builtin_review"
PRESET_DEFINITION_IDS = (PRESET_GENERAL_ID, PRESET_EXPLORE_ID, PRESET_REVIEW_ID)

PRESET_ROLE_BY_ID = {
    PRESET_GENERAL_ID: "general",
    PRESET_EXPLORE_ID: "explore",
    PRESET_REVIEW_ID: "review",
}

_PRESET_DESCRIPTIONS = {
    "general": (
        "General-purpose agent for researching complex questions, searching for code, and"
        " executing multi-step tasks with the execution tools this task approves."
    ),
    "explore": (
        "Read-only investigator that locates code, configuration and documentation and returns"
        " citable evidence without modifying the workspace."
    ),
    "review": (
        "Independent read-only verifier that checks a result against explicit completion criteria"
        " and reports gaps and risks without modifying the workspace."
    ),
}

_GENERAL_ROLE_PROMPT = (
    "Handle the assigned task end to end: research the question, search the codebase, implement"
    " the change, and verify the result with the execution tools this task approves. Respect the"
    " workspace constraints of the current permission mode, ground every claim in collected"
    " evidence, and treat tool failures as facts to report instead of assuming success. Return a"
    " clear final result that separates what was completed from what was verified. Do not claim"
    " changes or checks that were not actually completed."
)

_EXPLORE_ROLE_PROMPT = (
    "Investigate the assigned question inside the workspace: locate the relevant code,"
    " configuration and documentation, and return citable evidence with concrete references."
    " Read-only investigation only: report findings and constraints instead of modifying"
    " anything. If a needed tool is unavailable or fails, state that limitation explicitly and"
    " continue with the evidence you can actually show."
)

_REVIEW_ROLE_PROMPT = (
    "Independently verify the supplied result against its explicit completion criteria: inspect"
    " the relevant evidence, check risks and gaps, and return a clear verification result."
    " Read-only review only: never modify the workspace under review, and never run commands"
    " whose read-only effect is not proven; executing tests belongs to execution nodes. When"
    " evidence is missing or inconclusive, report that instead of approving."
)


def _execution_tools() -> tuple[ToolRequirement, ...]:
    """The declared execution-tool superset; admission narrows it to task approvals."""

    return tuple(
        ToolRequirement(name=name, requirement="optional")
        for name in (
            "read",
            "ls",
            "find",
            "grep",
            "edit",
            "write",
            "bash",
            "promote_sandbox_changes",
        )
    )


def _read_only_tools() -> tuple[ToolRequirement, ...]:
    """Only provably read-only tools; generic bash, writes and delegation stay excluded."""

    return (
        ToolRequirement(name="read", requirement="required"),
        ToolRequirement(name="grep", requirement="optional"),
        ToolRequirement(name="ls", requirement="optional"),
        ToolRequirement(name="find", requirement="optional"),
        ToolRequirement(name="write", requirement="forbidden"),
        ToolRequirement(name="edit", requirement="forbidden"),
        ToolRequirement(name="bash", requirement="forbidden"),
        ToolRequirement(name="promote_sandbox_changes", requirement="forbidden"),
    )


def preset_source(role: AgentPresetRole) -> AgentDefinitionSource:
    """Build the fixed preset source for one role; the payload is a stable fixture."""

    prompts = {
        "general": _GENERAL_ROLE_PROMPT,
        "explore": _EXPLORE_ROLE_PROMPT,
        "review": _REVIEW_ROLE_PROMPT,
    }
    return AgentDefinitionSource(
        definition_id=f"builtin_{role}",
        name=role.capitalize(),
        description=_PRESET_DESCRIPTIONS[role],
        role_prompt=prompts[role],
        # Model selection stays inherit-by-default; the preference overlay and the
        # admission-time invoking-active resolution own any concrete model choice.
        model_selection="invoking_active",
        access_mode_ceiling="write" if role == "general" else "read",
        tool_requirements=_execution_tools() if role == "general" else _read_only_tools(),
    )


def preset_sources() -> tuple[AgentDefinitionSource, ...]:
    return tuple(preset_source(role) for role in ("general", "explore", "review"))


def is_preset_definition_id(definition_id: str) -> bool:
    return definition_id in PRESET_DEFINITION_IDS


@dataclass(frozen=True)
class LoadedPresetPreference:
    """A preference overlay entry with the YAML document revision it came from."""

    preference: AgentPresetPreference
    revision: int


class AgentPresetPreference(ProtocolModel):
    """One preset's user model/generation preference; the preset source stays fixed.

    Absent fields inherit down the D06 chain. ``generation`` accepts the typed
    three-state choice; ``inherit`` is the absent default and ``model_default``
    stops the chain at the Provider/Adapter omission behavior.
    """

    definition_id: DefinitionId
    model: ModelRef | None = None
    generation: GenerationChoice | None = None

    @model_validator(mode="after")
    def preset_only(self):
        if not is_preset_definition_id(self.definition_id):
            raise ValueError("preferences apply only to the fixed workflow presets")
        return self


class AgentPresetPreferenceDocument(ProtocolModel):
    """Workspace preset preference overlay; independent non-sensitive YAML."""

    schema_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0, strict=True)
    presets: tuple[AgentPresetPreference, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def unique_presets(self):
        ids = [item.definition_id for item in self.presets]
        if len(ids) != len(set(ids)):
            raise ValueError("preset preferences must be unique per definition")
        return self

    def preference_for(self, definition_id: str) -> AgentPresetPreference | None:
        return next((item for item in self.presets if item.definition_id == definition_id), None)
