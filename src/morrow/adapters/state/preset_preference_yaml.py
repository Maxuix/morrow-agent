"""Workspace preset preference overlay using the bounded YAML and atomic IO owner."""

from __future__ import annotations

import yaml

from morrow.adapters.state.extension_yaml import (
    ExtensionYamlConflict,
    ExtensionYamlError,
    ExtensionYamlStore,
    _read_bytes,
)
from morrow.core.agent_presets import AgentPresetPreferenceDocument


class AgentPresetPreferenceYamlStore(ExtensionYamlStore):
    """Read-only during load; one OCC write path with backups, like definition YAML."""

    document_type = AgentPresetPreferenceDocument
    filename = "agent-preset-preferences.yaml"

    def __init__(self, root):
        super().__init__(root, create=False)

    def workspace_path(self, workspace_id):
        return super().workspace_path(workspace_id).with_name(self.filename)

    def load(self, workspace_id) -> AgentPresetPreferenceDocument:
        path = self.workspace_path(workspace_id)
        try:
            raw = _read_bytes(path)
        except FileNotFoundError:
            return self.document_type()
        try:
            return self.document_type.model_validate(yaml.safe_load(raw.decode("utf-8")))
        except (ValueError, UnicodeError, yaml.YAMLError):
            raise ExtensionYamlError(
                "invalid_preset_preferences", "Agent preset preferences are invalid"
            ) from None

    def write(
        self,
        workspace_id,
        document: AgentPresetPreferenceDocument,
        *,
        expected_revision: int,
    ) -> AgentPresetPreferenceDocument:
        path = self.workspace_path(workspace_id)
        self.locks.mkdir(parents=True, exist_ok=True)
        with self.maintenance_lock(), self._lock("workspace", workspace_id):
            current = self.load(workspace_id)
            if current.revision != expected_revision:
                raise ExtensionYamlConflict()
            candidate = self.document_type(
                revision=current.revision + 1,
                presets=document.presets,
            )
            raw = yaml.safe_dump(candidate.model_dump(mode="json"), allow_unicode=True).encode()
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                self._backup(path)
            self._publish(path, raw)
            return candidate
