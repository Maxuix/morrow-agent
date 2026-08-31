"""Sanitized backup bundle contracts."""

from __future__ import annotations

import posixpath
import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import (
    ARTIFACT_ID_PREFIX,
    DIGEST_PATTERN,
    WORKSPACE_ID_PREFIX,
    canonical_json_bytes,
    refuse_secret_material,
    validate_prefixed_id,
)
from morrow.core.models import ProtocolModel, utc_now

BACKUP_MANIFEST_VERSION = 2
BACKUP_MAX_FILES = 16_384
BACKUP_MAX_REFERENCES = 8_192
BACKUP_MAX_YAML_DOCUMENTS = 512
BACKUP_MAX_SKILL_VERSIONS = 2_048


class ArtifactBackupStatus(StrEnum):
    COPIED = "copied"


class BackupArtifactEntry(ProtocolModel):
    artifact_id: str
    workspace_id: str
    state: str
    sha256: str
    byte_size: int = Field(ge=0)
    status: ArtifactBackupStatus
    copied: bool = False
    verified: bool = False
    path: str

    @field_validator("artifact_id")
    @classmethod
    def valid_artifact_id(cls, value: str) -> str:
        return validate_prefixed_id(value, ARTIFACT_ID_PREFIX)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, WORKSPACE_ID_PREFIX)

    @field_validator("sha256")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        if not DIGEST_PATTERN.match(value):
            raise ValueError("backup hash must be a SHA-256 hex digest")
        return value

    @field_validator("path")
    @classmethod
    def valid_artifact_path(cls, value: str) -> str:
        if (
            not value
            or "\x00" in value
            or "\\" in value
            or value.startswith("/")
            or value != unicodedata.normalize("NFC", value)
            or posixpath.normpath(value) != value
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("backup Artifact path must be canonical and relative")
        return value


class BackupFileKind(StrEnum):
    DATABASE = "database"
    ARTIFACT = "artifact"
    EXTENSION_YAML = "extension_yaml"
    CONFIGURATION = "configuration"
    SKILL_PACKAGE = "skill_package"
    DEFINITION_SOURCE = "definition_source"


class BackupFileEntry(ProtocolModel):
    """One copied backup file addressed by a canonical bundle-relative path."""

    path: str
    kind: BackupFileKind
    sha256: str
    byte_size: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def valid_relative_path(cls, value: str) -> str:
        if (
            not value
            or "\x00" in value
            or "\\" in value
            or value.startswith("/")
            or value != unicodedata.normalize("NFC", value)
            or posixpath.isabs(value)
        ):
            raise ValueError("backup path must be a normalized relative POSIX path")
        parts = value.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("backup path contains an unsafe segment")
        if posixpath.normpath(value) != value:
            raise ValueError("backup path is not canonical")
        return value

    @model_validator(mode="after")
    def definition_source_path(self):
        if self.kind is BackupFileKind.DEFINITION_SOURCE:
            parts = self.path.split("/")
            if len(parts) != 3 or parts[0] != "workspaces" or parts[2] != "agent-definitions.yaml":
                raise ValueError("definition source path is not whitelisted")
            validate_prefixed_id(parts[1], WORKSPACE_ID_PREFIX)
            if self.byte_size > 2 * 1024 * 1024:
                raise ValueError("definition source exceeds its byte budget")
        return self

    @field_validator("sha256")
    @classmethod
    def valid_file_digest(cls, value: str) -> str:
        if not DIGEST_PATTERN.match(value):
            raise ValueError("backup file hash must be a SHA-256 hex digest")
        return value


class BackupYamlEntry(ProtocolModel):
    path: str
    scope: Literal["global", "workspace"]
    scope_id: str | None = None
    revision: int = Field(ge=0)
    schema_version: int = Field(ge=1)
    sha256: str

    @field_validator("path")
    @classmethod
    def valid_yaml_path(cls, value: str) -> str:
        return BackupFileEntry.valid_relative_path(value)

    @field_validator("sha256")
    @classmethod
    def valid_yaml_digest(cls, value: str) -> str:
        return BackupFileEntry.valid_file_digest(value)

    @model_validator(mode="after")
    def valid_yaml_scope(self) -> BackupYamlEntry:
        if self.scope == "global" and self.scope_id is not None:
            raise ValueError("global backup YAML must not carry a scope id")
        if self.scope == "workspace" and (
            self.scope_id is None or not self.scope_id.startswith("ws_")
        ):
            raise ValueError("workspace backup YAML requires a workspace id")
        return self


class BackupSkillEntry(ProtocolModel):
    skill_id: str
    version_id: str
    source_kind: Literal["imported", "generated"]
    effective_trust: Literal["builtin", "user", "generated", "imported", "unknown"] = "unknown"
    scope_id: str | None = None
    tree_digest: str
    envelope_sha256: str
    package_path: str
    file_paths: tuple[str, ...] = ()

    @field_validator("tree_digest", "envelope_sha256")
    @classmethod
    def valid_skill_digest(cls, value: str) -> str:
        if not DIGEST_PATTERN.match(value):
            raise ValueError("Skill backup digest must be a SHA-256 hex digest")
        return value

    @field_validator("package_path")
    @classmethod
    def valid_package_path(cls, value: str) -> str:
        return BackupFileEntry.valid_relative_path(value)

    @field_validator("file_paths")
    @classmethod
    def valid_file_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(BackupFileEntry.valid_relative_path(value) for value in values)
        if len(normalized) != len(set(normalized)):
            raise ValueError("Skill backup file paths must be unique")
        return normalized


class BackupReference(ProtocolModel):
    """A bounded, content-free cross-store reference in the current manifest."""

    kind: str = Field(min_length=1, max_length=64)
    identifier: str = Field(min_length=1, max_length=256)
    target: str = Field(min_length=1, max_length=512)

    @field_validator("target")
    @classmethod
    def valid_target(cls, value: str) -> str:
        return BackupFileEntry.valid_relative_path(value)


class BackupManifest(ProtocolModel):
    """Explicit Stage 6 bundle contract; it is never decoded as v1."""

    manifest_version: Literal[BACKUP_MANIFEST_VERSION] = BACKUP_MANIFEST_VERSION
    schema_version: int = Field(ge=1)
    schema_versions: dict[str, int] = Field(default_factory=dict)
    workspace_ids: tuple[str, ...] = ()
    files: tuple[BackupFileEntry, ...] = ()
    artifacts: tuple[BackupArtifactEntry, ...] = ()
    yaml_documents: tuple[BackupYamlEntry, ...] = ()
    skill_versions: tuple[BackupSkillEntry, ...] = ()
    references: tuple[BackupReference, ...] = ()
    database_path: str = "database.sqlite"
    created_at: datetime = Field(default_factory=utc_now)
    credentials_excluded: Literal[True] = True

    @field_validator("database_path")
    @classmethod
    def valid_database_path(cls, value: str) -> str:
        if value != "database.sqlite":
            raise ValueError("backup database path is fixed")
        return value

    @model_validator(mode="after")
    def bounded_and_canonical(self) -> BackupManifest:
        if len(self.files) > BACKUP_MAX_FILES:
            raise ValueError("backup contains too many files")
        if len(self.yaml_documents) > BACKUP_MAX_YAML_DOCUMENTS:
            raise ValueError("backup contains too many YAML documents")
        if len(self.skill_versions) > BACKUP_MAX_SKILL_VERSIONS:
            raise ValueError("backup contains too many Skill versions")
        if len(self.references) > BACKUP_MAX_REFERENCES:
            raise ValueError("backup contains too many references")
        paths = tuple(item.path for item in self.files)
        if len(paths) != len(set(paths)):
            raise ValueError("backup file paths must be unique")
        if any(path in {"manifest.json", "manifest.sha256"} for path in paths):
            raise ValueError("backup manifest files are reserved")
        yaml_paths = tuple(item.path for item in self.yaml_documents)
        if len(yaml_paths) != len(set(yaml_paths)):
            raise ValueError("backup YAML paths must be unique")
        skill_keys = tuple(
            (item.skill_id, item.version_id, item.scope_id) for item in self.skill_versions
        )
        if len(skill_keys) != len(set(skill_keys)):
            raise ValueError("backup Skill versions must be unique")
        reference_keys = tuple(
            (item.kind, item.identifier, item.target) for item in self.references
        )
        if len(reference_keys) != len(set(reference_keys)):
            raise ValueError("backup references must be unique")
        file_by_path = {item.path: item for item in self.files}
        database_file = file_by_path.get(self.database_path)
        if database_file is None or database_file.kind is not BackupFileKind.DATABASE:
            raise ValueError("backup database file is missing")
        for item in self.artifacts:
            file_item = file_by_path.get(item.path)
            if file_item is None or file_item.kind is not BackupFileKind.ARTIFACT:
                raise ValueError("backup Artifact file is missing")
            if item.status is not ArtifactBackupStatus.COPIED:
                raise ValueError("backup Artifacts must be copied")
        for item in self.yaml_documents:
            file_item = file_by_path.get(item.path)
            if file_item is None or file_item.kind not in {
                BackupFileKind.EXTENSION_YAML,
                BackupFileKind.CONFIGURATION,
            }:
                raise ValueError("backup YAML file is missing")
        skill_ids = {item.version_id for item in self.skill_versions}
        for item in self.skill_versions:
            if any(
                path not in file_by_path
                or file_by_path[path].kind is not BackupFileKind.SKILL_PACKAGE
                for path in item.file_paths
            ):
                raise ValueError("backup Skill file is missing")
            package_parent = item.package_path.rsplit("/", 1)[0]
            envelope_path = f"{package_parent}/managed-version.json"
            if envelope_path not in item.file_paths or not any(
                path.startswith(item.package_path + "/") for path in item.file_paths
            ):
                raise ValueError("backup Skill package path is invalid")
        artifact_paths = {item.path for item in self.artifacts}
        yaml_paths = {item.path for item in self.yaml_documents}
        skill_paths = {path for item in self.skill_versions for path in item.file_paths}
        for item in self.files:
            if item.kind is BackupFileKind.DATABASE and item.path != self.database_path:
                raise ValueError("backup database path is invalid")
            if item.kind is BackupFileKind.ARTIFACT and item.path not in artifact_paths:
                raise ValueError("backup Artifact file is unowned")
            if (
                item.kind
                in {
                    BackupFileKind.EXTENSION_YAML,
                    BackupFileKind.CONFIGURATION,
                }
                and item.path not in yaml_paths
            ):
                raise ValueError("backup YAML file is unowned")
            if item.kind is BackupFileKind.SKILL_PACKAGE and item.path not in skill_paths:
                raise ValueError("backup Skill file is unowned")
        definition_paths = {
            item.path for item in self.files if item.kind is BackupFileKind.DEFINITION_SOURCE
        }
        if definition_paths != {
            ref.target for ref in self.references if ref.kind == "definition_source"
        }:
            raise ValueError("backup definition source reference is missing")
        for reference in self.references:
            if reference.kind == "skill_version" and reference.identifier not in skill_ids:
                raise ValueError("backup Skill reference is missing")
            if reference.kind == "artifact" and reference.target not in artifact_paths:
                raise ValueError("backup Artifact reference is missing")
            if reference.kind == "yaml" and reference.target not in yaml_paths:
                raise ValueError("backup YAML reference is missing")
        payload_model = self.model_dump(mode="json")
        # This is a boolean exclusion assertion, not credential material. Keep
        # the generic scanner focused on values and opaque evidence fields.
        payload_model.pop("credentials_excluded", None)
        payload = canonical_json_bytes(payload_model)
        if len(payload) > 512 * 1024:
            raise ValueError("backup manifest exceeds its budget")
        refuse_secret_material(payload, label="backup manifest")
        return self


class BackupVerificationReport(ProtocolModel):
    bundle_name: str
    manifest_ok: bool
    database_integrity_ok: bool
    foreign_keys_ok: bool
    files_ok: bool
    yaml_ok: bool
    skills_ok: bool
    artifacts_ok: bool
    references_ok: bool
    credentials_excluded: bool = True
    issues: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return all(
            (
                self.manifest_ok,
                self.database_integrity_ok,
                self.foreign_keys_ok,
                self.files_ok,
                self.yaml_ok,
                self.skills_ok,
                self.artifacts_ok,
                self.references_ok,
                self.credentials_excluded,
            )
        )


class BackupBundleReport(ProtocolModel):
    bundle_name: str
    database_name: str = "database.sqlite"
    manifest_name: str = "manifest.json"
    schema_version: int = Field(ge=1)
    integrity_ok: bool
    manifest_sha256: str
    artifacts: tuple[BackupArtifactEntry, ...] = ()
    skill_versions: tuple[BackupSkillEntry, ...] = ()
    credentials_excluded: Literal[True] = True

    @field_validator("manifest_sha256")
    @classmethod
    def valid_bundle_manifest_digest(cls, value: str) -> str:
        return BackupFileEntry.valid_file_digest(value)


class BackupRestoreReport(ProtocolModel):
    bundle_name: str
    target_root: str
    restored: bool
    issues: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.restored and not self.issues


__all__ = [
    "BACKUP_MANIFEST_VERSION",
    "ArtifactBackupStatus",
    "BackupBundleReport",
    "BackupArtifactEntry",
    "BackupManifest",
    "BackupFileEntry",
    "BackupFileKind",
    "BackupReference",
    "BackupRestoreReport",
    "BackupSkillEntry",
    "BackupVerificationReport",
    "BackupYamlEntry",
]
