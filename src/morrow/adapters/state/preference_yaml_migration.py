"""Migration planning and publication mixin for the Preference YAML store."""

from __future__ import annotations

from pathlib import Path

from morrow.adapters.state.preference_migration import (
    GLOBAL_CONFIG_PREFERENCE_SCHEMA_VERSION,
    LEGACY_GLOBAL_SCHEMA_VERSION,
    LEGACY_WORKSPACE_PREFERENCE_SCHEMA_VERSION,
    WORKSPACE_PREFERENCE_SCHEMA_VERSION,
    PreferenceYamlDecodeError,
    decode_global_config,
    decode_workspace_preferences,
)
from morrow.adapters.state.preference_yaml_io import (
    PreferenceYamlIoError,
)
from morrow.adapters.state.preference_yaml_io import (
    raw_digest as _raw_digest,
)
from morrow.adapters.state.preference_yaml_io import (
    read_raw as _io_read_raw,
)
from morrow.adapters.state.preference_yaml_io import (
    with_revision as _with_revision,
)
from morrow.adapters.state.preference_yaml_io import (
    write_bytes as _write_bytes,
)
from morrow.adapters.state.preference_yaml_io import (
    yaml_bytes as _yaml_bytes,
)
from morrow.adapters.state.preference_yaml_types import (
    PreferenceMigrationPlan,
    PreferenceYamlConflict,
    PreferenceYamlError,
    PreferenceYamlLoad,
)


def _read_raw(path: Path) -> dict | None:
    try:
        return _io_read_raw(path)
    except PreferenceYamlIoError as exc:
        raise PreferenceYamlError(exc.code, "Preference YAML is corrupt") from exc


class PreferenceYamlMigrationMixin:
    """Prepare and publish legacy-to-generic migrations under OCC locks."""

    def prepare_global_migration(self) -> PreferenceMigrationPlan | None:
        raw = _read_raw(self.global_path)
        if raw is None or int(raw.get("schema_version", 0)) != LEGACY_GLOBAL_SCHEMA_VERSION:
            return None
        try:
            value = decode_global_config(raw)
        except PreferenceYamlDecodeError as exc:
            raise PreferenceYamlError(exc.code, "global Preference YAML is not migratable") from exc
        return PreferenceMigrationPlan(
            scope="global",
            source_revision=value.revision,
            source_schema_version=LEGACY_GLOBAL_SCHEMA_VERSION,
            source_digest=_raw_digest(raw),
            value=value,
        )

    def prepare_workspace_migration(self, workspace_id: str) -> PreferenceMigrationPlan | None:
        path = self.workspace_path(workspace_id)
        raw = _read_raw(path)
        if raw is None:
            return None
        source_schema = int(raw.get("schema_version", 0))
        if source_schema not in {1, LEGACY_WORKSPACE_PREFERENCE_SCHEMA_VERSION}:
            return None
        try:
            value = decode_workspace_preferences(raw)
        except PreferenceYamlDecodeError as exc:
            raise PreferenceYamlError(
                exc.code, "workspace Preference YAML is not migratable"
            ) from exc
        return PreferenceMigrationPlan(
            scope=workspace_id,
            source_revision=value.revision,
            source_schema_version=source_schema,
            source_digest=_raw_digest(raw),
            value=value,
        )

    def publish_migration(
        self,
        plan: PreferenceMigrationPlan,
        *,
        expected_revision: int | None = None,
    ) -> PreferenceYamlLoad:
        is_global = plan.scope == "global"
        workspace_id = None if is_global else plan.scope
        path = self.global_path if is_global else self.workspace_path(plan.scope)
        with self._lock("global" if is_global else "workspace", workspace_id):
            raw = _read_raw(path)
            current_revision = int((raw or {}).get("revision", 0) or 0)
            current_schema = int((raw or {}).get("schema_version", 0) or 0)
            expected = plan.source_revision if expected_revision is None else expected_revision
            expected_schema = (
                GLOBAL_CONFIG_PREFERENCE_SCHEMA_VERSION
                if is_global
                else WORKSPACE_PREFERENCE_SCHEMA_VERSION
            )
            if current_revision != expected:
                if current_schema == expected_schema:
                    return self._load_global(raw) if is_global else self.load_workspace(plan.scope)
                raise PreferenceYamlConflict()
            if _raw_digest(raw) != plan.source_digest:
                raise PreferenceYamlConflict()
            if self.failure_injector:
                self.failure_injector("backup")
            self._backup(path)
            value = _with_revision(plan.value, current_revision + 1)
            _write_bytes(path, _yaml_bytes(value), self.failure_injector)
            return (
                self._load_global(_read_raw(path)) if is_global else self.load_workspace(plan.scope)
            )

    def migrate_global(self) -> PreferenceYamlLoad | None:
        plan = self.prepare_global_migration()
        return None if plan is None else self.publish_migration(plan)

    def migrate_workspace(self, workspace_id: str) -> PreferenceYamlLoad | None:
        plan = self.prepare_workspace_migration(workspace_id)
        return None if plan is None else self.publish_migration(plan)


__all__ = ["PreferenceYamlMigrationMixin"]
