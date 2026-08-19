"""Recovery reports, decisions, and the pure execution classifier.

Subplan 39 owns classification and user-guided reconciliation. The classifier is
pure over durable rows and current observations. It never infers safety from
``ToolEffect``, a missing PID, a missing temp root, elapsed time, or process
visibility.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import (
    AGENT_RUN_ID_PREFIX,
    COMMAND_ID_PREFIX,
    DIGEST_PATTERN,
    SESSION_ID_PREFIX,
    TURN_ID_PREFIX,
    WORKSPACE_ID_PREFIX,
    canonical_json_bytes,
    refuse_secret_material,
    require_payload_budget,
    sha256_digest,
    validate_prefixed_id,
)
from morrow.core.execution import (
    RECOVERY_REPORT_MAX_BYTES,
    TOOL_EXECUTION_ID_PREFIX,
    EffectClass,
    FileMutationEvidence,
    MissingCompletionPolicy,
    RecoveryClassification,
    ToolExecutionState,
    ToolRecoveryDeclaration,
)
from morrow.core.models import ProtocolModel, utc_now

RECOVERY_REPORT_ID_PREFIX = "rrp"
RECOVERY_ITEM_ID_PREFIX = "rit"
RECOVERY_DECISION_ID_PREFIX = "rdc"


class RecoveryReportStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    QUARANTINED = "quarantined"


class RecoveryResolution(StrEnum):
    """User-guided outcomes. Resume is report-level after blocking items close."""

    ACKNOWLEDGE = "acknowledge"
    RETRY = "retry"
    ABORT = "abort"
    QUARANTINE = "quarantine"
    RESUME = "resume"


class FileObservation(StrEnum):
    MATCHES_EXPECTED = "matches_expected"
    MATCHES_BEFORE = "matches_before"
    MISSING = "missing"
    THIRD_PARTY = "third_party"
    EVIDENCE_MISSING = "evidence_missing"
    NOT_APPLICABLE = "not_applicable"


class RecoveryDecisionError(ValueError):
    """Illegal or conflicting recovery resolution."""


def _valid_digest(value: str) -> str:
    if not DIGEST_PATTERN.match(value):
        raise ValueError("value must be a SHA-256 hex digest")
    return value


class RecoveryEvidence(ProtocolModel):
    """Sanitized facts shown to the operator. Hashes are identity, not secrets."""

    execution_state: ToolExecutionState
    effect_class: EffectClass
    observation: FileObservation | None = None
    relative_paths: tuple[str, ...] = ()
    summary: tuple[str, ...] = ()

    @field_validator("summary")
    @classmethod
    def bounded_summary(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > 16:
            raise ValueError("recovery summary has too many lines")
        cleaned: list[str] = []
        for value in values:
            line = " ".join(value.split())
            if line:
                cleaned.append(line[:240])
        return tuple(cleaned)


class RecoveryItem(ProtocolModel):
    item_id: str
    report_id: str
    tool_execution_id: str
    tool_name: str
    classification: RecoveryClassification
    allowed_resolutions: tuple[RecoveryResolution, ...]
    evidence: RecoveryEvidence
    blocking: bool = True
    resolution: RecoveryResolution | None = None

    @field_validator("item_id")
    @classmethod
    def valid_item_id(cls, value: str) -> str:
        return validate_prefixed_id(value, RECOVERY_ITEM_ID_PREFIX)

    @field_validator("report_id")
    @classmethod
    def valid_report_id(cls, value: str) -> str:
        return validate_prefixed_id(value, RECOVERY_REPORT_ID_PREFIX)

    @field_validator("tool_execution_id")
    @classmethod
    def valid_execution_id(cls, value: str) -> str:
        return validate_prefixed_id(value, TOOL_EXECUTION_ID_PREFIX)


class RecoveryReport(ProtocolModel):
    report_id: str
    workspace_id: str
    session_id: str
    turn_id: str | None = None
    agent_run_id: str | None = None
    status: RecoveryReportStatus = RecoveryReportStatus.OPEN
    items: tuple[RecoveryItem, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None

    @field_validator("report_id")
    @classmethod
    def valid_report_id(cls, value: str) -> str:
        return validate_prefixed_id(value, RECOVERY_REPORT_ID_PREFIX)

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, WORKSPACE_ID_PREFIX)

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("turn_id")
    @classmethod
    def valid_turn_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, TURN_ID_PREFIX)

    @field_validator("agent_run_id")
    @classmethod
    def valid_agent_run_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, AGENT_RUN_ID_PREFIX)

    @model_validator(mode="after")
    def enforce_budget(self) -> RecoveryReport:
        payload = canonical_json_bytes(self.model_dump(mode="json"))
        require_payload_budget(payload, RECOVERY_REPORT_MAX_BYTES, label="RecoveryReport")
        refuse_secret_material(payload, label="RecoveryReport")
        return self

    @property
    def blocking_open(self) -> tuple[RecoveryItem, ...]:
        return tuple(item for item in self.items if item.blocking and item.resolution is None)


class RecoveryDecision(ProtocolModel):
    decision_id: str
    report_id: str
    item_id: str | None = None
    resolution: RecoveryResolution
    command_id: str
    request_digest: str
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("decision_id")
    @classmethod
    def valid_decision_id(cls, value: str) -> str:
        return validate_prefixed_id(value, RECOVERY_DECISION_ID_PREFIX)

    @field_validator("report_id")
    @classmethod
    def valid_report_id(cls, value: str) -> str:
        return validate_prefixed_id(value, RECOVERY_REPORT_ID_PREFIX)

    @field_validator("item_id")
    @classmethod
    def valid_item_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, RECOVERY_ITEM_ID_PREFIX)

    @field_validator("command_id")
    @classmethod
    def valid_command_id(cls, value: str) -> str:
        return validate_prefixed_id(value, COMMAND_ID_PREFIX)

    @field_validator("request_digest")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        return _valid_digest(value)


class RecoveryReceipt(ProtocolModel):
    session_id: str
    command_id: str
    request_digest: str
    report_id: str
    item_id: str | None = None
    resolution: RecoveryResolution
    kind: Literal["accepted", "replay", "conflict"] = "accepted"

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        return validate_prefixed_id(value, SESSION_ID_PREFIX)

    @field_validator("command_id")
    @classmethod
    def valid_command_id(cls, value: str) -> str:
        return validate_prefixed_id(value, COMMAND_ID_PREFIX)

    @field_validator("request_digest")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        return _valid_digest(value)


def allowed_resolutions(
    classification: RecoveryClassification,
    declaration: ToolRecoveryDeclaration,
) -> tuple[RecoveryResolution, ...]:
    abortable = (RecoveryResolution.ABORT, RecoveryResolution.QUARANTINE)
    if classification is RecoveryClassification.COMPLETED:
        return (RecoveryResolution.ACKNOWLEDGE,)
    if classification is RecoveryClassification.SAFE_TO_RETRY:
        return abortable
    if classification is RecoveryClassification.NEVER_STARTED:
        if declaration.missing_handler_completed is MissingCompletionPolicy.OUTCOME_UNKNOWN:
            return (RecoveryResolution.ACKNOWLEDGE, *abortable)
        return abortable
    if classification is RecoveryClassification.REQUIRES_RECONCILIATION:
        return (RecoveryResolution.ACKNOWLEDGE, *abortable)
    return (RecoveryResolution.ACKNOWLEDGE, *abortable)


def classify_execution(
    *,
    state: ToolExecutionState,
    declaration: ToolRecoveryDeclaration,
    observations: tuple[FileObservation, ...] = (),
) -> RecoveryClassification:
    if state is ToolExecutionState.CLOSED:
        return RecoveryClassification.COMPLETED
    if state is ToolExecutionState.HANDLER_COMPLETED:
        return RecoveryClassification.COMPLETED
    if state in {ToolExecutionState.PREPARED, ToolExecutionState.AWAITING_APPROVAL}:
        return RecoveryClassification.NEVER_STARTED
    if state is not ToolExecutionState.EXECUTING:
        return RecoveryClassification.OUTCOME_UNKNOWN
    policy = declaration.missing_handler_completed
    if policy is MissingCompletionPolicy.OUTCOME_UNKNOWN:
        return RecoveryClassification.OUTCOME_UNKNOWN
    if policy is MissingCompletionPolicy.SAFE_TO_RETRY:
        return RecoveryClassification.SAFE_TO_RETRY
    if observations:
        return classify_file_observations(observations)
    return RecoveryClassification.REQUIRES_RECONCILIATION


def classify_file_observations(observations: tuple[FileObservation, ...]) -> RecoveryClassification:
    if not observations:
        return RecoveryClassification.REQUIRES_RECONCILIATION
    if any(item is FileObservation.NOT_APPLICABLE for item in observations):
        return RecoveryClassification.OUTCOME_UNKNOWN
    if all(item is FileObservation.MATCHES_EXPECTED for item in observations):
        return RecoveryClassification.COMPLETED
    if all(item is FileObservation.MATCHES_BEFORE for item in observations):
        return RecoveryClassification.SAFE_TO_RETRY
    if any(
        item
        in {FileObservation.THIRD_PARTY, FileObservation.MISSING, FileObservation.EVIDENCE_MISSING}
        for item in observations
    ):
        return RecoveryClassification.OUTCOME_UNKNOWN
    return RecoveryClassification.REQUIRES_RECONCILIATION


def observe_file(evidence: FileMutationEvidence, *, root: Path) -> FileObservation:
    if evidence.expected_after_sha256 is None and evidence.before_sha256 is None:
        return FileObservation.EVIDENCE_MISSING
    target = root.joinpath(*evidence.relative_path.split("/"))
    try:
        exists = target.exists()
    except OSError:
        return FileObservation.EVIDENCE_MISSING
    if not exists:
        if not evidence.existed_before:
            return FileObservation.MATCHES_BEFORE
        return FileObservation.MISSING
    try:
        if target.is_symlink() or not target.is_file():
            return FileObservation.THIRD_PARTY
        raw = target.read_bytes()
    except OSError:
        return FileObservation.EVIDENCE_MISSING
    digest = sha256_digest(raw)
    if evidence.expected_after_sha256 is not None and digest == evidence.expected_after_sha256:
        if evidence.expected_size is not None and len(raw) != evidence.expected_size:
            return FileObservation.THIRD_PARTY
        return FileObservation.MATCHES_EXPECTED
    if evidence.before_sha256 is not None and digest == evidence.before_sha256:
        return FileObservation.MATCHES_BEFORE
    return FileObservation.THIRD_PARTY


def observe_config(
    *, source_revision: int, expected_revision: int | None, actual_revision: int | None
):
    if actual_revision is None:
        return FileObservation.EVIDENCE_MISSING
    if expected_revision is not None and actual_revision == expected_revision:
        return FileObservation.MATCHES_EXPECTED
    if actual_revision == source_revision:
        return FileObservation.MATCHES_BEFORE
    return FileObservation.THIRD_PARTY


def apply_item_resolution(item: RecoveryItem, resolution: RecoveryResolution) -> RecoveryItem:
    if item.resolution is not None:
        if item.resolution is resolution:
            return item
        raise RecoveryDecisionError("recovery resolution mismatch")
    if resolution is RecoveryResolution.RESUME:
        raise RecoveryDecisionError("resume is a report-level resolution")
    if resolution is RecoveryResolution.RETRY:
        raise RecoveryDecisionError("linked retry is not available in this recovery surface")
    if resolution not in item.allowed_resolutions:
        raise RecoveryDecisionError("recovery resolution is not allowed")
    return item.model_copy(update={"resolution": resolution})


def apply_report_resume(report: RecoveryReport, *, now: datetime | None = None) -> RecoveryReport:
    if report.status is not RecoveryReportStatus.OPEN:
        if report.status is RecoveryReportStatus.RESOLVED:
            return report
        raise RecoveryDecisionError("recovery report cannot resume")
    if report.blocking_open:
        raise RecoveryDecisionError("blocking recovery items are still open")
    stamp = now or utc_now()
    return report.model_copy(update={"status": RecoveryReportStatus.RESOLVED, "resolved_at": stamp})


def decision_digest(*, report_id: str, item_id: str | None, resolution: RecoveryResolution) -> str:
    return sha256_digest(
        canonical_json_bytes(
            {"report_id": report_id, "item_id": item_id, "resolution": resolution.value}
        )
    )
