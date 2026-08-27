"""Bounded, value-safe completion contracts for one AgentRun.

The models in this module describe what the runtime can prove about a run.  They
are deliberately not a business-correctness protocol: paths, validator names,
hashes and fixed reason codes are the only evidence that may cross this seam.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.models import ProtocolModel

_CODE = re.compile(r"^[a-z][a-z0-9_:-]{0,63}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_PATHS = 256
_MAX_BASELINE_ENTRIES = 256
_MAX_CODES = 32
_MAX_VALIDATIONS = 32
_MAX_EVIDENCE_CHARS = 80
_MAX_BASELINE_FILE_BYTES = 64 * 1024 * 1024


def _code(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not _CODE.fullmatch(value):
        raise ValueError(f"{field_name} must be a bounded local code")
    return value


def normalize_workspace_path(value: str) -> str:
    """Normalize a workspace-relative path without resolving or following it."""

    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError("workspace path is empty or too long")
    value = value.replace("\\", "/")
    if value.startswith("/") or "\x00" in value:
        raise ValueError("workspace path must be relative")
    if value == ".":
        return value
    parts = value.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise ValueError("workspace path contains an invalid component")
    return "/".join(parts)


def _paths(values: Iterable[str], *, field_name: str) -> tuple[str, ...]:
    result = tuple(dict.fromkeys(normalize_workspace_path(value) for value in values))
    if len(result) > _MAX_PATHS:
        raise ValueError(f"{field_name} has too many paths")
    return result


def _codes(values: Iterable[str], *, field_name: str) -> tuple[str, ...]:
    result = tuple(dict.fromkeys(_code(value, field_name=field_name) for value in values))
    if len(result) > _MAX_CODES:
        raise ValueError(f"{field_name} has too many values")
    return result


class OutcomeMode(StrEnum):
    CHANGE = "change"
    EXPLANATION = "explanation"
    UNSPECIFIED = "unspecified"


class ValidationStatus(StrEnum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    INCONCLUSIVE = "inconclusive"


class WorkspaceBaselineStatus(StrEnum):
    COMPLETE = "complete"
    TRUNCATED = "truncated"
    INCONCLUSIVE = "inconclusive"


class CompletionOutcome(StrEnum):
    PASSED = "passed"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


class CompletionBasis(StrEnum):
    VERIFIED = "verified"
    RUNTIME_EVIDENCE_WITHOUT_VERIFIER = "runtime_evidence_without_verifier"
    NOT_COMPLETED = "not_completed"
    INCONCLUSIVE = "inconclusive"


class VerifierStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class ValidationRequirement(ProtocolModel):
    """One exact validator/scope pair required by the completion contract."""

    validator_kind: str = Field(min_length=1, max_length=64)
    scope: str = "."

    @field_validator("validator_kind")
    @classmethod
    def valid_validator_kind(cls, value: str) -> str:
        return _code(value, field_name="validator kind")

    @field_validator("scope")
    @classmethod
    def valid_scope(cls, value: str) -> str:
        return normalize_workspace_path(value)

    @property
    def validator(self) -> str:
        """Compatibility spelling for callers that use the short field name."""

        return self.validator_kind


class OutcomeContract(ProtocolModel):
    """Frozen, bounded proof obligations for one task.

    The application admits either an explicit trusted declaration or the strict,
    authority-limited result of the no-tool semantic intent resolver. The model
    cannot name verifiers or bypass these validators.
    """

    mode: OutcomeMode
    requires_net_change: bool = False
    target_paths: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] | None = None
    forbidden_paths: tuple[str, ...] = ()
    required_validations: tuple[ValidationRequirement, ...] = ()
    verifier_id: str | None = Field(default=None, max_length=128)
    no_change_allowed: bool = False
    preparation_version: str = Field(default="s7p05.v1", max_length=32)
    contract_error_codes: tuple[str, ...] = Field(default=(), max_length=_MAX_CODES)

    @field_validator("target_paths", "forbidden_paths")
    @classmethod
    def valid_paths(cls, values: tuple[str, ...], info) -> tuple[str, ...]:
        return _paths(values, field_name=info.field_name)

    @field_validator("allowed_paths")
    @classmethod
    def valid_allowed_paths(cls, values: tuple[str, ...] | None) -> tuple[str, ...] | None:
        return None if values is None else _paths(values, field_name="allowed paths")

    @field_validator("verifier_id")
    @classmethod
    def valid_verifier_id(cls, value: str | None) -> str | None:
        if value is not None and not _IDENTIFIER.fullmatch(value):
            raise ValueError("verifier id is invalid")
        return value

    @field_validator("preparation_version")
    @classmethod
    def valid_preparation_version(cls, value: str) -> str:
        if not re.fullmatch(r"^[a-z][a-z0-9_.-]{0,31}$", value):
            raise ValueError("preparation version is invalid")
        return value

    @field_validator("contract_error_codes")
    @classmethod
    def valid_contract_error_codes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _codes(values, field_name="contract error codes")

    @model_validator(mode="after")
    def contract_consistency(self) -> OutcomeContract:
        if self.mode is OutcomeMode.CHANGE and not self.no_change_allowed:
            object.__setattr__(self, "requires_net_change", True)
        if self.no_change_allowed and self.requires_net_change:
            raise ValueError("no-change contract cannot require a net change")
        if self.allowed_paths is not None and not self.allowed_paths:
            raise ValueError("exclusive allowed path policy must not be empty")
        if len(self.required_validations) > _MAX_VALIDATIONS:
            raise ValueError("too many required validations")
        if len({(item.validator_kind, item.scope) for item in self.required_validations}) != len(
            self.required_validations
        ):
            raise ValueError("required validations must be unique")
        return self

    @property
    def requires_change(self) -> bool:
        return self.requires_net_change


class WorkspaceBaselineEntry(ProtocolModel):
    """A hash-only, no-follow observation of one workspace leaf."""

    path: str
    kind: Literal["file", "symlink", "missing"]
    sha256: str
    size: int = Field(ge=0, le=_MAX_BASELINE_FILE_BYTES)

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        return normalize_workspace_path(value)

    @field_validator("sha256")
    @classmethod
    def valid_sha256(cls, value: str) -> str:
        if not _DIGEST.fullmatch(value):
            raise ValueError("baseline digest must be SHA-256")
        return value


class WorkspaceBaseline(ProtocolModel):
    """Frozen safe workspace evidence used for before/after comparison."""

    status: WorkspaceBaselineStatus
    entries: tuple[WorkspaceBaselineEntry, ...] = Field(max_length=_MAX_BASELINE_ENTRIES)
    directory_paths: tuple[str, ...] = Field(default=(), max_length=_MAX_BASELINE_ENTRIES)
    repository_state: str = "unknown"
    git_head: str | None = Field(default=None, max_length=128)
    reason_code: str | None = Field(default=None, max_length=64)

    @field_validator("repository_state")
    @classmethod
    def valid_repository_state(cls, value: str) -> str:
        return _code(value, field_name="repository state")

    @field_validator("git_head")
    @classmethod
    def valid_git_head(cls, value: str | None) -> str | None:
        if value is not None and _DIGEST.fullmatch(value) is None:
            raise ValueError("git head evidence must be a SHA-256 digest")
        return value

    @field_validator("reason_code")
    @classmethod
    def valid_reason_code(cls, value: str | None) -> str | None:
        return None if value is None else _code(value, field_name="baseline reason code")

    @field_validator("directory_paths")
    @classmethod
    def valid_directory_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _paths(values, field_name="directory paths")

    @model_validator(mode="after")
    def status_consistency(self) -> WorkspaceBaseline:
        if self.status is WorkspaceBaselineStatus.COMPLETE and self.reason_code is not None:
            raise ValueError("complete baseline must not have a reason code")
        if self.status is not WorkspaceBaselineStatus.COMPLETE and self.reason_code is None:
            raise ValueError("incomplete baseline requires a reason code")
        paths = tuple(entry.path for entry in self.entries)
        if len(set(paths)) != len(paths):
            raise ValueError("workspace baseline entries must have unique paths")
        if len(set(self.directory_paths)) != len(self.directory_paths):
            raise ValueError("workspace baseline directories must have unique paths")
        return self


class ValidationCheckResult(ProtocolModel):
    requirement: ValidationRequirement
    status: ValidationStatus
    observed: bool = False
    reason_code: str | None = Field(default=None, max_length=64)

    @field_validator("reason_code")
    @classmethod
    def valid_reason_code(cls, value: str | None) -> str | None:
        return None if value is None else _code(value, field_name="validation reason code")


class CompletionCheckResult(ProtocolModel):
    """Runtime-owned completion evidence and bounded next-action guidance."""

    outcome: CompletionOutcome
    basis: CompletionBasis
    changed_paths: tuple[str, ...] = Field(max_length=_MAX_PATHS)
    target_paths: tuple[str, ...] = Field(max_length=_MAX_PATHS)
    unexpected_paths: tuple[str, ...] = Field(max_length=_MAX_PATHS)
    forbidden_paths: tuple[str, ...] = Field(max_length=_MAX_PATHS)
    unresolved_tool_count: int = Field(default=0, ge=0, le=128)
    required_validations: tuple[ValidationCheckResult, ...] = Field(max_length=_MAX_VALIDATIONS)
    known_failure_codes: tuple[str, ...] = Field(max_length=_MAX_CODES)
    verifier_status: VerifierStatus = VerifierStatus.NOT_CONFIGURED
    verifier_id: str | None = Field(default=None, max_length=128)
    baseline_status: WorkspaceBaselineStatus
    reason_codes: tuple[str, ...] = Field(max_length=_MAX_CODES)
    next_action_codes: tuple[str, ...] = Field(max_length=_MAX_CODES)

    @field_validator("changed_paths", "target_paths", "unexpected_paths", "forbidden_paths")
    @classmethod
    def valid_result_paths(cls, values: tuple[str, ...], info) -> tuple[str, ...]:
        return _paths(values, field_name=info.field_name)

    @field_validator("known_failure_codes", "reason_codes", "next_action_codes")
    @classmethod
    def valid_result_codes(cls, values: tuple[str, ...], info) -> tuple[str, ...]:
        return _codes(values, field_name=info.field_name)

    @field_validator("verifier_id")
    @classmethod
    def valid_result_verifier_id(cls, value: str | None) -> str | None:
        if value is not None and not _IDENTIFIER.fullmatch(value):
            raise ValueError("verifier id is invalid")
        return value

    @property
    def completion_outcome(self) -> CompletionOutcome:
        return self.outcome

    @property
    def completion_basis(self) -> CompletionBasis:
        return self.basis

    @property
    def passed(self) -> bool:
        return self.outcome is CompletionOutcome.PASSED

    @property
    def changed_count(self) -> int:
        return len(self.changed_paths)

    @property
    def stop_code(self):
        """Map only runtime proof failures to precise public stop codes."""

        from morrow.core.models import AgentStopCode

        if self.passed:
            return None
        if "forbidden_workspace_change" in self.reason_codes:
            return AgentStopCode.FORBIDDEN_WORKSPACE_CHANGE
        if "unexpected_workspace_change" in self.reason_codes:
            return AgentStopCode.UNEXPECTED_WORKSPACE_CHANGE
        if "unresolved_tool" in self.reason_codes:
            return AgentStopCode.UNRESOLVED_TOOL
        if "validation_missing" in self.reason_codes:
            return AgentStopCode.VALIDATION_MISSING
        if any(
            code in self.reason_codes
            for code in ("validation_failed", "validation_timeout", "validation_cancelled")
        ):
            return AgentStopCode.VALIDATION_FAILED
        if "missing_required_change" in self.reason_codes:
            return AgentStopCode.MISSING_REQUIRED_CHANGE
        if "verifier_failed" in self.reason_codes:
            return AgentStopCode.VERIFIER_FAILED
        if "known_failure" in self.reason_codes:
            return AgentStopCode.KNOWN_FAILURE
        return AgentStopCode.COMPLETION_INCONCLUSIVE


class CompletionVerifierResult(ProtocolModel):
    status: VerifierStatus
    reason_code: str | None = Field(default=None, max_length=64)

    @field_validator("reason_code")
    @classmethod
    def valid_reason_code(cls, value: str | None) -> str | None:
        return None if value is None else _code(value, field_name="verifier reason code")


class OutcomeContractCompiler:
    """Compatibility boundary for explicit, trusted completion contracts.

    Natural-language requests are intentionally not interpreted here. Production
    composition uses the model-backed ``OutcomeIntentResolver``; callers without
    one receive an unspecified contract rather than lexical guesses.
    """

    def __init__(self, workspace_root: str | Path | None = None) -> None:
        del workspace_root

    def compile(
        self, user_input: str, *, explicit: OutcomeContract | None = None
    ) -> OutcomeContract:
        if explicit is not None:
            return explicit
        del user_input
        return OutcomeContract(
            mode=OutcomeMode.UNSPECIFIED,
            no_change_allowed=True,
        )


def compile_outcome_contract(
    user_input: str,
    *,
    explicit: OutcomeContract | None = None,
    workspace_root: str | Path | None = None,
) -> OutcomeContract:
    return OutcomeContractCompiler(workspace_root=workspace_root).compile(
        user_input, explicit=explicit
    )


__all__ = [
    "CompletionBasis",
    "CompletionCheckResult",
    "CompletionOutcome",
    "CompletionVerifierResult",
    "OutcomeContract",
    "OutcomeContractCompiler",
    "OutcomeMode",
    "ValidationCheckResult",
    "ValidationRequirement",
    "ValidationStatus",
    "VerifierStatus",
    "WorkspaceBaseline",
    "WorkspaceBaselineEntry",
    "WorkspaceBaselineStatus",
    "compile_outcome_contract",
    "normalize_workspace_path",
]
