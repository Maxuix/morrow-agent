"""Framework-independent MCP control-plane contracts.

This module deliberately contains no SDK, subprocess, YAML, SQLite, or CLI
imports. MCP data is untrusted input: the local policy mapping is explicit and
annotations from a remote Server are retained only as evidence.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AliasChoices, Field, field_validator, model_validator

from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.models import ProtocolModel, ToolEffect, utc_now

MCP_SERVER_ID_PREFIX = "mcp"
MCP_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
MCP_MAX_SERVERS = 64
MCP_MAX_SERVER_BYTES = 16 * 1024
MCP_MAX_ARGV_ITEMS = 64
MCP_MAX_ARGV_ITEM_BYTES = 4096
MCP_MAX_REMOTE_TOOL_NAME = 128
MCP_MAX_REMOTE_TOOLS = 256
MCP_MAX_SCHEMA_BYTES = 16 * 1024
MCP_MAX_DESCRIPTION_BYTES = 4096
MCP_MAX_DIAGNOSTIC_BYTES = 2048
MCP_MAX_SNAPSHOT_BYTES = 64 * 1024
MCP_DEFAULT_TIMEOUT_MS = 15_000
MCP_MIN_TIMEOUT_MS = 100
MCP_MAX_TIMEOUT_MS = 300_000
MCP_SERVER_ID_PATTERN = re.compile(r"^mcp_[a-z0-9][a-z0-9_-]{0,63}$")
MCP_CREDENTIAL_REF_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
MCP_REMOTE_NAME_PATTERN = re.compile(r"^\S{1,128}$")
MCP_DIGEST_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)(?:api[_-]?key|authorization|password|secret|token)\s*[:=]"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"),
)
_PACKAGE_RUNNERS = frozenset({"uvx", "npx", "pnpx", "pipx", "npm", "pnpm", "bunx"})


class McpTransport(StrEnum):
    STDIO = "stdio"


class McpCwdPolicy(StrEnum):
    WORKSPACE = "workspace"
    ABSOLUTE = "absolute"
    MANAGED = "managed"


class McpWorkspaceVisibility(StrEnum):
    NONE = "none"
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class McpLaunchRisk(StrEnum):
    NETWORK = "network"
    CREDENTIALS = "credentials"
    LOOPBACK = "loopback"
    OUTSIDE_WORKSPACE = "outside_workspace"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    GIT_WRITE = "git_write"
    EXTERNAL_EFFECT = "external_effect"


class McpToolCatalogStatus(StrEnum):
    READY = "ready"
    INVALID_SCHEMA = "invalid_schema"
    UNMAPPED = "unmapped"
    DISALLOWED = "disallowed"


class McpCatalogStatus(StrEnum):
    EMPTY = "empty"
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class McpApprovalMode(StrEnum):
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    ALLOW = "allow"


def _utf8_len(value: str) -> int:
    return len(value.encode("utf-8"))


def _reject_secret_value(value: str, *, label: str) -> str:
    for pattern in _SECRET_VALUE_PATTERNS:
        if pattern.search(value):
            raise ValueError(f"{label} must not contain secret material")
    return value


def _clean_display(value: str, *, label: str, maximum: int = 128) -> str:
    value = unicodedata.normalize("NFC", value.strip())
    if not value or _utf8_len(value) > maximum:
        raise ValueError(f"{label} must be a bounded non-empty string")
    return _reject_secret_value(value, label=label)


def validate_mcp_server_id(value: str) -> str:
    if not isinstance(value, str) or not MCP_SERVER_ID_PATTERN.fullmatch(value):
        raise ValueError("MCP server id must match mcp_[a-z0-9][a-z0-9_-]{0,63}")
    return value


def validate_credential_ref(value: str) -> str:
    if not isinstance(value, str) or not MCP_CREDENTIAL_REF_PATTERN.fullmatch(value):
        raise ValueError("MCP credential refs must be opaque names, not values")
    return value


class McpToolRiskMapping(ProtocolModel):
    """Local risk semantics; remote annotations never fill these fields."""

    remote_name: str
    effect: ToolEffect
    approval: McpApprovalMode = McpApprovalMode.REQUIRE_APPROVAL
    enabled: bool = True

    @field_validator("remote_name")
    @classmethod
    def valid_remote_name(cls, value: str) -> str:
        if not MCP_REMOTE_NAME_PATTERN.fullmatch(value):
            raise ValueError("MCP remote tool name is invalid")
        return _reject_secret_value(value, label="MCP remote tool name")


class McpToolPolicy(ProtocolModel):
    """Explicit local allowlist plus per-tool local risk mapping."""

    allowlist: tuple[str, ...] = Field(
        default=(), validation_alias=AliasChoices("allowlist", "allowed_tools")
    )
    mappings: tuple[McpToolRiskMapping, ...] = ()

    @field_validator("allowlist")
    @classmethod
    def valid_allowlist(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > MCP_MAX_REMOTE_TOOLS:
            raise ValueError("MCP tool allowlist is too large")
        cleaned = tuple(
            _reject_secret_value(value, label="MCP allowlist entry")
            for value in values
            if MCP_REMOTE_NAME_PATTERN.fullmatch(value)
        )
        if len(cleaned) != len(values) or len(set(cleaned)) != len(cleaned):
            raise ValueError("MCP tool allowlist contains an invalid or duplicate name")
        return cleaned

    @model_validator(mode="after")
    def unique_mappings(self) -> McpToolPolicy:
        names = tuple(item.remote_name for item in self.mappings)
        if len(names) != len(set(names)):
            raise ValueError("MCP risk mappings must be unique by remote tool name")
        if any(item.remote_name not in self.allowlist for item in self.mappings):
            raise ValueError("MCP risk mappings must target allowlisted tools")
        return self

    def mapping_for(self, remote_name: str) -> McpToolRiskMapping | None:
        return next((item for item in self.mappings if item.remote_name == remote_name), None)


class McpServerDefinition(ProtocolModel):
    """Frozen stdio desired state stored in an Extension YAML document."""

    server_id: str = Field(validation_alias=AliasChoices("server_id", "id"))
    display_name: str = Field(
        validation_alias=AliasChoices("display_name", "name"),
        serialization_alias="display_name",
    )
    transport: McpTransport = McpTransport.STDIO
    executable: str = Field(
        validation_alias=AliasChoices("executable", "command", "command_or_endpoint")
    )
    argv: tuple[str, ...] = Field(default=(), validation_alias=AliasChoices("argv", "args"))
    executable_kind: Literal["absolute", "managed"] = "absolute"
    cwd_policy: McpCwdPolicy = McpCwdPolicy.WORKSPACE
    cwd: str | None = None
    timeout_ms: int = Field(
        default=MCP_DEFAULT_TIMEOUT_MS,
        validation_alias=AliasChoices("timeout_ms", "timeout"),
        ge=MCP_MIN_TIMEOUT_MS,
        le=MCP_MAX_TIMEOUT_MS,
    )
    credential_refs: tuple[str, ...] = ()
    workspace_visibility: McpWorkspaceVisibility = McpWorkspaceVisibility.NONE
    requested_launch_risks: tuple[McpLaunchRisk, ...] = ()
    enabled: bool = False
    tool_policy: McpToolPolicy = Field(
        default_factory=McpToolPolicy,
        validation_alias=AliasChoices("tool_policy", "capability_policy"),
    )
    revision: int = Field(default=1, ge=1, le=2_147_483_647)
    scope: Literal["global", "workspace"] = "global"
    scope_id: str | None = None

    @field_validator("server_id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        return validate_mcp_server_id(value)

    @field_validator("display_name")
    @classmethod
    def valid_display_name(cls, value: str) -> str:
        return _clean_display(value, label="MCP display name")

    @field_validator("executable")
    @classmethod
    def valid_executable(cls, value: str) -> str:
        value = value.strip()
        if not value or "\x00" in value or not value.startswith("/"):
            raise ValueError("MCP executable must be an absolute resolved path")
        value = _reject_secret_value(value, label="MCP executable")
        if value.rstrip("/").rsplit("/", 1)[-1].casefold() in _PACKAGE_RUNNERS:
            raise ValueError("package-runner auto-download executables are not allowed")
        return value

    @field_validator("argv")
    @classmethod
    def valid_argv(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > MCP_MAX_ARGV_ITEMS:
            raise ValueError("MCP argv is too long")
        for value in values:
            if not isinstance(value, str) or not value or "\x00" in value:
                raise ValueError("MCP argv entries must be non-empty strings without NUL")
            if _utf8_len(value) > MCP_MAX_ARGV_ITEM_BYTES:
                raise ValueError("MCP argv entry is too large")
            _reject_secret_value(value, label="MCP argv")
            if value in {"--from", "--package", "--with", "@latest"}:
                raise ValueError("MCP argv must not request package auto-download")
        return values

    @field_validator("cwd")
    @classmethod
    def valid_cwd(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value or "\x00" in value or not value.startswith("/"):
            raise ValueError("MCP absolute cwd must be an absolute path")
        return _reject_secret_value(value, label="MCP cwd")

    @field_validator("credential_refs")
    @classmethod
    def valid_credential_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > 16:
            raise ValueError("MCP credential refs are too many")
        cleaned = tuple(validate_credential_ref(value) for value in values)
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("MCP credential refs must be unique")
        return cleaned

    @field_validator("requested_launch_risks")
    @classmethod
    def unique_risks(cls, values: tuple[McpLaunchRisk, ...]) -> tuple[McpLaunchRisk, ...]:
        if len(values) != len(set(values)):
            raise ValueError("MCP launch risks must be unique")
        return values

    @model_validator(mode="after")
    def valid_paths_scope_and_budget(self) -> McpServerDefinition:
        if self.cwd_policy is McpCwdPolicy.ABSOLUTE and self.cwd is None:
            raise ValueError("absolute MCP cwd policy requires cwd")
        if self.cwd_policy is McpCwdPolicy.WORKSPACE and self.cwd is not None:
            raise ValueError("workspace MCP cwd policy must not carry an absolute cwd")
        if self.scope == "global" and self.scope_id is not None:
            raise ValueError("global MCP server must not carry scope_id")
        if self.scope == "workspace" and self.scope_id is None:
            raise ValueError("workspace MCP server requires scope_id")
        if self.executable.rstrip("/").rsplit("/", 1)[-1].casefold() == "uv" and self.argv:
            if self.argv[0] in {"run", "tool", "x"}:
                raise ValueError("package-runner auto-download modes are not allowed")
        payload = canonical_json_bytes(self.model_dump(mode="json", by_alias=True))
        if len(payload) > MCP_MAX_SERVER_BYTES:
            raise ValueError("MCP server definition exceeds its byte budget")
        return self

    @property
    def argv_digest(self) -> str:
        return sha256_digest(canonical_json_bytes([self.executable, *self.argv]))

    @property
    def launch_risk_values(self) -> tuple[str, ...]:
        return tuple(item.value for item in self.requested_launch_risks)


# A descriptive constructor name used by callers that prefer "config".
McpServerConfig = McpServerDefinition


class McpRemoteTool(ProtocolModel):
    """Sanitized DTO crossing the SDK adapter boundary."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not MCP_REMOTE_NAME_PATTERN.fullmatch(value):
            raise ValueError("MCP remote tool name is invalid")
        return _reject_secret_value(value, label="MCP remote tool name")

    @field_validator("description")
    @classmethod
    def bounded_description(cls, value: str) -> str:
        value = value.strip()
        if _utf8_len(value) > MCP_MAX_DESCRIPTION_BYTES:
            raise ValueError("MCP tool description exceeds its byte budget")
        return _reject_secret_value(value, label="MCP tool description")


