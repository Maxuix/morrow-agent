"""Sanitized backup bundle contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

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


class BackupManifest(ProtocolModel):
    manifest_version: int = Field(default=BACKUP_MANIFEST_VERSION, ge=1)
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
    credentials_excluded: bool = True
    issues: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return (
            self.database_integrity_ok
            and self.foreign_keys_ok
            and self.manifest_ok
            and self.artifacts_ok
            and self.credentials_excluded
        )
