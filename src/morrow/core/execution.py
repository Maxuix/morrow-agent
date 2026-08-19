"""Durable ToolExecution, Approval, effect, and recovery-declaration contracts.

These types are persistence-facing Core models for Subplan 38. They do not run
handlers, write ConversationLog, or derive crash safety from ``ToolEffect``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.capabilities import AccessScope, PolicyVerdict, ProcessIsolation
from morrow.core.domain import (
    AGENT_RUN_ID_PREFIX,
    AGENT_RUN_SNAPSHOT_MAX_BYTES,
    COMMAND_ID_PREFIX,
    CONVERSATION_RECORD_ID_PREFIX,
    CONVERSATION_RECORD_MAX_BYTES,
    DIGEST_PATTERN,
    ERROR_DETAIL_MAX_BYTES,
    PERMISSION_SNAPSHOT_ID_PREFIX,
    SESSION_ID_PREFIX,
    TASK_RUN_ID_PREFIX,
    TURN_ID_PREFIX,
    WORKSPACE_ID_PREFIX,
    ArtifactReference,
    canonical_json_bytes,
    refuse_secret_material,
    require_payload_budget,
    sha256_digest,
    validate_prefixed_id,
)
from morrow.core.models import TOOL_NAME_PATTERN, ProtocolModel, utc_now
from morrow.core.permissions import (
    CAPABILITY_GRANT_ID_PREFIX,
    UNCONFINED_HOST_WARNING,
    CapabilityGrant,
    IsolationLabel,
    PermissionEvidenceError,
    PermissionSnapshot,
    assert_grant_snapshot_matches,
)

TOOL_EXECUTION_ID_PREFIX = "tex"
APPROVAL_ID_PREFIX = "apr"

PREPARED_INTENT_MAX_BYTES = 32 * 1024
TOOL_CALL_ARGUMENTS_MAX_BYTES = 128 * 1024
TOOL_RESULT_ENVELOPE_MAX_BYTES = 16 * 1024
STRUCTURED_TOOL_FACTS_MAX_BYTES = 32 * 1024
APPROVAL_RECORD_MAX_BYTES = 16 * 1024
RECOVERY_REPORT_MAX_BYTES = 64 * 1024
TASK_OUTCOME_MAX_BYTES = 64 * 1024
APPLICATION_EVENT_MAX_BYTES = 8 * 1024

DURABLE_PAYLOAD_BUDGETS: dict[str, int] = {
    "conversation_record": CONVERSATION_RECORD_MAX_BYTES,
    "prepared_intent": PREPARED_INTENT_MAX_BYTES,
    "tool_call_arguments": TOOL_CALL_ARGUMENTS_MAX_BYTES,
    "tool_result_envelope": TOOL_RESULT_ENVELOPE_MAX_BYTES,
    "structured_tool_facts": STRUCTURED_TOOL_FACTS_MAX_BYTES,
    "approval_record": APPROVAL_RECORD_MAX_BYTES,
    "error_detail": ERROR_DETAIL_MAX_BYTES,
    "agent_run_snapshot": AGENT_RUN_SNAPSHOT_MAX_BYTES,
    "recovery_report": RECOVERY_REPORT_MAX_BYTES,
    "task_outcome": TASK_OUTCOME_MAX_BYTES,
    "application_event": APPLICATION_EVENT_MAX_BYTES,
}

_RELATIVE_PATH_LIMIT = 512
_PREVIEW_LINE_LIMIT = 240
_PREVIEW_LINE_COUNT = 40
_CALL_ID_LIMIT = 128


class EffectClass(StrEnum):
    """Independent durable effect class. Not a synonym for ``ToolEffect``."""

    PURE = "pure"
    BOUNDED_READ = "bounded_read"
    DURABLE_STATE_READ = "durable_state_read"
    BOUNDED_EXTERNAL_READ = "bounded_external_read"
    RECONCILEABLE_STRUCTURED_STATE_WRITE = "reconcileable_structured_state_write"
    RECONCILEABLE_FILE_WRITE = "reconcileable_file_write"
    UNCONFINED_EXTERNAL_EFFECT = "unconfined_external_effect"
    PROCESS_EFFECT_NON_DURABLE = "process_effect_non_durable"


class MissingCompletionPolicy(StrEnum):
    """What a tool declaration says when ``handler_completed`` is missing."""

    SAFE_TO_RETRY = "safe_to_retry"
    REQUIRES_RECONCILIATION = "requires_reconciliation"
    OUTCOME_UNKNOWN = "outcome_unknown"


class RecoveryClassification(StrEnum):
    """Classifier output owned by Subplan 39; declared here so 38 can persist it."""

    NEVER_STARTED = "never_started"
    SAFE_TO_RETRY = "safe_to_retry"
    REQUIRES_RECONCILIATION = "requires_reconciliation"
    OUTCOME_UNKNOWN = "outcome_unknown"
    COMPLETED = "completed"


class ToolExecutionState(StrEnum):
    PREPARED = "prepared"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    HANDLER_COMPLETED = "handler_completed"
    CLOSED = "closed"


class ToolExecutionDisposition(StrEnum):
    PENDING = "pending"
    DENIED = "denied"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


class ApprovalResolution(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


LEGAL_EXECUTION_TRANSITIONS: frozenset[tuple[ToolExecutionState, ToolExecutionState]] = frozenset(
    {
        (ToolExecutionState.PREPARED, ToolExecutionState.AWAITING_APPROVAL),
        (ToolExecutionState.PREPARED, ToolExecutionState.EXECUTING),
        (ToolExecutionState.PREPARED, ToolExecutionState.CLOSED),
        (ToolExecutionState.AWAITING_APPROVAL, ToolExecutionState.EXECUTING),
        (ToolExecutionState.AWAITING_APPROVAL, ToolExecutionState.CLOSED),
        (ToolExecutionState.EXECUTING, ToolExecutionState.HANDLER_COMPLETED),
        (ToolExecutionState.EXECUTING, ToolExecutionState.CLOSED),
        (ToolExecutionState.HANDLER_COMPLETED, ToolExecutionState.CLOSED),
    }
)

_TERMINAL_DISPOSITIONS = frozenset(
    {
        ToolExecutionDisposition.DENIED,
        ToolExecutionDisposition.SUCCEEDED,
        ToolExecutionDisposition.FAILED,
        ToolExecutionDisposition.CANCELLED,
        ToolExecutionDisposition.INTERRUPTED,
        ToolExecutionDisposition.UNKNOWN,
    }
)


class ExecutionTransitionError(ValueError):
    """Illegal ToolExecution state or handler-entry contract violation."""


class StaleRowVersionError(ValueError):
    """Optimistic row version did not match the current record."""


class ApprovalDecisionError(ValueError):
    """Expired, mismatched, stale, denied, or already-consumed approval."""


class UnknownToolDeclarationError(ValueError):
    """Registered tool has no durable recovery declaration."""


def _clean_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _RELATIVE_PATH_LIMIT:
        raise ValueError("relative path is empty or too long")
    if "\x00" in value or value.startswith(("/", "\\")):
        raise ValueError("relative path must not be absolute or contain NUL")
    return value


def _clean_preview_line(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("preview lines must be strings")
    line = " ".join(value.split())
    if not line:
        raise ValueError("preview lines must not be empty")
    return line[:_PREVIEW_LINE_LIMIT]


def _bounded_preview(values: tuple[str, ...]) -> tuple[str, ...]:
    cleaned: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("preview lines must be strings")
        line = " ".join(value.split())
        if not line:
            continue
        cleaned.append(line[:_PREVIEW_LINE_LIMIT])
        if len(cleaned) > _PREVIEW_LINE_COUNT:
            raise ValueError("preview has too many lines")
    return tuple(cleaned)


def _valid_digest(value: str) -> str:
    if not DIGEST_PATTERN.match(value):
        raise ValueError("value must be a SHA-256 hex digest")
    return value


def _valid_tool_name(value: str) -> str:
    if not TOOL_NAME_PATTERN.match(value):
        raise ValueError("tool name must match [A-Za-z0-9_-]{1,64}")
    return value


def _valid_call_id(value: str) -> str:
    if not value.strip() or len(value) > _CALL_ID_LIMIT:
        raise ValueError("call_id must be a bounded opaque token")
    return value


def _budget_and_redact(payload: dict[str, Any] | object, maximum: int, *, label: str) -> None:
    dumped = payload if isinstance(payload, dict) else payload.model_dump(mode="json")
    encoded = canonical_json_bytes(dumped)
    require_payload_budget(encoded, maximum, label=label)
    secret_scan = dumped
    if isinstance(dumped, dict) and isinstance(dumped.get("preview"), list):
        # This fixed safety warning intentionally mentions credentials.  It is
        # policy metadata, not user-supplied secret material; scan all other
        # preview lines and all other fields normally.
        secret_scan = {
            **dumped,
            "preview": [line for line in dumped["preview"] if line != UNCONFINED_HOST_WARNING],
        }
    refuse_secret_material(canonical_json_bytes(secret_scan), label=label)


def intent_hash(intent: PreparedIntent) -> str:
    return sha256_digest(canonical_json_bytes(intent.model_dump(mode="json")))


def approval_preview_digest(preview: tuple[str, ...]) -> str:
    return sha256_digest(canonical_json_bytes(list(preview)))


def require_tool_call_arguments_budget(arguments: str) -> str:
    require_payload_budget(
        arguments.encode("utf-8"),
        TOOL_CALL_ARGUMENTS_MAX_BYTES,
        label="tool call arguments",
    )
    return arguments


class FileMutationEvidence(ProtocolModel):
    """Pre-effect and post-handler file evidence. Full diffs wait for Artifacts."""

    relative_path: str
    operation: str = Field(min_length=1, max_length=32)
    existed_before: bool
    before_sha256: str | None = None
    expected_after_sha256: str | None = None
    expected_size: int | None = Field(default=None, ge=0)
    expected_kind: Literal["file", "directory", "absent"] = "file"
    parent_exists: bool = True
    parent_is_directory: bool = True
    policy_version: str = Field(min_length=1, max_length=64)
    conflict_input_digest: str
    changed_lines: int = Field(default=0, ge=0, le=100_000)
    changed_bytes: int = Field(default=0, ge=0, le=10_000_000)
    preview_truncated: bool = False
    actual_after_sha256: str | None = None
    actual_size: int | None = Field(default=None, ge=0)
    status: str | None = Field(default=None, max_length=32)

    @field_validator("relative_path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        return _clean_relative_path(value)

    @field_validator("operation")
    @classmethod
    def valid_operation(cls, value: str) -> str:
        if not value.isascii() or " " in value:
            raise ValueError("operation must be a local code")
        return value

    @field_validator(
        "before_sha256",
        "expected_after_sha256",
        "actual_after_sha256",
        "conflict_input_digest",
    )
    @classmethod
    def valid_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _valid_digest(value)


class ConfigMutationEvidence(ProtocolModel):
    document_kind: Literal["global_config", "workspace_profile", "workspace_preferences"]
    source_revision: int = Field(ge=0)
    operation: str = Field(min_length=1, max_length=64)
    expected_fields_digest: str | None = None
    expected_revision: int | None = Field(default=None, ge=0)
    actual_revision: int | None = Field(default=None, ge=0)
    actual_fields_digest: str | None = None

    @field_validator("expected_fields_digest", "actual_fields_digest")
    @classmethod
    def valid_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _valid_digest(value)


class DurableToolFacts(ProtocolModel):
    files: tuple[FileMutationEvidence, ...] = ()
    config: ConfigMutationEvidence | None = None
    truncated: bool = False

    @model_validator(mode="after")
    def enforce_budget(self) -> DurableToolFacts:
        _budget_and_redact(self, STRUCTURED_TOOL_FACTS_MAX_BYTES, label="structured tool facts")
        return self


class PreparedIntent(ProtocolModel):
    tool_name: str
    call_id: str
    ordinal: int = Field(ge=1, le=128)
    arguments_digest: str
    schema_digest: str
    permission_context_digest: str
    effect_class: EffectClass
    requires_approval: bool = False
    policy_verdict: PolicyVerdict | None = None
    redacted_arguments: dict[str, Any] = Field(default_factory=dict)
    file_evidence: tuple[FileMutationEvidence, ...] = ()
    config_evidence: ConfigMutationEvidence | None = None
    preview: tuple[str, ...] = ()

    @field_validator("tool_name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return _valid_tool_name(value)

    @field_validator("call_id")
    @classmethod
    def valid_call(cls, value: str) -> str:
        return _valid_call_id(value)

    @field_validator("arguments_digest", "schema_digest", "permission_context_digest")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        return _valid_digest(value)

    @field_validator("preview")
    @classmethod
    def valid_preview(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _bounded_preview(values)

    @model_validator(mode="after")
    def enforce_budget(self) -> PreparedIntent:
        _budget_and_redact(self, PREPARED_INTENT_MAX_BYTES, label="prepared intent")
        return self


class HandlerResultEnvelope(ProtocolModel):
    ok: bool
    truncated: bool = False
    summary: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = None

    @field_validator("error_message")
    @classmethod
    def valid_error_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        require_payload_budget(value.encode("utf-8"), ERROR_DETAIL_MAX_BYTES, label="error detail")
        refuse_secret_material(value, label="error detail")
        return value

    @model_validator(mode="after")
    def enforce_budget(self) -> HandlerResultEnvelope:
        _budget_and_redact(self, TOOL_RESULT_ENVELOPE_MAX_BYTES, label="tool result envelope")
        return self


class DurableToolExecution(ProtocolModel):
    tool_execution_id: str
    workspace_id: str
    session_id: str
    task_run_id: str
    turn_id: str
    agent_run_id: str
    call_id: str
    ordinal: int = Field(ge=1, le=128)
    tool_name: str
    intent: PreparedIntent
    state: ToolExecutionState = ToolExecutionState.PREPARED
    disposition: ToolExecutionDisposition = ToolExecutionDisposition.PENDING
    row_version: int = Field(default=1, ge=1)
    assistant_record_id: str | None = None
    retry_of_execution_id: str | None = None
    approval_id: str | None = None
    permission_snapshot_id: str | None = None
    grant_id: str | None = None
    isolation: IsolationLabel | None = None
    cancel_requested_at: datetime | None = None
    cancel_request_reason: str | None = None
    result_envelope: HandlerResultEnvelope | None = None
    facts: DurableToolFacts | None = None
    artifact_refs: tuple[ArtifactReference, ...] = ()
    error_code: str | None = Field(default=None, max_length=64)
    error_detail: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    executing_at: datetime | None = None
    handler_completed_at: datetime | None = None
    closed_at: datetime | None = None

    @field_validator("tool_execution_id")
    @classmethod
    def valid_execution_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TOOL_EXECUTION_ID_PREFIX)

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

    @field_validator("assistant_record_id")
    @classmethod
    def valid_record_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, CONVERSATION_RECORD_ID_PREFIX)

    @field_validator("retry_of_execution_id")
    @classmethod
    def valid_retry_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, TOOL_EXECUTION_ID_PREFIX)

    @field_validator("approval_id")
    @classmethod
    def valid_approval_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, APPROVAL_ID_PREFIX)

    @field_validator("permission_snapshot_id")
    @classmethod
    def valid_permission_snapshot_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, PERMISSION_SNAPSHOT_ID_PREFIX)

    @field_validator("grant_id")
    @classmethod
    def valid_grant_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, CAPABILITY_GRANT_ID_PREFIX)

    @field_validator("call_id")
    @classmethod
    def valid_call(cls, value: str) -> str:
        return _valid_call_id(value)

    @field_validator("tool_name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return _valid_tool_name(value)

    @field_validator("error_detail")
    @classmethod
    def valid_error_detail(cls, value: str | None) -> str | None:
        if value is None:
            return None
        require_payload_budget(value.encode("utf-8"), ERROR_DETAIL_MAX_BYTES, label="error detail")
        refuse_secret_material(value, label="error detail")
        return value

    @field_validator("cancel_request_reason")
    @classmethod
    def valid_cancel_request_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        require_payload_budget(
            value.encode("utf-8"), ERROR_DETAIL_MAX_BYTES, label="cancellation reason"
        )
        refuse_secret_material(value, label="cancellation reason")
        return " ".join(value.split()) or None

    @field_validator("artifact_refs")
    @classmethod
    def bounded_artifact_refs(
        cls, values: tuple[ArtifactReference, ...]
    ) -> tuple[ArtifactReference, ...]:
        if len(values) > 64:
            raise ValueError("execution contains too many artifact references")
        if len(set(values)) != len(values):
            raise ValueError("execution artifact references must be unique")
        return values

    @model_validator(mode="after")
    def intent_matches_call(self) -> DurableToolExecution:
        if self.intent.tool_name != self.tool_name or self.intent.call_id != self.call_id:
            raise ValueError("prepared intent must match the execution call")
        if self.intent.ordinal != self.ordinal:
            raise ValueError("prepared intent ordinal must match the execution")
        if (
            self.state is ToolExecutionState.CLOSED
            and self.disposition not in _TERMINAL_DISPOSITIONS
        ):
            raise ValueError("closed executions require a terminal disposition")
        if (
            self.state is ToolExecutionState.HANDLER_COMPLETED
            and self.disposition is ToolExecutionDisposition.PENDING
        ):
            raise ValueError("handler_completed requires a non-pending disposition")
        if self.grant_id is not None and (
            self.permission_snapshot_id is None
            or self.isolation is not IsolationLabel.UNCONFINED_HOST
        ):
            raise ValueError("elevated execution requires a snapshot and unconfined_host label")
        if self.isolation is IsolationLabel.UNCONFINED_HOST and self.grant_id is None:
            raise ValueError("unconfined_host execution requires a capability grant")
        if (self.cancel_requested_at is None) != (self.cancel_request_reason is None):
            raise ValueError("cancellation request requires a bounded reason and timestamp")
        return self


class DurableApproval(ProtocolModel):
    approval_id: str
    tool_execution_id: str
    intent_hash: str
    tool_schema_digest: str
    permission_context_digest: str
    requested_scope: str = Field(min_length=1, max_length=128)
    granted_scope: str | None = Field(default=None, max_length=128)
    preview: tuple[str, ...] = ()
    preview_digest: str
    row_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    resolution: ApprovalResolution = ApprovalResolution.PENDING
    resolved_at: datetime | None = None
    consumed_at: datetime | None = None
    command_id: str | None = None
    permission_snapshot_id: str | None = None
    grant_id: str | None = None
    isolation: IsolationLabel | None = None
    revoked_at: datetime | None = None
    revocation_reason: str | None = None

    @field_validator("approval_id")
    @classmethod
    def valid_approval_id(cls, value: str) -> str:
        return validate_prefixed_id(value, APPROVAL_ID_PREFIX)

    @field_validator("tool_execution_id")
    @classmethod
    def valid_execution_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TOOL_EXECUTION_ID_PREFIX)

    @field_validator(
        "intent_hash",
        "tool_schema_digest",
        "permission_context_digest",
        "preview_digest",
    )
    @classmethod
    def valid_digest(cls, value: str) -> str:
        return _valid_digest(value)

    @field_validator("preview")
    @classmethod
    def valid_preview(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _bounded_preview(values)

    @field_validator("command_id")
    @classmethod
    def valid_command_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, COMMAND_ID_PREFIX)

    @field_validator("permission_snapshot_id")
    @classmethod
    def valid_permission_snapshot_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, PERMISSION_SNAPSHOT_ID_PREFIX)

    @field_validator("grant_id")
    @classmethod
    def valid_grant_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, CAPABILITY_GRANT_ID_PREFIX)

    @field_validator("revocation_reason")
    @classmethod
    def valid_revocation_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        require_payload_budget(
            value.encode("utf-8"), ERROR_DETAIL_MAX_BYTES, label="approval revocation reason"
        )
        refuse_secret_material(value, label="approval revocation reason")
        return " ".join(value.split()) or None

    @model_validator(mode="after")
    def enforce_contract(self) -> DurableApproval:
        if self.expires_at <= self.created_at:
            raise ValueError("approval expiry must be after creation")
        if self.preview_digest != approval_preview_digest(self.preview):
            raise ValueError("approval preview digest does not match preview")
        if self.resolution is ApprovalResolution.APPROVED and not self.granted_scope:
            raise ValueError("approved approval requires a granted scope")
        if self.consumed_at is not None and self.resolution is not ApprovalResolution.APPROVED:
            raise ValueError("only an approved approval can be consumed")
        if self.grant_id is not None and (
            self.permission_snapshot_id is None
            or self.isolation is not IsolationLabel.UNCONFINED_HOST
        ):
            raise ValueError("elevated approval requires a snapshot and unconfined_host label")
        if self.isolation is IsolationLabel.UNCONFINED_HOST and self.grant_id is None:
            raise ValueError("unconfined_host approval requires a capability grant")
        if (self.revoked_at is None) != (self.revocation_reason is None):
            raise ValueError("approval revocation requires a bounded reason and timestamp")
        if self.revoked_at is not None:
            if self.resolution is not ApprovalResolution.DENIED:
                raise ValueError("revoked approval must be denied")
            if self.resolved_at is None or self.consumed_at is not None:
                raise ValueError("revoked approval cannot be consumed")
        _budget_and_redact(self, APPROVAL_RECORD_MAX_BYTES, label="approval record")
        return self


class ToolRecoveryDeclaration(ProtocolModel):
    tool_name: str
    effect_class: EffectClass
    missing_handler_completed: MissingCompletionPolicy
    process_isolation: ProcessIsolation | None = None
    requires_frozen_confinement: bool = False

    @field_validator("tool_name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return _valid_tool_name(value)


def _declaration(
    name: str,
    effect: EffectClass,
    missing: MissingCompletionPolicy,
    *,
    isolation: ProcessIsolation | None = None,
    frozen: bool = False,
) -> ToolRecoveryDeclaration:
    return ToolRecoveryDeclaration(
        tool_name=name,
        effect_class=effect,
        missing_handler_completed=missing,
        process_isolation=isolation,
        requires_frozen_confinement=frozen,
    )


PRODUCTION_TOOL_DECLARATIONS: tuple[ToolRecoveryDeclaration, ...] = (
    _declaration("list_directory", EffectClass.BOUNDED_READ, MissingCompletionPolicy.SAFE_TO_RETRY),
    _declaration("read_file", EffectClass.BOUNDED_READ, MissingCompletionPolicy.SAFE_TO_RETRY),
    _declaration("find_files", EffectClass.BOUNDED_READ, MissingCompletionPolicy.SAFE_TO_RETRY),
    _declaration("search_text", EffectClass.BOUNDED_READ, MissingCompletionPolicy.SAFE_TO_RETRY),
    _declaration(
        "show_changes",
        EffectClass.DURABLE_STATE_READ,
        MissingCompletionPolicy.SAFE_TO_RETRY,
    ),
    _declaration(
        "git_status",
        EffectClass.BOUNDED_EXTERNAL_READ,
        MissingCompletionPolicy.SAFE_TO_RETRY,
        frozen=True,
    ),
    _declaration(
        "git_diff",
        EffectClass.BOUNDED_EXTERNAL_READ,
        MissingCompletionPolicy.SAFE_TO_RETRY,
        frozen=True,
    ),
    _declaration(
        "update_configuration",
        EffectClass.RECONCILEABLE_STRUCTURED_STATE_WRITE,
        MissingCompletionPolicy.REQUIRES_RECONCILIATION,
    ),
    _declaration(
        "apply_patch",
        EffectClass.RECONCILEABLE_FILE_WRITE,
        MissingCompletionPolicy.REQUIRES_RECONCILIATION,
    ),
    _declaration(
        "write_file",
        EffectClass.RECONCILEABLE_FILE_WRITE,
        MissingCompletionPolicy.REQUIRES_RECONCILIATION,
    ),
    _declaration(
        "promote_sandbox_changes",
        EffectClass.RECONCILEABLE_FILE_WRITE,
        MissingCompletionPolicy.REQUIRES_RECONCILIATION,
    ),
    _declaration(
        "run_command",
        EffectClass.UNCONFINED_EXTERNAL_EFFECT,
        MissingCompletionPolicy.OUTCOME_UNKNOWN,
        isolation=ProcessIsolation.HOST,
    ),
    _declaration(
        "run_command",
        EffectClass.PROCESS_EFFECT_NON_DURABLE,
        MissingCompletionPolicy.OUTCOME_UNKNOWN,
        isolation=ProcessIsolation.NATIVE_SANDBOX,
    ),
)

FIXTURE_TOOL_DECLARATIONS: tuple[ToolRecoveryDeclaration, ...] = (
    _declaration("calculate", EffectClass.PURE, MissingCompletionPolicy.SAFE_TO_RETRY),
    _declaration("lookup_record", EffectClass.PURE, MissingCompletionPolicy.SAFE_TO_RETRY),
)

PRODUCTION_TOOL_NAMES: frozenset[str] = frozenset(
    declaration.tool_name for declaration in PRODUCTION_TOOL_DECLARATIONS
)


def _declaration_index(
    declarations: tuple[ToolRecoveryDeclaration, ...],
) -> dict[tuple[str, ProcessIsolation | None], ToolRecoveryDeclaration]:
    index: dict[tuple[str, ProcessIsolation | None], ToolRecoveryDeclaration] = {}
    for declaration in declarations:
        key = (declaration.tool_name, declaration.process_isolation)
        if key in index:
            raise ValueError(f"duplicate tool declaration: {declaration.tool_name}")
        index[key] = declaration
    return index


_PRODUCTION_INDEX = _declaration_index(PRODUCTION_TOOL_DECLARATIONS)
_ALL_INDEX = _declaration_index(PRODUCTION_TOOL_DECLARATIONS + FIXTURE_TOOL_DECLARATIONS)


def tool_declaration(
    name: str,
    *,
    process_isolation: ProcessIsolation | None = None,
    production_only: bool = False,
) -> ToolRecoveryDeclaration:
    index = _PRODUCTION_INDEX if production_only else _ALL_INDEX
    if name == "run_command":
        if process_isolation is None:
            raise UnknownToolDeclarationError("run_command declaration requires process isolation")
        key = (name, process_isolation)
        declaration = index.get(key)
        if declaration is None:
            raise UnknownToolDeclarationError(f"no durable declaration for {name}")
        return declaration
    declaration = index.get((name, None))
    if declaration is None:
        raise UnknownToolDeclarationError(f"no durable declaration for {name}")
    return declaration


def missing_declarations(
    tool_names: tuple[str, ...],
    *,
    process_isolation: ProcessIsolation = ProcessIsolation.HOST,
) -> tuple[str, ...]:
    missing: list[str] = []
    for name in tool_names:
        try:
            isolation = process_isolation if name == "run_command" else None
            tool_declaration(name, process_isolation=isolation)
        except UnknownToolDeclarationError:
            missing.append(name)
    return tuple(missing)


def assert_fresh_row_version(current: int, expected: int, *, label: str) -> None:
    if current != expected:
        raise StaleRowVersionError(f"stale {label} row version")


def transition_execution(
    execution: DurableToolExecution,
    target: ToolExecutionState,
    *,
    expected_row_version: int,
    disposition: ToolExecutionDisposition | None = None,
    now: datetime | None = None,
    approval_id: str | None = None,
    result_envelope: HandlerResultEnvelope | None = None,
    facts: DurableToolFacts | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> DurableToolExecution:
    assert_fresh_row_version(execution.row_version, expected_row_version, label="tool execution")
    if (execution.state, target) not in LEGAL_EXECUTION_TRANSITIONS:
        raise ExecutionTransitionError("illegal tool execution transition")
    updates: dict[str, Any] = {
        "state": target,
        "row_version": execution.row_version + 1,
    }
    if disposition is not None:
        updates["disposition"] = disposition
    if approval_id is not None:
        updates["approval_id"] = approval_id
    if result_envelope is not None:
        updates["result_envelope"] = result_envelope
    if facts is not None:
        updates["facts"] = facts
    if error_code is not None:
        updates["error_code"] = error_code
    if error_detail is not None:
        updates["error_detail"] = error_detail
    stamp = now or utc_now()
    if target is ToolExecutionState.EXECUTING:
        updates["executing_at"] = stamp
    elif target is ToolExecutionState.HANDLER_COMPLETED:
        updates["handler_completed_at"] = stamp
        if disposition is None:
            raise ExecutionTransitionError("handler_completed requires a disposition")
    elif target is ToolExecutionState.CLOSED:
        updates["closed_at"] = stamp
        final = disposition or execution.disposition
        if final not in _TERMINAL_DISPOSITIONS:
            raise ExecutionTransitionError("closed executions require a terminal disposition")
        updates["disposition"] = final
    return execution.model_copy(update=updates)


def resolve_approval(
    approval: DurableApproval,
    *,
    approved: bool,
    expected_row_version: int,
    now: datetime,
    command_id: str | None = None,
    granted_scope: str | None = None,
) -> DurableApproval:
    assert_fresh_row_version(approval.row_version, expected_row_version, label="approval")
    if approval.consumed_at is not None:
        raise ApprovalDecisionError("approval already consumed")
    if now >= approval.expires_at:
        target = ApprovalResolution.EXPIRED
    elif approved:
        target = ApprovalResolution.APPROVED
    else:
        target = ApprovalResolution.DENIED
    if approval.resolution is not ApprovalResolution.PENDING:
        if approval.resolution is target:
            return approval
        raise ApprovalDecisionError("approval resolution mismatch")
    scope = granted_scope
    if scope is None and target is ApprovalResolution.APPROVED:
        scope = approval.requested_scope
    return approval.model_copy(
        update={
            "resolution": target,
            "resolved_at": now,
            "row_version": approval.row_version + 1,
            "command_id": command_id,
            "granted_scope": scope,
        }
    )


def consume_approval(
    approval: DurableApproval,
    *,
    expected_row_version: int,
    now: datetime,
) -> DurableApproval:
    assert_fresh_row_version(approval.row_version, expected_row_version, label="approval")
    if approval.consumed_at is not None:
        raise ApprovalDecisionError("approval already consumed")
    if approval.resolution is not ApprovalResolution.APPROVED:
        raise ApprovalDecisionError("approval is not approved")
    if now >= approval.expires_at:
        raise ApprovalDecisionError("approval expired")
    return approval.model_copy(update={"consumed_at": now, "row_version": approval.row_version + 1})


def revoke_approval(
    approval: DurableApproval,
    *,
    expected_row_version: int,
    now: datetime,
    reason: str,
) -> DurableApproval:
    assert_fresh_row_version(approval.row_version, expected_row_version, label="approval")
    if approval.revoked_at is not None:
        return approval
    if approval.resolution is not ApprovalResolution.PENDING or approval.consumed_at is not None:
        raise ApprovalDecisionError("approval is no longer pending and cannot be revoked")
    updated = approval.model_copy(
        update={
            "resolution": ApprovalResolution.DENIED,
            "resolved_at": now,
            "row_version": approval.row_version + 1,
            "revoked_at": now,
            "revocation_reason": _clean_transition_reason(reason, label="approval revocation"),
        }
    )
    return DurableApproval.model_validate(updated.model_dump(), strict=True)


def request_execution_cancellation(
    execution: DurableToolExecution,
    *,
    expected_row_version: int,
    now: datetime,
    reason: str,
) -> DurableToolExecution:
    assert_fresh_row_version(execution.row_version, expected_row_version, label="tool execution")
    if execution.cancel_requested_at is not None:
        return execution
    if execution.state is not ToolExecutionState.EXECUTING:
        return execution
    updated = execution.model_copy(
        update={
            "row_version": execution.row_version + 1,
            "cancel_requested_at": now,
            "cancel_request_reason": _clean_transition_reason(reason, label="cancellation"),
        }
    )
    return DurableToolExecution.model_validate(updated.model_dump(), strict=True)


def _clean_transition_reason(value: str, *, label: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ApprovalDecisionError(f"{label} reason is empty")
    require_payload_budget(cleaned.encode("utf-8"), ERROR_DETAIL_MAX_BYTES, label=f"{label} reason")
    refuse_secret_material(cleaned, label=f"{label} reason")
    return cleaned


def assert_handler_may_enter(
    execution: DurableToolExecution,
    approval: DurableApproval | None,
    *,
    now: datetime,
    permission_snapshot: PermissionSnapshot | None = None,
    grant: CapabilityGrant | None = None,
) -> None:
    if execution.state is not ToolExecutionState.EXECUTING:
        raise ExecutionTransitionError("handler requires executing state")
    if execution.cancel_requested_at is not None:
        raise PermissionEvidenceError("handler cancellation has been requested")
    if (
        permission_snapshot is not None
        and permission_snapshot.access_scope is AccessScope.FULL_ACCESS
        and execution.tool_name == "run_command"
        and execution.intent.effect_class is EffectClass.UNCONFINED_EXTERNAL_EFFECT
        and execution.grant_id is None
    ):
        raise PermissionEvidenceError("Full Access Host handler requires a capability grant")
    if (
        permission_snapshot is not None
        and permission_snapshot.grant_id is not None
        and execution.tool_name == "run_command"
        and execution.intent.effect_class is EffectClass.UNCONFINED_EXTERNAL_EFFECT
        and execution.intent.requires_approval
        and execution.grant_id is None
    ):
        raise PermissionEvidenceError("elevated Host handler dropped capability grant evidence")
    if not execution.intent.requires_approval:
        if execution.permission_snapshot_id is not None:
            if permission_snapshot is None:
                raise PermissionEvidenceError("permission snapshot is missing")
            if permission_snapshot.permission_snapshot_id != execution.permission_snapshot_id:
                raise PermissionEvidenceError(
                    "handler permission snapshot does not match execution"
                )
            if (
                permission_snapshot.session_id != execution.session_id
                or permission_snapshot.task_run_id != execution.task_run_id
                or permission_snapshot.turn_id != execution.turn_id
                or permission_snapshot.agent_run_id != execution.agent_run_id
            ):
                raise PermissionEvidenceError("handler permission subjects are mismatched")
            if execution.grant_id is not None:
                if grant is None:
                    raise PermissionEvidenceError("capability grant is missing")
                assert_grant_snapshot_matches(
                    permission_snapshot,
                    grant,
                    now=now,
                    workspace_id=execution.workspace_id,
                    task_run_id=execution.task_run_id,
                    agent_run_id=execution.agent_run_id,
                )
        return
    if approval is None:
        raise ApprovalDecisionError("handler requires consumed approval")
    if approval.tool_execution_id != execution.tool_execution_id:
        raise ApprovalDecisionError("approval does not match execution")
    if approval.intent_hash != intent_hash(execution.intent):
        raise ApprovalDecisionError("approval intent hash mismatch")
    if approval.tool_schema_digest != execution.intent.schema_digest:
        raise ApprovalDecisionError("approval schema digest mismatch")
    if approval.permission_context_digest != execution.intent.permission_context_digest:
        raise ApprovalDecisionError("approval permission digest mismatch")
    if (
        approval.permission_snapshot_id != execution.permission_snapshot_id
        or approval.grant_id != execution.grant_id
        or approval.isolation != execution.isolation
    ):
        raise ApprovalDecisionError("approval permission evidence mismatch")
    if execution.permission_snapshot_id is not None:
        if permission_snapshot is None:
            raise PermissionEvidenceError("permission snapshot is missing")
        if permission_snapshot.permission_snapshot_id != execution.permission_snapshot_id:
            raise PermissionEvidenceError("handler permission snapshot does not match execution")
        if (
            permission_snapshot.session_id != execution.session_id
            or permission_snapshot.task_run_id != execution.task_run_id
            or permission_snapshot.turn_id != execution.turn_id
            or permission_snapshot.agent_run_id != execution.agent_run_id
        ):
            raise PermissionEvidenceError("handler permission subjects are mismatched")
        if execution.grant_id is not None:
            if grant is None:
                raise PermissionEvidenceError("elevated handler evidence is missing")
            assert_grant_snapshot_matches(
                permission_snapshot,
                grant,
                now=now,
                workspace_id=execution.workspace_id,
                task_run_id=execution.task_run_id,
                agent_run_id=execution.agent_run_id,
            )
        elif permission_snapshot.grant_id is not None:
            raise PermissionEvidenceError("handler dropped elevated grant evidence")
    if approval.consumed_at is None:
        raise ApprovalDecisionError("approval is not consumed")
    if approval.resolution is not ApprovalResolution.APPROVED:
        raise ApprovalDecisionError("approval is not approved")
    if now >= approval.expires_at:
        raise ApprovalDecisionError("approval expired")