class McpHandshake(ProtocolModel):
    server_name: str = Field(min_length=1, max_length=128)
    server_version: str | None = Field(default=None, max_length=128)
    protocol_version: str = Field(min_length=1, max_length=64)


class McpDiscovery(ProtocolModel):
    handshake: McpHandshake
    tools: tuple[McpRemoteTool, ...] = ()
    stderr_bytes: int = Field(default=0, ge=0, le=MCP_MAX_DIAGNOSTIC_BYTES)

    @model_validator(mode="after")
    def unique_tools(self) -> McpDiscovery:
        if len(self.tools) > MCP_MAX_REMOTE_TOOLS:
            raise ValueError("MCP Server returned too many tools")
        names = tuple(item.name for item in self.tools)
        if len(names) != len(set(names)):
            raise ValueError("MCP Server returned duplicate tool names")
        return self


class McpToolCatalogEntry(ProtocolModel):
    remote_name: str
    local_name: str
    description: str = ""
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    schema_dialect: str | None = None
    input_schema_digest: str | None = None
    output_schema_digest: str | None = None
    annotations: dict[str, Any] = Field(default_factory=dict)
    status: McpToolCatalogStatus = McpToolCatalogStatus.READY
    invalid_reason: str | None = Field(default=None, max_length=128)
    risk_mapping: McpToolRiskMapping | None = None

    @field_validator("remote_name")
    @classmethod
    def valid_remote(cls, value: str) -> str:
        if not MCP_REMOTE_NAME_PATTERN.fullmatch(value):
            raise ValueError("MCP catalog remote name is invalid")
        return value

    @field_validator("local_name")
    @classmethod
    def valid_local(cls, value: str) -> str:
        if not re.fullmatch(r"mcp__[A-Za-z0-9_-]{1,59}", value):
            raise ValueError("MCP catalog local name is invalid")
        return value

    @field_validator("schema_dialect")
    @classmethod
    def valid_dialect(cls, value: str | None) -> str | None:
        if value is not None and value != MCP_SCHEMA_DIALECT:
            raise ValueError("MCP catalog schema dialect is not supported")
        return value

    @field_validator("input_schema_digest", "output_schema_digest")
    @classmethod
    def valid_digest(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[a-f0-9]{64}", value):
            raise ValueError("MCP catalog schema digest is invalid")
        return value

    @model_validator(mode="after")
    def schema_consistency(self) -> McpToolCatalogEntry:
        if self.status is McpToolCatalogStatus.READY and (
            self.input_schema is None
            or self.schema_dialect is None
            or self.input_schema_digest is None
        ):
            raise ValueError("ready MCP catalog tools require a validated input schema")
        if self.status is McpToolCatalogStatus.INVALID_SCHEMA and not self.invalid_reason:
            raise ValueError("invalid MCP catalog tools require a bounded reason")
        if self.invalid_reason:
            _reject_secret_value(self.invalid_reason, label="MCP catalog invalid reason")
        return self


class McpCatalogSnapshot(ProtocolModel):
    server_id: str
    revision: int = Field(default=1, ge=1)
    status: McpCatalogStatus = McpCatalogStatus.EMPTY
    tools: tuple[McpToolCatalogEntry, ...] = ()
    handshake: McpHandshake | None = None
    catalog_digest: str = ""
    degraded_reason: str | None = Field(default=None, max_length=128)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("server_id")
    @classmethod
    def valid_server(cls, value: str) -> str:
        return validate_mcp_server_id(value)

    @model_validator(mode="after")
    def bounded_and_digested(self) -> McpCatalogSnapshot:
        ordered = tuple(sorted(self.tools, key=lambda item: item.remote_name))
        object.__setattr__(self, "tools", ordered)
        self_tools = ordered
        names = tuple(item.remote_name for item in self_tools)
        locals_ = tuple(item.local_name for item in self_tools)
        if len(names) != len(set(names)) or len(locals_) != len(set(locals_)):
            raise ValueError("MCP catalog tool names must be unique")
        if self.status is McpCatalogStatus.DEGRADED and not self.degraded_reason:
            raise ValueError("degraded MCP catalogs require a bounded reason")
        if self.status is not McpCatalogStatus.DEGRADED and self.degraded_reason:
            raise ValueError("only degraded MCP catalogs may carry a degraded reason")
        if self.degraded_reason:
            _reject_secret_value(self.degraded_reason, label="MCP catalog degraded reason")
        payload = self.model_dump(mode="json")
        payload.pop("catalog_digest", None)
        payload.pop("created_at", None)
        digest = sha256_digest(canonical_json_bytes(payload))
        if self.catalog_digest and self.catalog_digest != digest:
            raise ValueError("MCP catalog digest does not match its content")
        object.__setattr__(self, "catalog_digest", digest)
        if len(canonical_json_bytes(self.model_dump(mode="json"))) > MCP_MAX_SNAPSHOT_BYTES:
            raise ValueError("MCP catalog snapshot exceeds its byte budget")
        return self


class McpServerProjection(ProtocolModel):
    """Safe query projection; it intentionally omits credentials and schemas."""

    server_id: str
    display_name: str
    transport: McpTransport
    executable: str
    argv_digest: str
    config_digest: str
    argv_count: int = Field(ge=0, le=MCP_MAX_ARGV_ITEMS)
    cwd_policy: McpCwdPolicy
    workspace_visibility: McpWorkspaceVisibility
    requested_launch_risks: tuple[McpLaunchRisk, ...] = ()
    enabled: bool = False
    config_revision: int = Field(ge=1)
    catalog_revision: int | None = Field(default=None, ge=1)
    catalog_status: McpCatalogStatus = McpCatalogStatus.EMPTY
    catalog_digest: str | None = None
    tool_count: int = Field(default=0, ge=0, le=MCP_MAX_REMOTE_TOOLS)
    mapped_tool_count: int = Field(default=0, ge=0, le=MCP_MAX_REMOTE_TOOLS)
    degraded_reason: str | None = Field(default=None, max_length=128)


class McpToolProjection(ProtocolModel):
    """Safe Catalog tool projection; full remote schemas are query opt-in only."""

    remote_name: str
    local_name: str
    status: McpToolCatalogStatus
    schema_dialect: str | None = None
    input_schema_digest: str | None = None
    output_schema_digest: str | None = None
    annotation_keys: tuple[str, ...] = ()
    effect: ToolEffect | None = None
    approval: McpApprovalMode | None = None
    invalid_reason: str | None = None


class McpServerInspection(ProtocolModel):
    server: McpServerProjection
    tools: tuple[McpToolProjection, ...] = ()


class McpLaunchSnapshot(ProtocolModel):
    launch_snapshot_id: str
    workspace_id: str
    agent_run_id: str
    server_id: str
    config_revision: int = Field(ge=1)
    config_digest: str
    catalog_revision: int | None = Field(default=None, ge=1)
    catalog_digest: str | None = None
    transport: McpTransport = McpTransport.STDIO
    argv_digest: str
    executable_digest: str
    cwd_policy: McpCwdPolicy
    workspace_visibility: McpWorkspaceVisibility
    network_risk: bool = False
    credential_risk: bool = False
    outside_workspace_risk: bool = False
    allowlisted_remote_tools: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)


