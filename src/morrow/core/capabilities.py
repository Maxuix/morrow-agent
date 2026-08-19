"""Local-only capability, policy, and per-run fact contracts.

These values deliberately live outside the Provider message models.  They describe
what Morrow may do locally; they are never serialized into a ToolDefinition,
conversation message, public event, or model request.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import ConfigDict, Field, field_validator

from morrow.core.models import ProtocolModel, ToolEffect

_LOCAL_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RELATIVE_PATH_LIMIT = 512
_PREVIEW_LINE_LIMIT = 240


def _clean_code(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not _LOCAL_CODE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase local code")
    return value


def _clean_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _RELATIVE_PATH_LIMIT:
        raise ValueError("relative path is empty or too long")
    if "\x00" in value or value.startswith(("/", "\\")):
        raise ValueError("relative path must not be absolute or contain NUL")
    return value


def _clean_preview_line(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("preview lines must be strings")
    value = " ".join(value.split())
    if not value:
        raise ValueError("preview lines must not be empty")
    return value[:_PREVIEW_LINE_LIMIT]


class AccessScope(StrEnum):
    WORKSPACE = "workspace"
    FULL_ACCESS = "full_access"


class ApprovalMode(StrEnum):
    MANUAL = "manual"
    AUTO_SAFE = "auto_safe"
    AUTO = "auto"


class ProcessIsolation(StrEnum):
    HOST = "host"
    NATIVE_SANDBOX = "native_sandbox"


class PermissionPreset(StrEnum):
    MANUAL = "manual"
    AUTO_SAFE = "auto-safe"
    AUTO_SANDBOXED = "auto-sandboxed"
    FULL_ACCESS_MANUAL = "full-access-manual"


class LocalCapabilityModel(ProtocolModel):
    """Strict immutable base for local contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PermissionProfile(LocalCapabilityModel):
    """The three independent permission dimensions frozen for one Session."""

    access_scope: AccessScope = AccessScope.WORKSPACE
    approval_mode: ApprovalMode = ApprovalMode.MANUAL
    process_isolation: ProcessIsolation = ProcessIsolation.HOST

    @classmethod
    def from_preset(cls, preset: PermissionPreset) -> PermissionProfile:
        if preset is PermissionPreset.MANUAL:
            return cls()
        if preset is PermissionPreset.AUTO_SAFE:
            return cls(approval_mode=ApprovalMode.AUTO_SAFE)
        if preset is PermissionPreset.AUTO_SANDBOXED:
            return cls(
                approval_mode=ApprovalMode.AUTO,
                process_isolation=ProcessIsolation.NATIVE_SANDBOX,
            )
        if preset is PermissionPreset.FULL_ACCESS_MANUAL:
            return cls(
                access_scope=AccessScope.FULL_ACCESS,
                approval_mode=ApprovalMode.MANUAL,
                process_isolation=ProcessIsolation.HOST,
            )
        raise ValueError("unsupported permission preset")


