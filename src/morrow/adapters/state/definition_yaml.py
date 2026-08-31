"""Workspace desired definitions using the existing bounded YAML and atomic IO owner."""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from morrow.adapters.state.extension_yaml import (
    ExtensionYamlConflict,
    ExtensionYamlError,
    ExtensionYamlStore,
    _read_bytes,
)
from morrow.core.agent_definitions import AgentDefinitionDocument, AgentDefinitionSource
from morrow.core.workflows.definitions import WorkflowDefinitionDocument, WorkflowDefinitionSource


@dataclass(frozen=True)
class LoadedAgentDefinition:
    source: AgentDefinitionSource | WorkflowDefinitionSource
    source_revision: int

    @property
    def source_hash(self):
        return self.source.content_hash


class AgentDefinitionYamlStore(ExtensionYamlStore):
    """No initialization or writes during load/validate; failures stay definition-local."""

    document_type = AgentDefinitionDocument
    filename = "agent-definitions.yaml"
    identity_field = "definition_id"

    def __init__(self, root):
        super().__init__(root, create=False)

    def workspace_path(self, workspace_id):
        return super().workspace_path(workspace_id).with_name(self.filename)

    def load(self, workspace_id) -> AgentDefinitionDocument:
        path = self.workspace_path(workspace_id)
        try:
            raw = _read_bytes(path)
        except FileNotFoundError:
            return self.document_type()
        try:
            return self.document_type.model_validate(yaml.safe_load(raw.decode("utf-8")))
        except (ValueError, UnicodeError, yaml.YAMLError):
            raise ExtensionYamlError(
                "invalid_definition_source", "Desired definitions are invalid"
            ) from None

    def load_definition(self, workspace_id, definition_id) -> LoadedAgentDefinition:
        document = self.load(workspace_id)
        for source in document.definitions:
            if getattr(source, self.identity_field) == definition_id:
                return LoadedAgentDefinition(source, document.revision)
        raise ExtensionYamlError("definition_missing", "Agent definition is missing")

    def write(self, workspace_id, document: AgentDefinitionDocument, *, expected_revision: int):
        path = self.workspace_path(workspace_id)
        self.locks.mkdir(parents=True, exist_ok=True)
        with self.maintenance_lock(), self._lock("workspace", workspace_id):
            current = self.load(workspace_id)
            if current.revision != expected_revision:
                raise ExtensionYamlConflict()
            candidate = self.document_type(
                revision=current.revision + 1,
                definitions=document.definitions,
            )
            raw = yaml.safe_dump(candidate.model_dump(mode="json"), allow_unicode=True).encode()
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                self._backup(path)
            self._publish(path, raw)
            return candidate


class WorkflowDefinitionYamlStore(AgentDefinitionYamlStore):
    document_type = WorkflowDefinitionDocument
    filename = "workflow-definitions.yaml"
    identity_field = "workflow_definition_id"
