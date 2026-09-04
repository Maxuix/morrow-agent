"""Frozen read-only views of existing definition, contract and capability authorities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import get_args

from morrow.application.agent_definitions.publication import validate_definition
from morrow.core.agent_definitions import AgentDefinitionVersion
from morrow.core.agent_runs import AgentDefinitionRef
from morrow.core.models import ModelRef, ProtocolModel
from morrow.core.workflows.contracts import NodeOutputKind
from morrow.core.workflows.definitions import WorkflowDefinitionSource


@dataclass(frozen=True)
class NodeCatalogEntry:
    version: AgentDefinitionVersion
    model: ModelRef
    tool_names: tuple[str, ...]

    @property
    def ref(self):
        return AgentDefinitionRef(
            definition_id=self.version.source.definition_id,
            version_id=self.version.version_id,
            content_hash=self.version.content_hash,
        )


class GraphGrammar(ProtocolModel):
    """Describes the Compiler schema, not a second executable or persisted language."""

    max_nodes: int = 16
    concurrency: int = 1
    node_kind: str = "agent"

    def wire(self):
        return self.model_dump() | {"source_schema": WorkflowDefinitionSource.model_json_schema()}


class PlanningCatalogService:
    def __init__(self, drafts):
        self.drafts = drafts

    def nodes(self) -> tuple[NodeCatalogEntry, ...]:
        drafts = self.drafts
        repo = drafts.journal.agent_definitions
        catalog = drafts.management.agent_publication.catalog
        entries = []
        # Versions remain in their sole registry; only current enabled heads enter this view.
        for version in repo.list_versions(drafts.workspace_id):
            head = repo.get_head(drafts.workspace_id, version.source.definition_id)
            if head is None or not head.enabled or head.version_id != version.version_id:
                continue
            if repo.get_revocation(drafts.workspace_id, version.version_id) is not None:
                continue
            model = version.source.model_selection
            if model == "invoking_active":
                model = drafts.management.active_model
            if model is None or not drafts.model_available(model):
                continue
            if any(not drafts.skill_available(skv) for skv in version.source.skill_version_ids):
                continue
            try:
                checked = validate_definition(version.source, catalog)
            except ValueError:
                continue
            entries.append(NodeCatalogEntry(version, model, checked.tool_names))
        return tuple(sorted(entries, key=lambda item: item.version.source.definition_id)[:128])

    def wire(self):
        catalog = self.drafts.management.agent_publication.catalog
        return {
            "nodes": [
                {
                    "agent_definition_ref": item.ref.model_dump(mode="json"),
                    "name": item.version.source.name,
                    "model": item.model.model_dump(mode="json"),
                    "skills": list(item.version.source.skill_version_ids),
                    "tools": list(item.tool_names),
                    "access_mode": item.version.source.access_mode_ceiling,
                }
                for item in self.nodes()
            ],
            "artifacts": [{"kind": kind, "version": 1} for kind in get_args(NodeOutputKind)],
            "capabilities": [
                {"tool": name, "access_mode": catalog.tool_access[name]}
                for name in sorted(catalog.allowed_tools)
                if name in catalog.tool_access
            ][:128],
            "grammar": GraphGrammar().wire(),
        }