class McpToolSnapshot(ProtocolModel):
    tool_snapshot_id: str
    launch_snapshot_id: str
    agent_run_id: str
    server_id: str
    remote_name: str
    local_name: str
    schema_dialect: str
    input_schema_digest: str
    output_schema_digest: str | None = None
    effect: ToolEffect
    approval: McpApprovalMode
    catalog_revision: int = Field(ge=1)
    recovery_declaration: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class McpResultArtifactLink(ProtocolModel):
    link_id: str
    workspace_id: str
    tool_execution_id: str
    artifact_id: str
    role: str = Field(min_length=1, max_length=64)
    created_at: datetime = Field(default_factory=utc_now)


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    result = re.sub(r"[^a-z0-9_-]+", "_", normalized).strip("_-")
    return result or "tool"


def mcp_local_tool_name(
    server_id: str,
    remote_name: str,
    *,
    used_names: frozenset[str] = frozenset(),
) -> str:
    """Return the deterministic Provider-compatible local name for one remote tool."""

    validate_mcp_server_id(server_id)
    if not MCP_REMOTE_NAME_PATTERN.fullmatch(remote_name):
        raise ValueError("MCP remote tool name is invalid")
    server_slug = _slug(server_id.removeprefix("mcp_"))
    tool_slug = _slug(remote_name)
    base = f"mcp__{server_slug}__{tool_slug}"
    digest = hashlib.sha256(f"{server_id}\x00{remote_name}".encode()).hexdigest()
    if len(base) <= 64 and base not in used_names:
        return base
    suffix = f"_{digest[:10]}"
    candidate = base[: 64 - len(suffix)] + suffix
    if candidate not in used_names:
        return candidate
    suffix = f"_{digest[:16]}"
    candidate = base[: 64 - len(suffix)] + suffix
    if candidate in used_names:
        raise ValueError("MCP local tool namespace collision cannot be resolved")
    return candidate


