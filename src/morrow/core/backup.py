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

BACKUP_MANIFEST_VERSION = 1
BACKUP_V2_MANIFEST_VERSION = 2
BACKUP_V2_MAX_FILES = 16_384
BACKUP_V2_MAX_REFERENCES = 8_192
BACKUP_V2_MAX_YAML_DOCUMENTS = 512
BACKUP_V2_MAX_SKILL_VERSIONS = 2_048


class ArtifactBackupStatus(StrEnum):
    COPIED = "copied"
    MISSING = "missing"
    CORRUPT = "corrupt"
    CHANGED = "changed"
    NOT_AVAILABLE = "not_available"


class ArtifactBackupEntry(ProtocolModel):
    artifact_id: str
    workspace_id: str
    state: str
    sha256: str
    byte_size: int = Field(ge=0)
    status: ArtifactBackupStatus
    copied: bool = False
    verified: bool = False

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


class BackupV2ArtifactEntry(ArtifactBackupEntry):
    path: str

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


class BackupManifest(ProtocolModel):
    """Historical v1 manifest.

    The default keeps old bundles that predate the explicit field decodable.  A
    v2 manifest has a separate contract so a v1 verifier cannot accidentally
    accept a relabeled/incomplete v2 bundle.
    """

    manifest_version: Literal[BACKUP_MANIFEST_VERSION] = BACKUP_MANIFEST_VERSION
    schema_version: int = Field(ge=1)
    workspace_ids: tuple[str, ...] = ()
    artifacts: tuple[ArtifactBackupEntry, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def bounded_and_redacted(self) -> BackupManifest:
        payload = canonical_json_bytes(self.model_dump(mode="json"))
        if len(payload) > 32 * 1024:
            raise ValueError("backup manifest exceeds its budget")
        refuse_secret_material(payload, label="backup manifest")
        return self


class BackupBundleReport(ProtocolModel):
    bundle_name: str
    database_name: str
    manifest_name: str
    schema_version: int
    integrity_ok: bool
    manifest_sha256: str
    artifacts: tuple[ArtifactBackupEntry, ...] = ()
    credentials_excluded: bool = True
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("manifest_sha256")
    @classmethod
    def valid_manifest_digest(cls, value: str) -> str:
        if not DIGEST_PATTERN.match(value):
            raise ValueError("backup manifest hash must be a SHA-256 hex digest")
        return value


class BackupVerificationReport(ProtocolModel):
    bundle_name: str
    database_integrity_ok: bool
    foreign_keys_ok: bool
    manifest_ok: bool
    artifacts_ok: bool
    memory_references_ok: bool = True
    learning_references_ok: bool = True
    preference_references_ok: bool = True
    credentials_excluded: bool = True
    issues: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return (
            self.database_integrity_ok
            and self.foreign_keys_ok
            and self.manifest_ok
            and self.artifacts_ok
            and self.memory_references_ok
            and self.learning_references_ok
            and self.preference_references_ok
            and self.credentials_excluded
        )


class BackupV2FileKind(StrEnum):
    DATABASE = "database"
    ARTIFACT = "artifact"
    EXTENSION_YAML = "extension_yaml"
    CONFIGURATION = "configuration"
    SKILL_PACKAGE = "skill_package"


class BackupV2FileEntry(ProtocolModel):
    """One copied v2 file addressed by a canonical bundle-relative path."""

    path: str
    kind: BackupV2FileKind
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

    @field_validator("sha256")
    @classmethod
    def valid_file_digest(cls, value: str) -> str:
        if not DIGEST_PATTERN.match(value):
            raise ValueError("backup file hash must be a SHA-256 hex digest")
        return value


class BackupV2YamlEntry(ProtocolModel):
    path: str
    scope: Literal["global", "workspace"]
    scope_id: str | None = None
    revision: int = Field(ge=0)
    schema_version: int = Field(ge=1)
    sha256: str

    @field_validator("path")
    @classmethod
    def valid_yaml_path(cls, value: str) -> str:
        return BackupV2FileEntry.valid_relative_path(value)

    @field_validator("sha256")
    @classmethod
    def valid_yaml_digest(cls, value: str) -> str:
        return BackupV2FileEntry.valid_file_digest(value)

    @model_validator(mode="after")
    def valid_yaml_scope(self) -> BackupV2YamlEntry:
        if self.scope == "global" and self.scope_id is not None:
            raise ValueError("global backup YAML must not carry a scope id")
        if self.scope == "workspace" and (
            self.scope_id is None or not self.scope_id.startswith("ws_")
        ):
            raise ValueError("workspace backup YAML requires a workspace id")
        return self


class BackupV2SkillEntry(ProtocolModel):
    skill_id: str
    version_id: str
    source_kind: Literal["imported", "generated"]
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
        return BackupV2FileEntry.valid_relative_path(value)

    @field_validator("file_paths")
    @classmethod
    def valid_file_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(BackupV2FileEntry.valid_relative_path(value) for value in values)
        if len(normalized) != len(set(normalized)):
            raise ValueError("Skill backup file paths must be unique")
        return normalized


class BackupV2Reference(ProtocolModel):
    """A bounded, content-free cross-store reference in the v2 manifest."""

    kind: str = Field(min_length=1, max_length=64)
    identifier: str = Field(min_length=1, max_length=256)
    target: str = Field(min_length=1, max_length=512)

    @field_validator("target")
    @classmethod
    def valid_target(cls, value: str) -> str:
        return BackupV2FileEntry.valid_relative_path(value)


class BackupV2Manifest(ProtocolModel):
    """Explicit Stage 6 bundle contract; it is never decoded as v1."""

    manifest_version: Literal[BACKUP_V2_MANIFEST_VERSION] = BACKUP_V2_MANIFEST_VERSION
    schema_version: int = Field(ge=1)
    schema_versions: dict[str, int] = Field(default_factory=dict)
    workspace_ids: tuple[str, ...] = ()
    files: tuple[BackupV2FileEntry, ...] = ()
    artifacts: tuple[BackupV2ArtifactEntry, ...] = ()
    yaml_documents: tuple[BackupV2YamlEntry, ...] = ()
    skill_versions: tuple[BackupV2SkillEntry, ...] = ()
    references: tuple[BackupV2Reference, ...] = ()
    database_path: str = "database.sqlite"
    created_at: datetime = Field(default_factory=utc_now)
    credentials_excluded: Literal[True] = True

    @field_validator("database_path")
    @classmethod
    def valid_database_path(cls, value: str) -> str:
        if value != "database.sqlite":
            raise ValueError("v2 database path is fixed")
        return value

    @model_validator(mode="after")
    def bounded_and_canonical(self) -> BackupV2Manifest:
        if len(self.files) > BACKUP_V2_MAX_FILES:
            raise ValueError("backup v2 contains too many files")
        if len(self.yaml_documents) > BACKUP_V2_MAX_YAML_DOCUMENTS:
            raise ValueError("backup v2 contains too many YAML documents")
        if len(self.skill_versions) > BACKUP_V2_MAX_SKILL_VERSIONS:
            raise ValueError("backup v2 contains too many Skill versions")
        if len(self.references) > BACKUP_V2_MAX_REFERENCES:
            raise ValueError("backup v2 contains too many references")
        paths = tuple(item.path for item in self.files)
        if len(paths) != len(set(paths)):
            raise ValueError("backup v2 file paths must be unique")
        if any(path in {"manifest.json", "manifest.sha256"} for path in paths):
            raise ValueError("backup v2 manifest files are reserved")
        yaml_paths = tuple(item.path for item in self.yaml_documents)
        if len(yaml_paths) != len(set(yaml_paths)):
            raise ValueError("backup v2 YAML paths must be unique")
        skill_keys = tuple(
            (item.skill_id, item.version_id, item.scope_id) for item in self.skill_versions
        )
        if len(skill_keys) != len(set(skill_keys)):
            raise ValueError("backup v2 Skill versions must be unique")
        reference_keys = tuple(
            (item.kind, item.identifier, item.target) for item in self.references
        )
        if len(reference_keys) != len(set(reference_keys)):
            raise ValueError("backup v2 references must be unique")
        file_by_path = {item.path: item for item in self.files}
        database_file = file_by_path.get(self.database_path)
        if database_file is None or database_file.kind is not BackupV2FileKind.DATABASE:
            raise ValueError("backup v2 database file is missing")
        for item in self.artifacts:
            file_item = file_by_path.get(item.path)
            if file_item is None or file_item.kind is not BackupV2FileKind.ARTIFACT:
                raise ValueError("backup v2 Artifact file is missing")
            if item.status is not ArtifactBackupStatus.COPIED:
                raise ValueError("backup v2 Artifacts must be copied")
        for item in self.yaml_documents:
            file_item = file_by_path.get(item.path)
            if file_item is None or file_item.kind not in {
                BackupV2FileKind.EXTENSION_YAML,
                BackupV2FileKind.CONFIGURATION,
            }:
                raise ValueError("backup v2 YAML file is missing")
        skill_ids = {item.version_id for item in self.skill_versions}
        for item in self.skill_versions:
            if any(
                path not in file_by_path
                or file_by_path[path].kind is not BackupV2FileKind.SKILL_PACKAGE
                for path in item.file_paths
            ):
                raise ValueError("backup v2 Skill file is missing")
            package_parent = item.package_path.rsplit("/", 1)[0]
            envelope_path = f"{package_parent}/managed-version.json"
            if envelope_path not in item.file_paths or not any(
                path.startswith(item.package_path + "/") for path in item.file_paths
            ):
                raise ValueError("backup v2 Skill package path is invalid")
        artifact_paths = {item.path for item in self.artifacts}
        yaml_paths = {item.path for item in self.yaml_documents}
        skill_paths = {path for item in self.skill_versions for path in item.file_paths}
        for item in self.files:
            if item.kind is BackupV2FileKind.DATABASE and item.path != self.database_path:
                raise ValueError("backup v2 database path is invalid")
            if item.kind is BackupV2FileKind.ARTIFACT and item.path not in artifact_paths:
                raise ValueError("backup v2 Artifact file is unowned")
            if (
                item.kind
                in {
                    BackupV2FileKind.EXTENSION_YAML,
                    BackupV2FileKind.CONFIGURATION,
                }
                and item.path not in yaml_paths
            ):
                raise ValueError("backup v2 YAML file is unowned")
            if item.kind is BackupV2FileKind.SKILL_PACKAGE and item.path not in skill_paths:
                raise ValueError("backup v2 Skill file is unowned")
        for reference in self.references:
            if reference.kind == "skill_version" and reference.identifier not in skill_ids:
                raise ValueError("backup v2 Skill reference is missing")
            if reference.kind == "artifact" and reference.target not in artifact_paths:
                raise ValueError("backup v2 Artifact reference is missing")
            if reference.kind == "yaml" and reference.target not in yaml_paths:
                raise ValueError("backup v2 YAML reference is missing")
        payload_model = self.model_dump(mode="json")
        # This is a boolean exclusion assertion, not credential material. Keep
        # the generic scanner focused on values and opaque evidence fields.
        payload_model.pop("credentials_excluded", None)
        payload = canonical_json_bytes(payload_model)
        if len(payload) > 512 * 1024:
            raise ValueError("backup v2 manifest exceeds its budget")
        refuse_secret_material(payload, label="backup v2 manifest")
        return self


class BackupV2VerificationReport(ProtocolModel):
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


class BackupV2BundleReport(ProtocolModel):
    bundle_name: str
    database_name: str = "database.sqlite"
    manifest_name: str = "manifest.json"
    schema_version: int = Field(ge=1)
    integrity_ok: bool
    manifest_sha256: str
    artifacts: tuple[BackupV2ArtifactEntry, ...] = ()
    skill_versions: tuple[BackupV2SkillEntry, ...] = ()
    credentials_excluded: Literal[True] = True

    @field_validator("manifest_sha256")
    @classmethod
    def valid_bundle_manifest_digest(cls, value: str) -> str:
        return BackupV2FileEntry.valid_file_digest(value)


class BackupV2RestoreReport(ProtocolModel):
    bundle_name: str
    target_root: str
    restored: bool
    issues: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.restored and not self.issues


__all__ = [
    "BACKUP_MANIFEST_VERSION",
    "BACKUP_V2_MANIFEST_VERSION",
    "ArtifactBackupEntry",
    "ArtifactBackupStatus",
    "BackupBundleReport",
    "BackupV2BundleReport",
    "BackupV2ArtifactEntry",
    "BackupManifest",
    "BackupV2FileEntry",
    "BackupV2FileKind",
    "BackupV2Manifest",
    "BackupV2Reference",
    "BackupV2RestoreReport",
    "BackupV2SkillEntry",
    "BackupV2VerificationReport",
    "BackupV2YamlEntry",
    "BackupVerificationReport",
]
