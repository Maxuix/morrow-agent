"""Bounded Skill catalog plus Binding status projections."""

from __future__ import annotations

import unicodedata

from morrow.adapters.state.extension_yaml import ExtensionYamlLoadStatus
from morrow.application.skills.bindings import SkillBindingService
from morrow.core.models import ProtocolModel
from morrow.core.skills.bindings import SkillBinding
from morrow.core.skills.catalog import (
    SkillAvailability,
    SkillCatalogEntry,
    SkillConflictStatus,
    SkillVersion,
)
from morrow.core.skills.trust import SourceKind, TrustLevel


class SkillQueryError(ValueError):
    """A bounded Skill query cannot resolve its requested identity."""


class SkillStatusView(ProtocolModel):
    skill_id: str
    name: str
    scope_id: str | None = None
    source_kind: SourceKind
    availability: SkillAvailability
    conflict_status: SkillConflictStatus
    effective_trust: TrustLevel
    requested_trust: TrustLevel | None = None
    versions: tuple[SkillVersion, ...] = ()
    binding: SkillBinding | None = None
    validation_errors: tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        return self.binding.enabled if self.binding else False

    @property
    def pinned_version_id(self) -> str | None:
        return self.binding.pinned_version_id if self.binding else None


class SkillRunStatusView(ProtocolModel):
    """Reference-only AgentRun Skill status; never includes full context text."""

    agent_run_id: str
    status: str
    selected_count: int = 0
    context_count: int = 0
    omitted_count: int = 0
    skill_ids: tuple[str, ...] = ()
    issue_codes: tuple[str, ...] = ()


class SkillQueries:
    """Read-only list/show/status surface; it never changes Binding or packages."""

    def __init__(self, catalog, bindings: SkillBindingService, *, journal=None) -> None:
        self.catalog = catalog
        self.bindings = bindings
        self.journal = journal

    def list(self, *, scope_id: str | None = None, limit: int = 100) -> tuple[SkillStatusView, ...]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 256:
            raise SkillQueryError("Skill query limit is invalid")
        view = self.catalog.scan_scope(scope_id)
        binding_map = self._binding_map(scope_id)
        return tuple(
            self._status(entry, binding_map.get(entry.definition.skill_id))
            for entry in view.entries[:limit]
        )

    def show(self, skill_id_or_name: str, *, scope_id: str | None = None) -> SkillStatusView:
        values = self.list(scope_id=scope_id, limit=256)
        direct = next((item for item in values if item.skill_id == skill_id_or_name), None)
        if direct is not None:
            return direct
        folded = unicodedata.normalize("NFC", skill_id_or_name).casefold()
        matches = [
            item for item in values if unicodedata.normalize("NFC", item.name).casefold() == folded
        ]
        if len(matches) != 1:
            raise SkillQueryError(
                "Skill identity is ambiguous"
                if len(matches) > 1
                else "Skill identity is unavailable"
            )
        return matches[0]

    def run_status(
        self, agent_run_id: str, *, workspace_id: str | None = None
    ) -> SkillRunStatusView:
        """Return bounded selection/context health without exposing package text."""

        if self.journal is None:
            raise SkillQueryError("Skill AgentRun status requires the operational journal")
        run = self.journal.get_agent_run(
            workspace_id or self.bindings.workspace_id or "", agent_run_id
        )
        if run is None:
            raise SkillQueryError("AgentRun is unavailable")
        workspace_id = workspace_id or self.bindings.workspace_id or ""
        selections = self.journal.list_skill_selections(workspace_id, agent_run_id)
        contexts = self.journal.list_skill_contexts(workspace_id, agent_run_id)
        issues: list[str] = []
        if len(selections) != run.snapshot.skill_selected_count:
            issues.append("selection_count_mismatch")
        if len(contexts) != len(run.snapshot.skill_context_ids):
            issues.append("context_count_mismatch")
        if run.snapshot.skill_omitted_count:
            issues.append("candidates_omitted")
        status = "ok" if not issues else "degraded"
        if run.snapshot.skill_selection_ids and not selections:
            status = "unavailable"
        return SkillRunStatusView(
            agent_run_id=agent_run_id,
            status=status,
            selected_count=len(selections),
            context_count=len(contexts),
            omitted_count=run.snapshot.skill_omitted_count,
            skill_ids=tuple(item.skill_id for item in selections),
            issue_codes=tuple(issues),
        )

    def _binding_map(self, scope_id: str | None) -> dict[str, SkillBinding]:
        scope = "global" if scope_id is None else "workspace"
        load = self.bindings.load(scope, scope_id=scope_id)
        if load.status is not ExtensionYamlLoadStatus.OK or load.value is None:
            return {}
        return {item.skill_id: item for item in load.value.bindings}

    @staticmethod
    def _status(entry: SkillCatalogEntry, binding: SkillBinding | None) -> SkillStatusView:
        return SkillStatusView(
            skill_id=entry.definition.skill_id,
            name=entry.definition.name,
            scope_id=entry.definition.scope_id,
            source_kind=entry.definition.source_kind,
            availability=entry.definition.availability,
            conflict_status=entry.definition.conflict_status,
            effective_trust=entry.effective_trust,
            requested_trust=entry.requested_trust,
            versions=entry.versions,
            binding=binding,
            validation_errors=entry.validation_errors,
        )


__all__ = ["SkillQueries", "SkillQueryError", "SkillRunStatusView", "SkillStatusView"]
