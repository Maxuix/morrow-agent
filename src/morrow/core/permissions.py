"""Durable, run-bound permission evidence for Stage 4.

The models in this module are evidence contracts, not a general authorization
engine.  A CapabilityGrant can only be created by the application boundary and
is frozen into a PermissionSnapshot for one AgentRun.  No model/tool payload is
accepted as an authority source here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from pydantic import Field, field_validator, model_validator

from morrow.core.capabilities import (
    AccessScope,
    ApprovalMode,
    LocalCapabilityModel,
    ProcessIsolation,
)
from morrow.core.domain import (
    AGENT_RUN_ID_PREFIX,
    CAPABILITY_GRANT_ID_PREFIX,
    COMMAND_ID_PREFIX,
    DIGEST_PATTERN,
    PERMISSION_SNAPSHOT_ID_PREFIX,
    SESSION_ID_PREFIX,
    TASK_RUN_ID_PREFIX,
    TURN_ID_PREFIX,
    WORKSPACE_ID_PREFIX,
    SourceRevisionRef,
    canonical_json_bytes,
    refuse_secret_material,
    require_payload_budget,
    sha256_digest,
    validate_prefixed_id,
)
from morrow.core.models import utc_now

PERMISSION_POLICY_VERSION = "stage4-permissions-v1"
PERMISSION_SCHEMA_VERSION = 9
GRANT_MAX_LIFETIME = timedelta(hours=24)
PERMISSION_SNAPSHOT_MAX_BYTES = 64 * 1024
GRANT_REASON_MAX_CHARS = 1_024
UNCONFINED_HOST_WARNING = (
    "unconfined_host: this process is not OS-isolated and may reach user files, network, "
    "credentials, sockets, and Morrow state with the current user's authority"
)
UNCONFINED_HOST_WARNING_DIGEST = sha256_digest(canonical_json_bytes(UNCONFINED_HOST_WARNING))
UNCONFINED_HOST_APPROVAL_LANGUAGE = (
    "明确确认：批准后该 Host 命令不会获得操作系统隔离，可能以当前用户权限触达用户文件、"
    "网络、凭据、套接字和 Morrow 状态"
)


class CapabilityName(StrEnum):
    """The deliberately small elevated capability set shipped in Stage 4."""

    UNCONFINED_HOST_PROCESS = "unconfined_host_process"


class GrantSource(StrEnum):
    """The only authority source accepted by the grant service."""

    LOCAL_INTERFACE_COMMAND = "local_interface_command"


class IsolationLabel(StrEnum):
    """Operational evidence; ``UNCONFINED_HOST`` is explicitly not isolation."""

    WORKSPACE = "workspace"
    NATIVE_SANDBOX = "native_sandbox"
    UNCONFINED_HOST = "unconfined_host"


class CapabilityIsolation(LocalCapabilityModel):
    """The effective process label for one frozen elevated capability."""

    capability: CapabilityName
    isolation: IsolationLabel

    @model_validator(mode="after")
    def supported_pair(self) -> CapabilityIsolation:
        if self.capability is CapabilityName.UNCONFINED_HOST_PROCESS:
            if self.isolation is not IsolationLabel.UNCONFINED_HOST:
                raise ValueError("unconfined_host_process must use the unconfined_host label")
        return self


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _clean_reason(value: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > GRANT_REASON_MAX_CHARS:
        raise ValueError("grant reason must be bounded and non-empty")
    refuse_secret_material(cleaned, label="grant reason")
    return cleaned


def _valid_digest(value: str) -> str:
    if not DIGEST_PATTERN.fullmatch(value):
        raise ValueError("permission evidence digest must be a SHA-256 hex digest")
    return value


def workspace_root_digest(root: Path) -> str:
    """Return a non-reversible workspace-root evidence digest.

    Absolute workspace paths are intentionally not persisted in permission
    evidence.  The caller is responsible for supplying the already validated
    absolute root from ``WorkspaceCapability``.
    """

    if not root.is_absolute():
        raise ValueError("workspace root must be absolute")
    return sha256_digest(str(root).encode("utf-8"))


class CapabilityGrant(LocalCapabilityModel):
    """Immutable authority metadata plus explicit revocation state."""

    grant_id: str
    workspace_id: str
    task_run_id: str
    agent_run_id: str
    capabilities: tuple[CapabilityName, ...]
    granted_by: GrantSource = GrantSource.LOCAL_INTERFACE_COMMAND
    command_id: str
    reason: str
    preview_digest: str
    policy_version: str = PERMISSION_POLICY_VERSION
    schema_version: int = Field(default=PERMISSION_SCHEMA_VERSION, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    revoked_at: datetime | None = None
    revocation_reason: str | None = None
    row_version: int = Field(default=1, ge=1)

    @field_validator("grant_id")
    @classmethod
    def valid_grant_id(cls, value: str) -> str:
        return validate_prefixed_id(value, CAPABILITY_GRANT_ID_PREFIX)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, WORKSPACE_ID_PREFIX)

    @field_validator("task_run_id")
    @classmethod
    def valid_task_run_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TASK_RUN_ID_PREFIX)

    @field_validator("agent_run_id")
    @classmethod
    def valid_agent_run_id(cls, value: str) -> str:
        return validate_prefixed_id(value, AGENT_RUN_ID_PREFIX)

    @field_validator("command_id")
    @classmethod
    def valid_command_id(cls, value: str) -> str:
        return validate_prefixed_id(value, COMMAND_ID_PREFIX)

    @field_validator("preview_digest")
    @classmethod
    def valid_preview_digest(cls, value: str) -> str:
        return _valid_digest(value)

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value: str) -> str:
        return _clean_reason(value)

    @field_validator("revocation_reason")
    @classmethod
    def valid_revocation_reason(cls, value: str | None) -> str | None:
        return None if value is None else _clean_reason(value)

    @field_validator("policy_version")
    @classmethod
    def valid_policy_version(cls, value: str) -> str:
        if value != PERMISSION_POLICY_VERSION:
            raise ValueError("unsupported permission policy version")
        return value

    @field_validator("created_at", "expires_at", "revoked_at", mode="before")
    @classmethod
    def normalize_timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc(value)

    @field_validator("capabilities")
    @classmethod
    def valid_capabilities(cls, values: tuple[CapabilityName, ...]) -> tuple[CapabilityName, ...]:
        if not values:
            raise ValueError("capability grant must contain at least one capability")
        if len(values) > 8 or len(set(values)) != len(values):
            raise ValueError("capability grant contains a duplicate or too many capabilities")
        # Stage 4 deliberately exposes only the one capability declared above.
        if any(value is not CapabilityName.UNCONFINED_HOST_PROCESS for value in values):
            raise ValueError("capability is not available in Stage 4")
        return values

    @model_validator(mode="after")
    def enforce_contract(self) -> CapabilityGrant:
        if self.granted_by is not GrantSource.LOCAL_INTERFACE_COMMAND:
            raise ValueError("capability grants require a local interface command")
        if self.schema_version != PERMISSION_SCHEMA_VERSION:
            raise ValueError("unsupported permission evidence schema version")
        if self.expires_at <= self.created_at:
            raise ValueError("grant expiry must be after creation")
        if self.expires_at - self.created_at > GRANT_MAX_LIFETIME:
            raise ValueError("grant lifetime exceeds the bounded Stage 4 maximum")
        if self.revoked_at is None and self.revocation_reason is not None:
            raise ValueError("revocation reason requires a revoked grant")
        if self.revoked_at is not None:
            if self.revoked_at < self.created_at:
                raise ValueError("grant revocation cannot precede creation")
            if self.revocation_reason is None:
                raise ValueError("revoked grant requires a revocation reason")
        payload = canonical_json_bytes(self.model_dump(mode="json"))
        require_payload_budget(payload, PERMISSION_SNAPSHOT_MAX_BYTES, label="capability grant")
        refuse_secret_material(payload, label="capability grant")
        return self

    def is_active(self, now: datetime) -> bool:
        current = _utc(now)
        return self.revoked_at is None and current < self.expires_at


class PermissionSnapshot(LocalCapabilityModel):
    """Complete immutable permission evidence frozen for one AgentRun."""

    permission_snapshot_id: str
    workspace_id: str
    session_id: str
    task_run_id: str
    turn_id: str
    agent_run_id: str
    access_scope: AccessScope
    approval_mode: ApprovalMode
    process_isolation: ProcessIsolation
    workspace_root_digest: str
    workspace_read_only: bool
    tool_schema_digest: str
    run_policy_digest: str
    permission_profile_digest: str
    policy_version: str = PERMISSION_POLICY_VERSION
    schema_version: int = Field(default=PERMISSION_SCHEMA_VERSION, ge=1)
    source_revisions: tuple[SourceRevisionRef, ...] = ()
    grant_id: str | None = None
    grant_digest: str | None = None
    granted_capabilities: tuple[CapabilityName, ...] = ()
    capability_isolations: tuple[CapabilityIsolation, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("permission_snapshot_id")
    @classmethod
    def valid_snapshot_id(cls, value: str) -> str:
        return validate_prefixed_id(value, PERMISSION_SNAPSHOT_ID_PREFIX)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, WORKSPACE_ID_PREFIX)

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("task_run_id")
    @classmethod
    def valid_task_run_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TASK_RUN_ID_PREFIX)

    @field_validator("turn_id")
    @classmethod
    def valid_turn_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TURN_ID_PREFIX)

    @field_validator("agent_run_id")
    @classmethod
    def valid_agent_run_id(cls, value: str) -> str:
        return validate_prefixed_id(value, AGENT_RUN_ID_PREFIX)

    @field_validator("grant_id")
    @classmethod
    def valid_grant_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, CAPABILITY_GRANT_ID_PREFIX)

    @field_validator(
        "workspace_root_digest",
        "tool_schema_digest",
        "run_policy_digest",
        "permission_profile_digest",
        "grant_digest",
    )
    @classmethod
    def valid_digests(cls, value: str | None) -> str | None:
        return None if value is None else _valid_digest(value)

    @field_validator("policy_version")
    @classmethod
    def valid_policy_version(cls, value: str) -> str:
        if value != PERMISSION_POLICY_VERSION:
            raise ValueError("unsupported permission policy version")
        return value

    @field_validator("created_at", mode="before")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return _utc(value)

    @field_validator("granted_capabilities")
    @classmethod
    def valid_granted_capabilities(
        cls, values: tuple[CapabilityName, ...]
    ) -> tuple[CapabilityName, ...]:
        if len(values) > 8 or len(set(values)) != len(values):
            raise ValueError("permission snapshot capabilities must be unique and bounded")
        if any(value is not CapabilityName.UNCONFINED_HOST_PROCESS for value in values):
            raise ValueError("capability is not available in Stage 4")
        return values

    @model_validator(mode="after")
    def enforce_contract(self) -> PermissionSnapshot:
        if self.schema_version != PERMISSION_SCHEMA_VERSION:
            raise ValueError("unsupported permission evidence schema version")
        if self.grant_id is None:
            if self.grant_digest is not None or self.granted_capabilities:
                raise ValueError("ungranted snapshots cannot contain grant evidence")
            if self.capability_isolations:
                raise ValueError("ungranted snapshots cannot contain elevated isolation evidence")
        else:
            if not self.granted_capabilities or not self.grant_digest:
                raise ValueError("granted snapshots require complete grant evidence")
            if (
                self.access_scope is not AccessScope.FULL_ACCESS
                or self.approval_mode is not ApprovalMode.MANUAL
                or self.process_isolation is not ProcessIsolation.HOST
            ):
                raise ValueError("elevated snapshots require Full Access Manual")
            if (
                tuple(item.capability for item in self.capability_isolations)
                != self.granted_capabilities
            ):
                raise ValueError("capability isolation evidence must match granted capabilities")
        payload = canonical_json_bytes(self.model_dump(mode="json"))
        require_payload_budget(payload, PERMISSION_SNAPSHOT_MAX_BYTES, label="PermissionSnapshot")
        refuse_secret_material(payload, label="PermissionSnapshot")
        return self

    @property
    def isolation_label(self) -> IsolationLabel | None:
        if len(self.capability_isolations) != 1:
            return None
        return self.capability_isolations[0].isolation


class PermissionEvidenceError(ValueError):
    """Frozen permission evidence cannot prove the requested elevated effect."""


def capability_grant_digest(grant: CapabilityGrant) -> str:
    """Hash the immutable authority fields, excluding revocation bookkeeping."""

    payload = {
        "grant_id": grant.grant_id,
        "workspace_id": grant.workspace_id,
        "task_run_id": grant.task_run_id,
        "agent_run_id": grant.agent_run_id,
        "capabilities": tuple(value.value for value in grant.capabilities),
        "granted_by": grant.granted_by.value,
        "command_id": grant.command_id,
        "reason": grant.reason,
        "preview_digest": grant.preview_digest,
        "policy_version": grant.policy_version,
        "schema_version": grant.schema_version,
        "created_at": grant.created_at.isoformat(),
        "expires_at": grant.expires_at.isoformat(),
    }
    return sha256_digest(canonical_json_bytes(payload))


def assert_grant_snapshot_matches(
    snapshot: PermissionSnapshot,
    grant: CapabilityGrant,
    *,
    now: datetime,
    workspace_id: str,
    task_run_id: str,
    agent_run_id: str,
) -> None:
    """Fail closed when a run-bound grant cannot prove the current scope."""

    if snapshot.workspace_id != workspace_id:
        raise PermissionEvidenceError("permission snapshot is outside the workspace")
    if snapshot.task_run_id != task_run_id or snapshot.agent_run_id != agent_run_id:
        raise PermissionEvidenceError("permission snapshot subjects are mismatched")
    if snapshot.grant_id != grant.grant_id:
        raise PermissionEvidenceError("permission snapshot grant is mismatched")
    if snapshot.grant_digest != capability_grant_digest(grant):
        raise PermissionEvidenceError("permission snapshot grant digest is mismatched")
    if snapshot.granted_capabilities != grant.capabilities:
        raise PermissionEvidenceError("permission snapshot capability subset is mismatched")
    if (
        snapshot.access_scope is not AccessScope.FULL_ACCESS
        or snapshot.approval_mode is not ApprovalMode.MANUAL
        or snapshot.process_isolation is not ProcessIsolation.HOST
    ):
        raise PermissionEvidenceError("elevated capability requires Full Access Manual")
    if not grant.is_active(now):
        raise PermissionEvidenceError("capability grant is expired or revoked")
    if CapabilityName.UNCONFINED_HOST_PROCESS in grant.capabilities:
        if snapshot.isolation_label is not IsolationLabel.UNCONFINED_HOST:
            raise PermissionEvidenceError("elevated capability lacks the unconfined_host label")