def mcp_server_config_digest(definition: McpServerDefinition) -> str:
    return sha256_digest(canonical_json_bytes(definition.model_dump(mode="json", by_alias=True)))


__all__ = [
    "MCP_DEFAULT_TIMEOUT_MS",
    "MCP_MAX_ARGV_ITEM_BYTES",
    "MCP_MAX_ARGV_ITEMS",
    "MCP_MAX_DIAGNOSTIC_BYTES",
    "MCP_MAX_DESCRIPTION_BYTES",
    "MCP_MAX_REMOTE_TOOL_NAME",
    "MCP_MAX_REMOTE_TOOLS",
    "MCP_MAX_SCHEMA_BYTES",
    "MCP_MAX_SERVER_BYTES",
    "MCP_SCHEMA_DIALECT",
    "McpApprovalMode",
    "McpCatalogSnapshot",
    "McpCatalogStatus",
    "McpCwdPolicy",
    "McpDiscovery",
    "McpHandshake",
    "McpLaunchRisk",
    "McpLaunchSnapshot",
    "McpRemoteTool",
    "McpResultArtifactLink",
    "McpServerConfig",
    "McpServerDefinition",
    "McpServerInspection",
    "McpServerProjection",
    "McpToolCatalogEntry",
    "McpToolCatalogStatus",
    "McpToolPolicy",
    "McpToolProjection",
    "McpToolRiskMapping",
    "McpToolSnapshot",
    "McpTransport",
    "McpWorkspaceVisibility",
    "mcp_local_tool_name",
    "mcp_server_config_digest",
    "validate_credential_ref",
    "validate_mcp_server_id",
]
