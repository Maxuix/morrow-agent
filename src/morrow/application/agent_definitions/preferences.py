"""Preset model/generation preference overlay: read, OCC write and selection view.

D04: preset identity, prompts and tool policy stay fixed; users only set model
and thinking-degree preferences in this independent non-sensitive YAML overlay.
Reads never write. Explicit choices are validated against the exact Model
capabilities so an unsupported effort is rejected at save time instead of at
task start (T13 pre-exposure for the preference layer).
"""

from __future__ import annotations

from morrow.adapters.state.extension_yaml import ExtensionYamlConflict
from morrow.adapters.state.preset_preference_yaml import AgentPresetPreferenceYamlStore
from morrow.core.agent_definitions import AgentDefinitionSource
from morrow.core.agent_presets import AgentPresetPreference, AgentPresetPreferenceDocument
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.execution_selections import (
    ResolvedSelection,
    SelectionInputs,
    resolve_selection,
)
from morrow.core.models import ModelRef, ReasoningEffort


class AgentPresetPreferenceService:
    def __init__(self, store: AgentPresetPreferenceYamlStore, *, workspace_id: str, validator):
        """``validator(model)`` returns the supported efforts for one exact Model
        or raises ``ValueError`` when the Model is unavailable."""
        self.store = store
        self.workspace_id = workspace_id
        self.validator = validator

    def document(self) -> AgentPresetPreferenceDocument:
        return self.store.load(self.workspace_id)

    def preference_for(self, definition_id: str) -> AgentPresetPreference | None:
        return self.document().preference_for(definition_id)

    def set(
        self,
        definition_id: str,
        preference: AgentPresetPreference,
        *,
        expected_revision: int,
    ) -> AgentPresetPreferenceDocument:
        if preference.definition_id != definition_id:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "preference path and body identities differ"
            )
        self._validate(preference)
        current = self.document()
        document = AgentPresetPreferenceDocument(
            presets=(
                *(item for item in current.presets if item.definition_id != definition_id),
                preference,
            )
        )
        return self._write(document, expected_revision)

    def clear(self, definition_id: str, *, expected_revision: int) -> AgentPresetPreferenceDocument:
        current = self.document()
        if current.preference_for(definition_id) is None:
            return current
        document = AgentPresetPreferenceDocument(
            presets=tuple(item for item in current.presets if item.definition_id != definition_id)
        )
        return self._write(document, expected_revision)

    def resolve_for_source(
        self,
        source: AgentDefinitionSource,
        *,
        session_model: ModelRef | None = None,
        session_generation=None,
        adapter_default_model: ModelRef | None = None,
    ) -> ResolvedSelection:
        """Resolve the D06 chain for one definition with its preference overlay.

        Preset sources carry no explicit selection of their own, so their
        preference overlay provides the agent-level choice; custom definitions
        provide it directly through ``model_selection``/``generation_selection``.
        """
        preference = self.preference_for(source.definition_id)
        agent_model: ModelRef | str | None = source.model_selection
        if (
            not isinstance(agent_model, ModelRef)
            and preference is not None
            and preference.model is not None
        ):
            agent_model = preference.model
        agent_generation = source.generation_selection
        if agent_generation is None and preference is not None:
            agent_generation = preference.generation
        efforts: tuple[ReasoningEffort, ...] | None = None
        resolved_model = agent_model if isinstance(agent_model, ModelRef) else session_model
        if resolved_model is not None:
            efforts = self.validator(resolved_model)
        return resolve_selection(
            SelectionInputs(
                agent_model=agent_model,
                agent_generation=agent_generation,
                session_model=session_model,
                session_generation=session_generation,
                adapter_default_model=adapter_default_model,
            ),
            exact_reasoning_efforts=efforts,
        )

    def _write(self, document, expected_revision) -> AgentPresetPreferenceDocument:
        try:
            return self.store.write(
                self.workspace_id, document, expected_revision=expected_revision
            )
        except ExtensionYamlConflict:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT, "preset preferences changed, refresh and retry"
            ) from None

    def _validate(self, preference: AgentPresetPreference) -> None:
        if preference.model is None:
            # Model and generation resolve independently (D06). Without an exact
            # model there is no capability set to validate an explicit effort
            # against at save time; the task-start resolver rejects an
            # unsupported effort loudly instead of silently dropping it.
            return
        efforts = self.validator(preference.model)
        generation = preference.generation
        effort = (
            generation.value if generation is not None and generation.mode == "explicit" else None
        )
        if effort is not None:
            if effort not in efforts:
                raise ApplicationError(ApplicationErrorCode.INVALID, "当前模型不支持所选思考参数")