class WorkspaceCapability(LocalCapabilityModel):
    """Confirmed workspace identity and the effective read-only intersection."""

    workspace_id: str = Field(min_length=1, max_length=128)
    root: Path
    read_only: bool = False

    @field_validator("root")
    @classmethod
    def absolute_root(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("workspace root must be absolute")
        return value


class OperationKind(StrEnum):
    INTERNAL_READ = "internal_read"
    WORKSPACE_READ = "workspace_read"
    WORKSPACE_WRITE = "workspace_write"
    CONFIGURATION_WRITE = "configuration_write"
    PROCESS = "process"
    GIT_READ = "git_read"
    DESTRUCTIVE = "destructive"
    EXTERNAL_EFFECT = "external_effect"


class RiskFlag(StrEnum):
    OUTSIDE_WORKSPACE = "outside_workspace"
    NETWORK = "network"
    LOOPBACK = "loopback"
    CREDENTIAL_ACCESS = "credential_access"
    PROTECTED_RESOURCE = "protected_resource"
    DESTRUCTIVE = "destructive"
    GIT_WRITE = "git_write"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    HOST_PROCESS = "host_process"
    NATIVE_SANDBOX = "native_sandbox"
    FULL_ACCESS = "full_access"
    STALE_REVISION = "stale_revision"
    MUTATION_APPROVAL_REQUIRED = "mutation_approval_required"


class OperationIntent(LocalCapabilityModel):
    """Sanitized, preflighted description used by the local capability policy."""

    kind: OperationKind
    effect: ToolEffect = ToolEffect.NONE
    relative_paths: tuple[str, ...] = ()
    command_class: str | None = Field(default=None, max_length=64)
    risk_flags: tuple[RiskFlag, ...] = ()
    requires_host: bool = False
    requires_sandbox: bool = False
    preview_summary: tuple[str, ...] = ()

    @field_validator("relative_paths")
    @classmethod
    def valid_relative_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_clean_relative_path(value) for value in values)

    @field_validator("command_class")
    @classmethod
    def valid_command_class(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean_code(value, field_name="command_class")

    @field_validator("preview_summary")
    @classmethod
    def bounded_preview(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > 16:
            raise ValueError("preview summary has too many lines")
        return tuple(_clean_preview_line(value) for value in values)


class PolicyVerdict(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class PolicyDecision(LocalCapabilityModel):
    verdict: PolicyVerdict
    reason_codes: tuple[str, ...] = ()
    preview_summary: tuple[str, ...] = ()

    @field_validator("reason_codes")
    @classmethod
    def valid_reason_codes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > 8:
            raise ValueError("too many policy reason codes")
        return tuple(_clean_code(value, field_name="reason code") for value in values)

    @field_validator("preview_summary")
    @classmethod
    def valid_preview_summary(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > 16:
            raise ValueError("preview summary has too many lines")
        return tuple(_clean_preview_line(value) for value in values)


class ToolFactHeader(LocalCapabilityModel):
    """Fields shared by every local fact variant."""

    call_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=64)
    ordinal: int = Field(ge=1, le=128)
    relative_paths: tuple[str, ...] = ()
    approval_verdict: PolicyVerdict

    @field_validator("relative_paths")
    @classmethod
    def valid_fact_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_clean_relative_path(value) for value in values)


class ChangeToolFact(ToolFactHeader):
    kind: Literal["change"] = "change"
    operation: str = Field(min_length=1, max_length=32)
    before_revision: str | None = Field(default=None, max_length=128)
    after_revision: str | None = Field(default=None, max_length=128)
    edit_count: int = Field(default=0, ge=0, le=128)
    changed_lines: int = Field(ge=0, le=100_000)
    changed_bytes: int = Field(ge=0, le=10_000_000)
    diff_truncated: bool = False
    change_set_id: str | None = Field(default=None, max_length=128)

    @field_validator("operation")
    @classmethod
    def valid_operation(cls, value: str) -> str:
        return _clean_code(value, field_name="operation")


class CommandToolFact(ToolFactHeader):
    kind: Literal["command"] = "command"
    command_class: str
    status: str = Field(min_length=1, max_length=32)
    exit_code: int | None = None
    signal: int | None = Field(default=None, ge=1, le=255)
    duration_ms: int = Field(ge=0, le=120_000)
    output_truncated: bool = False
    redaction_flags: tuple[str, ...] = ()
    redaction_count: int = Field(default=0, ge=0, le=100_000)

    @field_validator("command_class", "status")
    @classmethod
    def valid_command_fields(cls, value: str, info) -> str:
        return _clean_code(value, field_name=info.field_name)

    @field_validator("redaction_flags")
    @classmethod
    def valid_redaction_flags(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > 16:
            raise ValueError("too many redaction flags")
        return tuple(_clean_code(value, field_name="redaction flag") for value in values)


class GitToolFact(ToolFactHeader):
    kind: Literal["git"] = "git"
    repository_state: str = Field(min_length=1, max_length=32)
    diff_truncated: bool = False

    @field_validator("repository_state")
    @classmethod
    def valid_repository_state(cls, value: str) -> str:
        return _clean_code(value, field_name="repository state")


ToolFact = Annotated[
    ChangeToolFact | CommandToolFact | GitToolFact,
    Field(discriminator="kind"),
]


class RunMetricsSnapshot(LocalCapabilityModel):
    """Optional process-local metrics; never part of Provider or persisted state."""

    run_id: str = Field(min_length=1, max_length=128)
    finish_reason: str = Field(min_length=1, max_length=32)
    tool_calls: int = Field(ge=0, le=128)
    successful_tool_calls: int = Field(ge=0, le=128)
    failed_tool_calls: int = Field(ge=0, le=128)
    approval_requests: int = Field(ge=0, le=128)
    approval_rejections: int = Field(ge=0, le=128)
    timeout_count: int = Field(ge=0, le=128)
    cancellation_count: int = Field(ge=0, le=128)
    changed_file_count: int = Field(ge=0, le=128)
    validation_outcome: str = Field(pattern=r"^(not_run|passed|failed|timeout|cancelled)$")


@dataclass
class ToolRunContext:
    """Mutable only for ordered, process-local accumulation of sanitized facts."""

    run_id: str
    session_id: str
    _facts: list[ToolFact] = field(default_factory=list, repr=False)
    _change_sets: dict[str, object] = field(default_factory=dict, repr=False)
    _tool_calls: int = field(default=0, repr=False)
    _successful_tool_calls: int = field(default=0, repr=False)
    _failed_tool_calls: int = field(default=0, repr=False)
    _approval_requests: int = field(default=0, repr=False)
    _approval_rejections: int = field(default=0, repr=False)
    _timeout_count: int = field(default=0, repr=False)
    _cancellation_count: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        if not self.run_id.strip() or not self.session_id.strip():
            raise ValueError("ToolRunContext identifiers must be non-empty")

    @property
    def facts(self) -> tuple[ToolFact, ...]:
        return tuple(self._facts)

    def record(self, facts: Iterable[ToolFact]) -> None:
        for fact in facts:
            if not isinstance(fact, (ChangeToolFact, CommandToolFact, GitToolFact)):
                raise TypeError("ToolRunContext accepts only validated ToolFact values")
            self._facts.append(fact)

    def retain_change_set(self, change_set_id: str, value: object) -> None:
        if not change_set_id.strip():
            raise ValueError("change set id must be non-empty")
        self._change_sets[change_set_id] = value

    def change_set(self, change_set_id: str) -> object | None:
        return self._change_sets.get(change_set_id)

    @property
    def change_sets(self) -> tuple[object, ...]:
        return tuple(self._change_sets.values())

    def note_tool_outcome(self, *, ok: bool, error_code: str | None = None) -> None:
        self._tool_calls += 1
        if ok:
            self._successful_tool_calls += 1
        else:
            self._failed_tool_calls += 1
        code = getattr(error_code, "value", error_code)
        if code in {"approval_rejected", "approval_unavailable"}:
            self._approval_requests += 1
            if code == "approval_rejected":
                self._approval_rejections += 1
        elif any(fact.approval_verdict is PolicyVerdict.REQUIRE_APPROVAL for fact in self._facts):
            self._approval_requests += 1
        if code == "timeout":
            self._timeout_count += 1
        if code == "cancelled":
            self._cancellation_count += 1

    def metrics(self, finish_reason: str) -> RunMetricsSnapshot:
        command_facts = tuple(fact for fact in self._facts if isinstance(fact, CommandToolFact))
        if any(fact.status == "timed_out" for fact in command_facts):
            validation = "timeout"
        elif any(
            fact.status == "signaled" or fact.exit_code not in {None, 0} for fact in command_facts
        ):
            validation = "failed"
        elif any(fact.status == "exited" for fact in command_facts):
            validation = "passed"
        else:
            validation = "cancelled" if self._cancellation_count else "not_run"
        changed_paths = {
            path
            for fact in self._facts
            if isinstance(fact, ChangeToolFact)
            for path in fact.relative_paths
        }
        approval_facts = sum(
            fact.approval_verdict is PolicyVerdict.REQUIRE_APPROVAL for fact in self._facts
        )
        return RunMetricsSnapshot(
            run_id=self.run_id,
            finish_reason=finish_reason,
            tool_calls=self._tool_calls,
            successful_tool_calls=self._successful_tool_calls,
            failed_tool_calls=self._failed_tool_calls,
            approval_requests=min(128, self._approval_requests + approval_facts),
            approval_rejections=self._approval_rejections,
            timeout_count=min(
                128,
                self._timeout_count + sum(fact.status == "timed_out" for fact in command_facts),
            ),
            cancellation_count=self._cancellation_count,
            changed_file_count=min(128, len(changed_paths)),
            validation_outcome=validation,
        )


@dataclass(frozen=True)
class ToolCallContext:
    """One tool call's local execution budget and run association."""

    run: ToolRunContext
    call_id: str
    tool_name: str
    ordinal: int
    total: int
    result_limit: int
    approval_verdict: PolicyVerdict = PolicyVerdict.ALLOW

    def __post_init__(self) -> None:
        if not self.call_id.strip() or not self.tool_name.strip():
            raise ValueError("ToolCallContext identifiers must be non-empty")
        if self.ordinal < 1 or self.total < self.ordinal or self.result_limit < 1:
            raise ValueError("invalid ToolCallContext bounds")


@dataclass(frozen=True)
class ToolHandlerOutcome:
    """Typed handler return with an ordinary payload and optional local facts."""

    payload: object
    facts: tuple[ToolFact, ...] = ()

    def __post_init__(self) -> None:
        facts = tuple(self.facts)
        if any(
            not isinstance(fact, (ChangeToolFact, CommandToolFact, GitToolFact)) for fact in facts
        ):
            raise TypeError("ToolHandlerOutcome facts must be validated ToolFact values")
        object.__setattr__(self, "facts", facts)


class SensitiveResourcePolicy(Protocol):
    """Local content-safety contract shared by future content-producing services."""

    def is_protected_path(self, relative_path: str) -> bool: ...

    def is_protected_content(self, content: bytes) -> bool: ...


@dataclass(frozen=True)
class DefaultSensitiveResourcePolicy:
    """Conservative local policy for credential and private-key disclosure."""

    protected_exact_names: tuple[str, ...] = (
        ".git-credentials",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials",
        "credential",
        "secrets",
        "secret",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
    )
    protected_suffixes: tuple[str, ...] = (".pem", ".key", ".p12", ".pfx")
    example_names: tuple[str, ...] = (".env.example", ".env.sample", ".env.template")

    def is_protected_path(self, relative_path: str) -> bool:
        normalized = relative_path.replace("\\", "/").strip("/")
        if not normalized:
            return True
        parts = normalized.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            return True
        for raw_part in parts:
            part = raw_part.casefold()
            if part in {".git", ".morrow"}:
                return True
            if part in self.protected_exact_names:
                return True
            if part == ".env" or (part.startswith(".env.") and part not in self.example_names):
                return True
            if part.endswith(self.protected_suffixes):
                return True
        return False

    def is_protected_content(self, content: bytes) -> bool:
        head = content[:16_384].lower()
        private_key_markers = (
            b"private key-----",
            b"-----begin pgp private key block-----",
            b"openssh private key",
        )
        if any(marker in head for marker in private_key_markers):
            return True
        credential_markers = (
            b"aws_access_key_id=",
            b"aws_secret_access_key=",
            b"client_secret=",
            b'private_key":',
        )
        return any(marker in head for marker in credential_markers)
