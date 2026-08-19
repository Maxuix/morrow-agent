"""Durable Session/Task/Turn/AgentRun contracts for Stage 4 Subplan 37.

These types are persistence-facing Core models. They do not write history and do
not depend on sqlite3. ConversationLog remains the message-grammar authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.models import (
    ModelRef,
    Preferences,
    Profile,
    ProtocolModel,
    utc_now,
)

WORKSPACE_ID_PREFIX = "ws"
SESSION_ID_PREFIX = "ses"
TASK_RUN_ID_PREFIX = "task"
TASK_TRANSITION_ID_PREFIX = "ttr"
TASK_OUTCOME_ID_PREFIX = "out"
ARTIFACT_ID_PREFIX = "art"
TURN_ID_PREFIX = "turn"
AGENT_RUN_ID_PREFIX = "arun"
CONVERSATION_RECORD_ID_PREFIX = "rec"
COMMAND_ID_PREFIX = "cmd"
CHECKPOINT_ID_PREFIX = "chk"
CAPABILITY_GRANT_ID_PREFIX = "grt"
PERMISSION_SNAPSHOT_ID_PREFIX = "psnap"

ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
CLIENT_MESSAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
DIGEST_PATTERN = re.compile(r"^[a-f0-9]{64}$")

CONVERSATION_RECORD_MAX_BYTES = 256 * 1024
AGENT_RUN_SNAPSHOT_MAX_BYTES = 64 * 1024
ERROR_DETAIL_MAX_BYTES = 4 * 1024
TASK_OUTCOME_MAX_BYTES = 64 * 1024
SECRET_NEEDLES = ("api_key", "authorization", "password", "credential", "sk-")


class SessionLifecycle(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class SessionHealth(StrEnum):
    OK = "ok"
    NEEDS_RECOVERY = "needs_recovery"
    QUARANTINED = "quarantined"
    READ_ONLY = "read_only"


class TaskRunStatus(StrEnum):
    """Durable foreground TaskRun states owned by Subplan 40."""

    OPEN = "open"
    READY_FOR_ACCEPTANCE = "ready_for_acceptance"
    ACCEPTED = "accepted"
    CANCELLED = "cancelled"
    FAILED = "failed"
    ABANDONED = "abandoned"

    @property
    def is_terminal(self) -> bool:
        return self in {
            TaskRunStatus.ACCEPTED,
            TaskRunStatus.CANCELLED,
            TaskRunStatus.ABANDONED,
        }


TASK_TERMINAL_STATUSES = frozenset(
    {
        TaskRunStatus.ACCEPTED,
        TaskRunStatus.CANCELLED,
        TaskRunStatus.ABANDONED,
    }
)

LEGAL_TASK_TRANSITIONS: frozenset[tuple[TaskRunStatus, TaskRunStatus]] = frozenset(
    {
        (TaskRunStatus.OPEN, TaskRunStatus.READY_FOR_ACCEPTANCE),
        (TaskRunStatus.READY_FOR_ACCEPTANCE, TaskRunStatus.OPEN),
        (TaskRunStatus.READY_FOR_ACCEPTANCE, TaskRunStatus.ACCEPTED),
        (TaskRunStatus.OPEN, TaskRunStatus.CANCELLED),
        (TaskRunStatus.OPEN, TaskRunStatus.FAILED),
        (TaskRunStatus.OPEN, TaskRunStatus.ABANDONED),
        (TaskRunStatus.READY_FOR_ACCEPTANCE, TaskRunStatus.CANCELLED),
        (TaskRunStatus.READY_FOR_ACCEPTANCE, TaskRunStatus.ABANDONED),
        (TaskRunStatus.FAILED, TaskRunStatus.OPEN),
    }
)


class TaskRunTransitionError(ValueError):
    """Raised when a TaskRun state transition is not part of the contract."""


def validate_task_transition(
    current: TaskRunStatus, target: TaskRunStatus
) -> tuple[TaskRunStatus, TaskRunStatus]:
    if (current, target) not in LEGAL_TASK_TRANSITIONS:
        raise TaskRunTransitionError(
            f"illegal TaskRun transition: {current.value} -> {target.value}"
        )
    return current, target


class TaskOutcomeTrigger(StrEnum):
    ACCEPTANCE = "acceptance"
    SNAPSHOT = "snapshot"
    TERMINAL_CLOSE = "terminal_close"


class TaskOutcomeEvidenceKind(StrEnum):
    TURN = "turn"
    CONVERSATION_RECORD = "conversation_record"
    AGENT_RUN = "agent_run"
    TOOL_EXECUTION = "tool_execution"
    TASK_TRANSITION = "task_transition"
    ARTIFACT = "artifact"
    CHECKPOINT = "checkpoint"


_OUTCOME_REFERENCE_PREFIXES = {
    TaskOutcomeEvidenceKind.TURN: TURN_ID_PREFIX,
    TaskOutcomeEvidenceKind.CONVERSATION_RECORD: CONVERSATION_RECORD_ID_PREFIX,
    TaskOutcomeEvidenceKind.AGENT_RUN: AGENT_RUN_ID_PREFIX,
    TaskOutcomeEvidenceKind.TOOL_EXECUTION: "tex",
    TaskOutcomeEvidenceKind.TASK_TRANSITION: TASK_TRANSITION_ID_PREFIX,
    TaskOutcomeEvidenceKind.ARTIFACT: "art",
    TaskOutcomeEvidenceKind.CHECKPOINT: "chk",
}


class TaskOutcomeEvidenceRef(ProtocolModel):
    """A typed, opaque link to an already durable Stage 4 record."""

    kind: TaskOutcomeEvidenceKind
    reference_id: str
    role: str = Field(default="evidence", min_length=1, max_length=64)

    @field_validator("reference_id")
    @classmethod
    def valid_reference_id(cls, value: str) -> str:
        if not ID_PATTERN.match(value):
            raise ValueError("outcome evidence reference must be a bounded opaque token")
        return value

    @model_validator(mode="after")
    def matches_kind(self) -> TaskOutcomeEvidenceRef:
        expected = _OUTCOME_REFERENCE_PREFIXES[self.kind]
        validate_prefixed_id(self.reference_id, expected)
        return self

    @property
    def id(self) -> str:
        return self.reference_id


class ArtifactReference(ProtocolModel):
    """A bounded link to immutable bytes managed by the Artifact Store."""

    artifact_id: str
    role: str = Field(default="evidence", min_length=1, max_length=64)

    @field_validator("artifact_id")
    @classmethod
    def valid_artifact_id(cls, value: str) -> str:
        return validate_prefixed_id(value, ARTIFACT_ID_PREFIX)

    @field_validator("role")
    @classmethod
    def valid_role(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned or len(cleaned) > 64:
            raise ValueError("artifact reference role must be bounded and non-empty")
        return cleaned


def _bounded_outcome_lines(
    values: tuple[str, ...], *, label: str, maximum: int = 64, line_limit: int = 512
) -> tuple[str, ...]:
    if len(values) > maximum:
        raise ValueError(f"{label} contains too many items")
    cleaned: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f"{label} items must be strings")
        line = " ".join(value.split())
        if line:
            cleaned.append(line[:line_limit])
    return tuple(cleaned)


class TaskOutcome(ProtocolModel):
    """Immutable, deterministic evidence emitted at an explicit milestone."""

    outcome_id: str
    workspace_id: str
    session_id: str
    task_run_id: str
    version: int = Field(ge=1)
    trigger: TaskOutcomeTrigger
    task_status: TaskRunStatus
    summary: str = Field(min_length=1, max_length=2_000)
    goal_reference: TaskOutcomeEvidenceRef | None = None
    changed_paths: tuple[str, ...] = ()
    validation_facts: tuple[str, ...] = ()
    side_effects: tuple[str, ...] = ()
    unresolved_items: tuple[str, ...] = ()
    completion_basis: tuple[str, ...] = ()
    feedback: tuple[str, ...] = ()
    evidence_refs: tuple[TaskOutcomeEvidenceRef, ...] = ()
    artifact_refs: tuple[ArtifactReference, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("outcome_id")
    @classmethod
    def valid_outcome_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TASK_OUTCOME_ID_PREFIX)

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

    @field_validator("summary")
    @classmethod
    def clean_summary(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("outcome summary must not be empty")
        return cleaned

    @field_validator("changed_paths")
    @classmethod
    def clean_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > 128:
            raise ValueError("outcome contains too many changed paths")
        cleaned: list[str] = []
        for value in values:
            parts = value.split("/")
            if (
                not value
                or len(value) > 512
                or value.startswith(("/", "\\"))
                or "\\" in value
                or "\x00" in value
                or any(not part or part in {".", ".."} for part in parts)
            ):
                raise ValueError("outcome paths must be bounded workspace-relative paths")
            cleaned.append(value)
        return tuple(cleaned)

    @field_validator(
        "validation_facts", "side_effects", "unresolved_items", "completion_basis", "feedback"
    )
    @classmethod
    def clean_evidence_lines(cls, values: tuple[str, ...], info) -> tuple[str, ...]:
        return _bounded_outcome_lines(values, label=info.field_name)

    @field_validator("artifact_refs")
    @classmethod
    def bounded_artifact_refs(
        cls, values: tuple[ArtifactReference, ...]
    ) -> tuple[ArtifactReference, ...]:
        if len(values) > 64:
            raise ValueError("outcome contains too many artifact references")
        seen: set[tuple[str, str]] = set()
        for value in values:
            key = (value.artifact_id, value.role)
            if key in seen:
                raise ValueError("outcome artifact references must be unique")
            seen.add(key)
        return values

    @model_validator(mode="after")
    def enforce_budget_and_redaction(self) -> TaskOutcome:
        payload = canonical_json_bytes(self.model_dump(mode="json"))
        require_payload_budget(payload, TASK_OUTCOME_MAX_BYTES, label="TaskOutcome")
        refuse_secret_material(payload, label="TaskOutcome")
        return self


# The durable name is useful at adapter boundaries while keeping the public domain name concise.
DurableTaskOutcome = TaskOutcome


class TaskCommandDisposition(StrEnum):
    ACCEPTED = "accepted"
    REPLAY = "replay"
    CONFLICT = "conflict"


class TaskCommandReceipt(ProtocolModel):
    """Idempotency evidence for retry-sensitive foreground Task commands."""

    command_id: str
    workspace_id: str
    session_id: str
    task_run_id: str | None = None
    operation: str = Field(min_length=1, max_length=64)
    request_digest: str
    disposition: TaskCommandDisposition = TaskCommandDisposition.ACCEPTED
    result_task_run_id: str | None = None
    outcome_id: str | None = None
    task_status: TaskRunStatus | None = None
    row_version: int | None = Field(default=None, ge=1)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("command_id")
    @classmethod
    def valid_command_id(cls, value: str) -> str:
        return validate_prefixed_id(value, COMMAND_ID_PREFIX)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, WORKSPACE_ID_PREFIX)

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("task_run_id", "result_task_run_id")
    @classmethod
    def valid_optional_task_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, TASK_RUN_ID_PREFIX)

    @field_validator("outcome_id")
    @classmethod
    def valid_optional_outcome_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, TASK_OUTCOME_ID_PREFIX)

    @field_validator("request_digest")
    @classmethod
    def valid_request_digest(cls, value: str) -> str:
        if not DIGEST_PATTERN.match(value):
            raise ValueError("request digest must be a SHA-256 hex digest")
        return value


class TurnSubmitDisposition(StrEnum):
    ACCEPTED_OPEN = "accepted_open"
    ACCEPTED_CLOSED = "accepted_closed"
    RECOVERY = "recovery"
    CONFLICT = "conflict"


class SequenceNamespace(StrEnum):
    CONVERSATION_POSITION = "conversation_position"
    RUNTIME_EVENT_SEQUENCE = "runtime_event_sequence"
    APPLICATION_EVENT_CURSOR = "application_event_cursor"


def utf8_size(value: str | bytes) -> int:
    if isinstance(value, bytes):
        return len(value)
    return len(value.encode("utf-8"))


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def sha256_digest(value: str | bytes) -> str:
    payload = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def require_payload_budget(payload: bytes, maximum: int, *, label: str) -> bytes:
    if len(payload) > maximum:
        raise ValueError(f"{label} exceeds the durable payload budget")
    return payload


def refuse_secret_material(payload: str | bytes, *, label: str) -> None:
    text = payload if isinstance(payload, str) else payload.decode("utf-8")
    serialized = text.casefold()
    for needle in SECRET_NEEDLES:
        if needle in serialized:
            raise ValueError(f"{label} cannot contain secret material")


def validate_prefixed_id(value: str, prefix: str) -> str:
    if not value or not value.strip():
        raise ValueError("identifier must not be empty")
    if not ID_PATTERN.match(value):
        raise ValueError("identifier must be a bounded opaque token")
    expected = f"{prefix}_"
    if not value.startswith(expected):
        raise ValueError("identifier prefix does not match its owner")
    remainder = value[len(expected) :]
    if not remainder:
        raise ValueError("identifier must include an opaque suffix")
    return value


class ConversationPosition(ProtocolModel):
    namespace: Literal[SequenceNamespace.CONVERSATION_POSITION] = (
        SequenceNamespace.CONVERSATION_POSITION
    )
    session_id: str
    value: int = Field(ge=0)

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)


class RuntimeEventSequence(ProtocolModel):
    namespace: Literal[SequenceNamespace.RUNTIME_EVENT_SEQUENCE] = (
        SequenceNamespace.RUNTIME_EVENT_SEQUENCE
    )
    turn_id: str
    value: int = Field(ge=1)

    @field_validator("turn_id")
    @classmethod
    def valid_turn_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TURN_ID_PREFIX)


class ApplicationEventCursor(ProtocolModel):
    namespace: Literal[SequenceNamespace.APPLICATION_EVENT_CURSOR] = (
        SequenceNamespace.APPLICATION_EVENT_CURSOR
    )
    value: int = Field(ge=0)


class SourceRevisionRef(ProtocolModel):
    kind: Literal["global_config", "workspace_profile", "workspace_preferences"]
    revision: int = Field(ge=0)
    content_sha256: str

    @field_validator("content_sha256")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        if not DIGEST_PATTERN.match(value):
            raise ValueError("source hash must be a SHA-256 hex digest")
        return value


class AgentRunSnapshot(ProtocolModel):
    """Immutable non-secret AgentRun evidence. Not a configuration authority."""

    profile: Profile | None = None
    preferences: Preferences = Field(default_factory=Preferences)
    model: ModelRef
    provider_id: str
    source_revisions: tuple[SourceRevisionRef, ...] = ()
    run_policy_digest: str
    tool_schema_digest: str
    permission_profile_digest: str
    runtime_instance_id: str

    @field_validator("provider_id", "runtime_instance_id")
    @classmethod
    def non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("snapshot field must not be empty")
        return value

    @field_validator("run_policy_digest", "tool_schema_digest", "permission_profile_digest")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        if not DIGEST_PATTERN.match(value):
            raise ValueError("snapshot digest must be a SHA-256 hex digest")
        return value

    @model_validator(mode="after")
    def enforce_budget_and_redaction(self) -> AgentRunSnapshot:
        dumped = self.model_dump(mode="json")
        payload = canonical_json_bytes(dumped)
        require_payload_budget(payload, AGENT_RUN_SNAPSHOT_MAX_BYTES, label="AgentRun snapshot")
        refuse_secret_material(payload, label="AgentRun snapshot")
        return self


class DurableSession(ProtocolModel):
    session_id: str
    workspace_id: str
    lifecycle: SessionLifecycle = SessionLifecycle.ACTIVE
    health: SessionHealth = SessionHealth.OK
    current_task_run_id: str | None = None
    conversation_position: int = Field(default=0, ge=0)
    parent_session_id: str | None = None
    parent_cut_record_id: str | None = None
    parent_cut_position: int | None = Field(default=None, ge=1)
    parent_checkpoint_id: str | None = None
    fork_reason: str | None = Field(default=None, max_length=256)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, WORKSPACE_ID_PREFIX)

    @field_validator("current_task_run_id")
    @classmethod
    def valid_task_run_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, TASK_RUN_ID_PREFIX)

    @field_validator("parent_session_id")
    @classmethod
    def valid_parent_session_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("parent_cut_record_id")
    @classmethod
    def valid_parent_cut_record_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, CONVERSATION_RECORD_ID_PREFIX)

    @field_validator("parent_checkpoint_id")
    @classmethod
    def valid_parent_checkpoint_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, CHECKPOINT_ID_PREFIX)

    @field_validator("fork_reason")
    @classmethod
    def valid_fork_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("fork reason must not be empty")
        refuse_secret_material(cleaned, label="Session fork reason")
        return cleaned

    @model_validator(mode="after")
    def quarantine_is_health_not_lifecycle(self) -> DurableSession:
        has_parent = self.parent_session_id is not None
        if has_parent != (self.parent_cut_record_id is not None):
            raise ValueError("forked Session must include parent and cut record IDs")
        if has_parent != (self.parent_cut_position is not None):
            raise ValueError("forked Session must include a parent cut position")
        if has_parent != (self.fork_reason is not None):
            raise ValueError("forked Session must include a fork reason")
        if not has_parent and self.parent_checkpoint_id is not None:
            raise ValueError("checkpoint provenance requires a parent Session")
        if has_parent and self.current_task_run_id is not None:
            raise ValueError("forked Session cannot inherit a current TaskRun")
        if not has_parent and self.fork_reason is not None:
            raise ValueError("fork reason requires a parent Session")
        if (
            self.parent_cut_position is not None
            and self.conversation_position < self.parent_cut_position
        ):
            raise ValueError("forked Session conversation position cannot precede its cut position")
        if self.lifecycle is SessionLifecycle.DELETED and self.health is SessionHealth.OK:
            return self
        return self


class DurableTaskRun(ProtocolModel):
    task_run_id: str
    session_id: str
    workspace_id: str
    status: TaskRunStatus = TaskRunStatus.OPEN
    row_version: int = Field(default=1, ge=1)
    attempt: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    accepted_at: datetime | None = None
    closed_at: datetime | None = None

    @field_validator("task_run_id")
    @classmethod
    def valid_task_run_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TASK_RUN_ID_PREFIX)

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, WORKSPACE_ID_PREFIX)


class DurableTaskRunTransition(ProtocolModel):
    """Immutable audit row for one legal TaskRun transition."""

    transition_id: str
    workspace_id: str
    session_id: str
    task_run_id: str
    from_status: TaskRunStatus | None = None
    to_status: TaskRunStatus
    reason: str = Field(min_length=1, max_length=256)
    turn_id: str | None = None
    command_id: str | None = None
    attempt: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("transition_id")
    @classmethod
    def valid_transition_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TASK_TRANSITION_ID_PREFIX)

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
    def valid_turn_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, TURN_ID_PREFIX)

    @field_validator("command_id")
    @classmethod
    def valid_command_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, COMMAND_ID_PREFIX)

    @model_validator(mode="after")
    def legal_pair(self) -> DurableTaskRunTransition:
        if self.from_status is not None:
            validate_task_transition(self.from_status, self.to_status)
        elif self.to_status is not TaskRunStatus.OPEN:
            raise TaskRunTransitionError("a new TaskRun must start in open state")
        return self


class DurableTurn(ProtocolModel):
    turn_id: str
    session_id: str
    task_run_id: str
    client_message_id: str
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("turn_id")
    @classmethod
    def valid_turn_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TURN_ID_PREFIX)

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("task_run_id")
    @classmethod
    def valid_task_run_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TASK_RUN_ID_PREFIX)

    @field_validator("client_message_id")
    @classmethod
    def valid_client_message_id(cls, value: str) -> str:
        if not CLIENT_MESSAGE_ID_PATTERN.match(value):
            raise ValueError("client_message_id must be a bounded opaque command field")
        return value


class DurableConversationRecord(ProtocolModel):
    record_id: str
    session_id: str
    conversation_position: int = Field(ge=1)
    kind: Literal["message", "terminal"]
    payload: dict[str, Any]

    @field_validator("record_id")
    @classmethod
    def valid_record_id(cls, value: str) -> str:
        return validate_prefixed_id(value, CONVERSATION_RECORD_ID_PREFIX)

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @model_validator(mode="after")
    def enforce_payload_budget(self) -> DurableConversationRecord:
        require_payload_budget(
            canonical_json_bytes(self.payload),
            CONVERSATION_RECORD_MAX_BYTES,
            label="conversation record",
        )
        return self


class DurableAgentRun(ProtocolModel):
    agent_run_id: str
    turn_id: str
    session_id: str
    snapshot: AgentRunSnapshot
    permission_snapshot_id: str | None = None
    resume_of_agent_run_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("agent_run_id")
    @classmethod
    def valid_agent_run_id(cls, value: str) -> str:
        return validate_prefixed_id(value, AGENT_RUN_ID_PREFIX)

    @field_validator("turn_id")
    @classmethod
    def valid_turn_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TURN_ID_PREFIX)

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("resume_of_agent_run_id")
    @classmethod
    def valid_resume_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, AGENT_RUN_ID_PREFIX)

    @field_validator("permission_snapshot_id")
    @classmethod
    def valid_permission_snapshot_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, PERMISSION_SNAPSHOT_ID_PREFIX)


class TurnSubmitReceipt(ProtocolModel):
    session_id: str
    client_message_id: str
    request_digest: str
    disposition: TurnSubmitDisposition
    turn_id: str | None = None
    command_id: str | None = None

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("client_message_id")
    @classmethod
    def valid_client_message_id(cls, value: str) -> str:
        if not CLIENT_MESSAGE_ID_PATTERN.match(value):
            raise ValueError("client_message_id must be a bounded opaque command field")
        return value

    @field_validator("request_digest")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        if not DIGEST_PATTERN.match(value):
            raise ValueError("request digest must be a SHA-256 hex digest")
        return value

    @field_validator("turn_id")
    @classmethod
    def valid_turn_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, TURN_ID_PREFIX)

    @field_validator("command_id")
    @classmethod
    def valid_command_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, COMMAND_ID_PREFIX)
