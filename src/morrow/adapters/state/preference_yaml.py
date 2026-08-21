"""YAML authority and migration codec for generic Preferences."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from filelock import FileLock

from morrow.adapters.state.preference_migration import (
    GLOBAL_CONFIG_PREFERENCE_SCHEMA_VERSION,
    LEGACY_GLOBAL_SCHEMA_VERSION,
    LEGACY_WORKSPACE_PREFERENCE_SCHEMA_VERSION,
    WORKSPACE_PREFERENCE_SCHEMA_VERSION,
    PreferenceYamlDecodeError,
    decode_global_config,
    decode_legacy_agent_run_preferences,
    decode_workspace_preferences,
)
from morrow.adapters.state.preference_yaml_io import (
    PreferenceYamlIoError,
)
from morrow.adapters.state.preference_yaml_io import (
    backup as _io_backup,
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
from morrow.adapters.state.preference_yaml_migration import PreferenceYamlMigrationMixin
from morrow.adapters.state.preference_yaml_types import (
    PreferenceMigrationPlan,
    PreferenceYamlConflict,
    PreferenceYamlError,
    PreferenceYamlLoad,
    PreferenceYamlLoadStatus,
)
from morrow.core.preference_documents import GlobalConfigV2, WorkspacePreferenceDocumentV3


def _read_raw(path: Path) -> dict | None:
    try:
        return _io_read_raw(path)
    except PreferenceYamlIoError as exc:
        raise PreferenceYamlError(exc.code, "Preference YAML is corrupt") from exc


def _schema_version(raw: dict | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw.get("schema_version", 0))
    except (TypeError, ValueError) as exc:
        raise PreferenceYamlError("corrupt", "Preference YAML schema is invalid") from exc


class PreferenceYamlStore(PreferenceYamlMigrationMixin):
    """Read, prepare, and explicitly publish generic Preference YAML."""

    def __init__(
        self,
        root: Path,
        *,
        failure_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.locks = root / "locks"
        self.locks.mkdir(parents=True, exist_ok=True)
        self.failure_injector = failure_injector

    @property
    def global_path(self) -> Path:
        return self.root / "config.yaml"

    def workspace_path(self, workspace_id: str) -> Path:
        if not workspace_id.startswith("ws_") or "/" in workspace_id or "\\" in workspace_id:
            raise ValueError("invalid workspace_id")
        return self.root / "workspaces" / workspace_id / "preferences.yaml"

    def _lock(self, scope: str, workspace_id: str | None = None) -> FileLock:
        name = (
            "config-preferences.lock" if scope == "global" else f"{workspace_id}-preferences.lock"
        )
        return FileLock(str(self.locks / name), timeout=5)

    def load_global(self) -> PreferenceYamlLoad:
        try:
            raw = _read_raw(self.global_path)
        except PreferenceYamlError as exc:
            return PreferenceYamlLoad(
                PreferenceYamlLoadStatus.CORRUPT, None, 0, None, error=exc.code
            )
        return self._load_global(raw)

    def _load_global(self, raw: dict | None) -> PreferenceYamlLoad:
        if raw is None:
            value = GlobalConfigV2()
            return PreferenceYamlLoad(
                PreferenceYamlLoadStatus.OK, value, 0, GLOBAL_CONFIG_PREFERENCE_SCHEMA_VERSION
            )
        schema = _schema_version(raw)
        try:
            value = decode_global_config(raw)
        except PreferenceYamlDecodeError as exc:
            status = (
                PreferenceYamlLoadStatus.UNSUPPORTED_SCHEMA
                if exc.code.startswith("future_")
                else PreferenceYamlLoadStatus.CORRUPT
            )
            return PreferenceYamlLoad(
                status,
                None,
                int(raw.get("revision", 0) or 0),
                schema,
                error=exc.code,
            )
        return PreferenceYamlLoad(
            PreferenceYamlLoadStatus.OK,
            value,
            value.revision,
            schema,
            migrated=schema == LEGACY_GLOBAL_SCHEMA_VERSION,
            presence="present",
        )

    def load_workspace(self, workspace_id: str) -> PreferenceYamlLoad:
        path = self.workspace_path(workspace_id)
        try:
            raw = _read_raw(path)
        except PreferenceYamlError as exc:
            return PreferenceYamlLoad(
                PreferenceYamlLoadStatus.CORRUPT, None, 0, None, error=exc.code
            )
        if raw is None:
            return PreferenceYamlLoad(
                PreferenceYamlLoadStatus.OK,
                WorkspacePreferenceDocumentV3(state="cleared", entries=None),
                0,
                WORKSPACE_PREFERENCE_SCHEMA_VERSION,
                presence="missing",
            )
        schema = _schema_version(raw)
        try:
            value = decode_workspace_preferences(raw)
        except PreferenceYamlDecodeError as exc:
            status = (
                PreferenceYamlLoadStatus.UNSUPPORTED_SCHEMA
                if exc.code.startswith("future_")
                else PreferenceYamlLoadStatus.CORRUPT
            )
            return PreferenceYamlLoad(
                status,
                None,
                int(raw.get("revision", 0) or 0),
                schema,
                error=exc.code,
            )
        return PreferenceYamlLoad(
            PreferenceYamlLoadStatus.OK,
            value,
            value.revision,
            schema,
            migrated=schema in {1, LEGACY_WORKSPACE_PREFERENCE_SCHEMA_VERSION},
            presence=value.state,
        )

    def write_global(
        self, value: GlobalConfigV2, *, expected_revision: int | None = None
    ) -> PreferenceYamlLoad:
        return self._write_value(value, self.global_path, "global", None, expected_revision)

    def write_workspace(
        self,
        workspace_id: str,
        value: WorkspacePreferenceDocumentV3,
        *,
        expected_revision: int | None = None,
    ) -> PreferenceYamlLoad:
        return self._write_value(
            value, self.workspace_path(workspace_id), "workspace", workspace_id, expected_revision
        )

    def _write_value(
        self,
        value: GlobalConfigV2 | WorkspacePreferenceDocumentV3,
        path: Path,
        scope: str,
        workspace_id: str | None,
        expected_revision: int | None,
    ) -> PreferenceYamlLoad:
        with self._lock(scope, workspace_id):
            raw = _read_raw(path)
            current = (
                self._load_global(raw)
                if scope == "global"
                else self.load_workspace(workspace_id or "")
            )
            if current.status is not PreferenceYamlLoadStatus.OK:
                raise PreferenceYamlError(
                    current.error or "corrupt", "Preference YAML is not writable"
                )
            if expected_revision is not None and current.revision != expected_revision:
                raise PreferenceYamlConflict()
            if scope == "global" and not isinstance(value, GlobalConfigV2):
                raise PreferenceYamlError("scope", "global Preference document has the wrong type")
            if scope == "workspace" and not isinstance(value, WorkspacePreferenceDocumentV3):
                raise PreferenceYamlError(
                    "scope", "workspace Preference document has the wrong type"
                )
            if self.failure_injector:
                self.failure_injector("backup")
            if path.exists():
                self._backup(path)
            next_value = _with_revision(value, current.revision + 1)
            _write_bytes(path, _yaml_bytes(next_value), self.failure_injector)
            return (
                self._load_global(_read_raw(path))
                if scope == "global"
                else self.load_workspace(workspace_id or "")
            )

    def _backup(self, path: Path) -> None:
        try:
            _io_backup(path)
        except PreferenceYamlIoError as exc:
            raise PreferenceYamlError(exc.code, "Preference YAML backup failed") from exc


__all__ = [
    "GLOBAL_CONFIG_PREFERENCE_SCHEMA_VERSION",
    "LEGACY_GLOBAL_SCHEMA_VERSION",
    "LEGACY_WORKSPACE_PREFERENCE_SCHEMA_VERSION",
    "PreferenceMigrationPlan",
    "PreferenceYamlConflict",
    "PreferenceYamlDecodeError",
    "PreferenceYamlError",
    "PreferenceYamlLoad",
    "PreferenceYamlLoadStatus",
    "PreferenceYamlStore",
    "WORKSPACE_PREFERENCE_SCHEMA_VERSION",
    "decode_global_config",
    "decode_legacy_agent_run_preferences",
    "decode_workspace_preferences",
]
