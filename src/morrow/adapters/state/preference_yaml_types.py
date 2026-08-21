"""Public result and error contracts for Preference YAML operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from morrow.core.preference_documents import GlobalConfigV2, WorkspacePreferenceDocumentV3


class PreferenceYamlLoadStatus(StrEnum):
    OK = "ok"
    CORRUPT = "corrupt"
    UNSUPPORTED_SCHEMA = "unsupported_schema"


class PreferenceYamlError(RuntimeError):
    """Sanitized state/migration error; paths and YAML payloads never escape."""

    def __init__(self, code: str, message: str = "Preference YAML operation failed") -> None:
        super().__init__(message)
        self.code = code


class PreferenceYamlConflict(PreferenceYamlError):
    def __init__(self) -> None:
        super().__init__("revision_conflict", "Preference YAML revision changed")


@dataclass(frozen=True)
class PreferenceYamlLoad:
    status: PreferenceYamlLoadStatus
    value: GlobalConfigV2 | WorkspacePreferenceDocumentV3 | None
    revision: int
    source_schema_version: int | None
    migrated: bool = False
    presence: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class PreferenceMigrationPlan:
    scope: str
    source_revision: int
    source_schema_version: int
    source_digest: str
    value: GlobalConfigV2 | WorkspacePreferenceDocumentV3


__all__ = [
    "PreferenceMigrationPlan",
    "PreferenceYamlConflict",
    "PreferenceYamlError",
    "PreferenceYamlLoad",
    "PreferenceYamlLoadStatus",
]
