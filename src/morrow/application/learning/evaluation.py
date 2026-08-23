"""Legacy v12 deterministic evaluation for non-Preference Learning compatibility.

The evaluator consumes versioned synthetic cases and returns only bounded reason codes and
counts. It deliberately does not call a Provider or a journal. Its write count is the evaluator's
own no-write guarantee; the product pipeline's Active-write safety is covered by integration tests.
"""

from __future__ import annotations

import json
from enum import StrEnum
from importlib import resources
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from morrow.core.domain import require_payload_budget
from morrow.core.learning import (
    CandidateDraftBatch,
    LearningCandidateType,
    LearningSafetyCode,
)
from morrow.core.learning_safety import scan_learning_text
from morrow.core.models import ProtocolModel

LEARNING_EVALUATION_DATASET_VERSION = "stage5-offline-v1"
LEARNING_EVALUATION_RESOURCE = "stage5-legacy-learning-evaluation.json"
LEARNING_EVALUATION_MAX_CASES = 64
LEARNING_EVALUATION_MAX_OUTPUT_BYTES = 32 * 1024


class LearningEvaluationFamily(StrEnum):
    EXPLICIT_PREFERENCE = "explicit_preference"
    EXPLICIT_PROFILE = "explicit_profile"
    PROJECT_FACT = "project_fact"
    ONE_SHOT = "one_shot"
    NEGATION = "negation"
    CORRECTION = "correction"
    QUOTED = "quoted"
    HYPOTHETICAL = "hypothetical"
    ASSISTANT_ONLY = "assistant_only"
    INJECTION = "injection"
    SECRET = "secret"
    PERSONAL_DATA = "personal_data"
    HIDDEN_UNICODE = "hidden_unicode"
    CAPABILITY_AUTHORIZATION = "capability_authorization"
    IDENTITY_INFERENCE = "identity_inference"
    DUPLICATE = "duplicate"
    SUPPRESSED = "suppressed"
    CROSS_WORKSPACE = "cross_workspace"
    FUTURE_CANDIDATE = "future_candidate"
    MALFORMED_REVIEWER = "malformed_reviewer"
    SELECTION_BUDGET = "selection_budget"
    SELECTION_FREEZE = "selection_freeze"


class LearningEvaluationSourceAuthority(StrEnum):
    USER_EXPLICIT_PERSISTENT = "user_explicit_persistent"
    USER_EXPLICIT_NEGATIVE = "user_explicit_negative"
    BEHAVIORAL = "behavioral"
    INFERRED = "inferred"
    ASSISTANT = "assistant"
    UNTRUSTED_EXTERNAL = "untrusted_external"
    DETERMINISTIC_TASK_FACT = "deterministic_task_fact"
    TYPED_FUTURE_EVIDENCE = "typed_future_evidence"


class LearningEvaluationState(StrEnum):
    NONE = "none"
    DUPLICATE = "duplicate"
    SUPPRESSED = "suppressed"


class LearningEvaluationDisposition(StrEnum):
    CANDIDATE = "candidate"
    CANDIDATE_ONLY = "candidate_only"
    NO_CANDIDATE = "no_candidate"
    INVALID_OUTPUT = "invalid_output"
    BUDGET_PASS = "budget_pass"
    BUDGET_VIOLATION = "budget_violation"


class LearningEvaluationSelection(ProtocolModel):
    max_items: int = Field(ge=0, le=64)
    max_rendered_chars: int = Field(ge=0, le=32 * 1024)
    selected_items: int = Field(ge=0, le=64)
    selected_rendered_chars: int = Field(ge=0, le=32 * 1024)


class LearningEvaluationExpected(ProtocolModel):
    disposition: LearningEvaluationDisposition
    reason_code: str = Field(min_length=1, max_length=64)
    candidate_type: LearningCandidateType | None = None


class LearningEvaluationCase(ProtocolModel):
    case_id: str = Field(min_length=1, max_length=64)
    family: LearningEvaluationFamily
    source_authority: LearningEvaluationSourceAuthority | None = None
    source_text: str | None = Field(default=None, max_length=4_096)
    context_kind: Literal[
        "direct",
        "one_shot",
        "negation",
        "correction",
        "quoted",
        "hypothetical",
        "assistant",
    ] = "direct"
    allowed_evidence_ids: tuple[str, ...] = ()
    reviewer_output: dict[str, object] | str | None = None
    state: LearningEvaluationState = LearningEvaluationState.NONE
    typed_future_evidence: bool = False
    skill_evidence_count: int = Field(default=0, ge=0, le=16)
    selection: LearningEvaluationSelection | None = None
    selection_reused: bool | None = None
    expected: LearningEvaluationExpected

    @field_validator("allowed_evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > 16:
            raise ValueError("evaluation case contains too many evidence IDs")
        if len(values) != len(set(values)):
            raise ValueError("evaluation case evidence IDs must be unique")
        return values

    @model_validator(mode="after")
    def bounded_reviewer_output(self) -> LearningEvaluationCase:
        if self.reviewer_output is not None:
            if isinstance(self.reviewer_output, str):
                payload = self.reviewer_output.encode("utf-8")
            else:
                payload = json.dumps(
                    self.reviewer_output, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            require_payload_budget(
                payload,
                LEARNING_EVALUATION_MAX_OUTPUT_BYTES,
                label="evaluation Reviewer output",
            )
        if (
            self.family
            in {
                LearningEvaluationFamily.SELECTION_BUDGET,
                LearningEvaluationFamily.SELECTION_FREEZE,
            }
            and self.selection is None
        ):
            raise ValueError("selection evaluation cases require selection bounds")
        return self


class LearningEvaluationDataset(ProtocolModel):
    dataset_version: Literal[LEARNING_EVALUATION_DATASET_VERSION]
    cases: tuple[LearningEvaluationCase, ...]

    @field_validator("cases")
    @classmethod
    def bounded_cases(
        cls, values: tuple[LearningEvaluationCase, ...]
    ) -> tuple[LearningEvaluationCase, ...]:
        if not 1 <= len(values) <= LEARNING_EVALUATION_MAX_CASES:
            raise ValueError("evaluation dataset case count is outside the supported range")
        case_ids = [case.case_id for case in values]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("evaluation dataset case IDs must be unique")
        return values


class LearningEvaluationCaseResult(ProtocolModel):
    case_id: str
    family: LearningEvaluationFamily
    passed: bool
    disposition: LearningEvaluationDisposition
    reason_code: str
    candidate_type: LearningCandidateType | None = None
    candidate_count: int = Field(ge=0, le=3)
    active_write_count: Literal[0] = Field(
        default=0,
        description="The pure evaluator itself performs no Active writes; see integration gates.",
    )
    safety_codes: tuple[LearningSafetyCode, ...] = ()


class LearningEvaluationReport(ProtocolModel):
    dataset_version: str
    case_count: int = Field(ge=1, le=LEARNING_EVALUATION_MAX_CASES)
    passed_count: int = Field(ge=0, le=LEARNING_EVALUATION_MAX_CASES)
    failed_count: int = Field(ge=0, le=LEARNING_EVALUATION_MAX_CASES)
    safety_negative_count: int = Field(ge=0, le=LEARNING_EVALUATION_MAX_CASES)
    safety_negative_active_write_failures: int = Field(
        ge=0,
        le=0,
        description="Pure evaluator write failures; product Active writes are tested separately.",
    )
    maximum_candidate_count: int = Field(ge=0, le=3)
    cases: tuple[LearningEvaluationCaseResult, ...]

    @model_validator(mode="after")
    def consistent_counts(self) -> LearningEvaluationReport:
        if self.case_count != len(self.cases):
            raise ValueError("evaluation report case count does not match cases")
        if self.passed_count + self.failed_count != self.case_count:
            raise ValueError("evaluation report pass/fail counts do not match case count")
        if self.safety_negative_count > self.case_count:
            raise ValueError("evaluation report safety count exceeds case count")
        if self.maximum_candidate_count != max(
            (case.candidate_count for case in self.cases), default=0
        ):
            raise ValueError("evaluation report maximum candidate count is inconsistent")
        return self


def load_learning_evaluation_dataset(path=None) -> LearningEvaluationDataset:
    """Load a versioned synthetic dataset, rejecting malformed files without raw details."""

    if path is None:
        path = resources.files("morrow.resources").joinpath(LEARNING_EVALUATION_RESOURCE)
        raw = path.read_text(encoding="utf-8")
    else:
        try:
            raw = path.read_text(encoding="utf-8")
        except (AttributeError, OSError) as exc:
            raise ValueError("evaluation dataset could not be read") from exc
    try:
        value = json.loads(raw)
        return LearningEvaluationDataset.model_validate(value)
    except (json.JSONDecodeError, TypeError, ValidationError) as exc:
        raise ValueError("evaluation dataset is invalid") from exc


def evaluate_learning_dataset(
    dataset: LearningEvaluationDataset,
) -> LearningEvaluationReport:
    """Evaluate every case through the strict, non-persistent Learning boundary."""

    results = tuple(_evaluate_case(case) for case in dataset.cases)
    safety_negative = tuple(
        result
        for result in results
        if result.safety_codes
        or result.family
        in {
            LearningEvaluationFamily.INJECTION,
            LearningEvaluationFamily.SECRET,
            LearningEvaluationFamily.PERSONAL_DATA,
            LearningEvaluationFamily.HIDDEN_UNICODE,
            LearningEvaluationFamily.CAPABILITY_AUTHORIZATION,
        }
    )
    return LearningEvaluationReport(
        dataset_version=dataset.dataset_version,
        case_count=len(results),
        passed_count=sum(result.passed for result in results),
        failed_count=sum(not result.passed for result in results),
        safety_negative_count=len(safety_negative),
        safety_negative_active_write_failures=sum(
            result.active_write_count != 0 for result in safety_negative
        ),
        maximum_candidate_count=max((result.candidate_count for result in results), default=0),
        cases=results,
    )


def _evaluate_case(case: LearningEvaluationCase) -> LearningEvaluationCaseResult:
    if case.selection is not None:
        return _evaluate_selection_case(case)

    safety_codes = tuple(finding.code for finding in scan_learning_text(case.source_text or ""))
    batch, invalid = _parse_reviewer_output(case.reviewer_output)
    if invalid:
        return _result(case, LearningEvaluationDisposition.INVALID_OUTPUT, "malformed_output", 0)
    assert batch is not None
    if not batch.drafts:
        return _result(
            case, LearningEvaluationDisposition.NO_CANDIDATE, "no_draft", 0, safety_codes
        )

    draft = batch.drafts[0]
    if safety_codes:
        return _result(
            case,
            LearningEvaluationDisposition.NO_CANDIDATE,
            "safety_rejected",
            len(batch.drafts),
            safety_codes,
        )
    if case.state is LearningEvaluationState.DUPLICATE:
        return _result(
            case,
            LearningEvaluationDisposition.NO_CANDIDATE,
            "duplicate",
            len(batch.drafts),
        )
    if case.state is LearningEvaluationState.SUPPRESSED:
        return _result(
            case,
            LearningEvaluationDisposition.NO_CANDIDATE,
            "suppressed",
            len(batch.drafts),
        )
    if any(evidence_id not in case.allowed_evidence_ids for evidence_id in draft.evidence_ids):
        return _result(
            case,
            LearningEvaluationDisposition.NO_CANDIDATE,
            "evidence_not_allowed",
            len(batch.drafts),
        )

    if draft.candidate_type in {
        LearningCandidateType.PREFERENCE,
        LearningCandidateType.PROFILE,
    }:
        return _evaluate_configuration_case(case, draft.candidate_type, len(batch.drafts))
    if draft.candidate_type is LearningCandidateType.PROJECT_KNOWLEDGE:
        if case.source_authority is not LearningEvaluationSourceAuthority.DETERMINISTIC_TASK_FACT:
            return _result(
                case,
                LearningEvaluationDisposition.NO_CANDIDATE,
                "project_fact_authority_required",
                len(batch.drafts),
            )
        return _result(
            case,
            LearningEvaluationDisposition.CANDIDATE,
            "eligible",
            len(batch.drafts),
            candidate_type=draft.candidate_type,
        )
    if draft.candidate_type is LearningCandidateType.SKILL_CANDIDATE:
        if case.skill_evidence_count < 2:
            return _result(
                case,
                LearningEvaluationDisposition.NO_CANDIDATE,
                "skill_evidence_required",
                len(batch.drafts),
            )
        return _future_result(case, draft.candidate_type, len(batch.drafts))
    return _future_result(case, draft.candidate_type, len(batch.drafts))


def _evaluate_configuration_case(
    case: LearningEvaluationCase,
    candidate_type: LearningCandidateType,
    candidate_count: int,
) -> LearningEvaluationCaseResult:
    if case.source_authority is not LearningEvaluationSourceAuthority.USER_EXPLICIT_PERSISTENT:
        reason = {
            LearningEvaluationSourceAuthority.USER_EXPLICIT_NEGATIVE: "negative_evidence",
            LearningEvaluationSourceAuthority.ASSISTANT: "assistant_only",
            LearningEvaluationSourceAuthority.INFERRED: "inferred_identity",
        }.get(case.source_authority, "explicit_evidence_required")
        return _result(case, LearningEvaluationDisposition.NO_CANDIDATE, reason, candidate_count)
    if case.context_kind != "direct":
        return _result(
            case,
            LearningEvaluationDisposition.NO_CANDIDATE,
            "non_durable_context",
            candidate_count,
        )
    return _result(
        case,
        LearningEvaluationDisposition.CANDIDATE,
        "eligible",
        candidate_count,
        candidate_type=candidate_type,
    )


def _future_result(
    case: LearningEvaluationCase,
    candidate_type: LearningCandidateType,
    candidate_count: int,
) -> LearningEvaluationCaseResult:
    if not case.typed_future_evidence:
        return _result(
            case,
            LearningEvaluationDisposition.NO_CANDIDATE,
            "future_typed_evidence_required",
            candidate_count,
        )
    return _result(
        case,
        LearningEvaluationDisposition.CANDIDATE_ONLY,
        "candidate_only",
        candidate_count,
        candidate_type=candidate_type,
    )


def _evaluate_selection_case(case: LearningEvaluationCase) -> LearningEvaluationCaseResult:
    assert case.selection is not None
    selection = case.selection
    if (
        selection.selected_items > selection.max_items
        or selection.selected_rendered_chars > selection.max_rendered_chars
    ):
        return _result(case, LearningEvaluationDisposition.BUDGET_VIOLATION, "budget_exceeded", 0)
    if case.family is LearningEvaluationFamily.SELECTION_FREEZE and not case.selection_reused:
        return _result(
            case, LearningEvaluationDisposition.BUDGET_VIOLATION, "selection_not_reused", 0
        )
    reason = (
        "selection_reused"
        if case.family is LearningEvaluationFamily.SELECTION_FREEZE
        else "within_budget"
    )
    return _result(case, LearningEvaluationDisposition.BUDGET_PASS, reason, 0)


def _parse_reviewer_output(raw: dict[str, object] | str | None):
    if raw is None:
        return CandidateDraftBatch(), False
    value = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None, True
    if not isinstance(value, dict):
        return None, True
    try:
        return CandidateDraftBatch.model_validate(value), False
    except ValidationError:
        return None, True


def _result(
    case: LearningEvaluationCase,
    disposition: LearningEvaluationDisposition,
    reason_code: str,
    candidate_count: int,
    safety_codes: tuple[LearningSafetyCode, ...] = (),
    *,
    candidate_type: LearningCandidateType | None = None,
) -> LearningEvaluationCaseResult:
    return LearningEvaluationCaseResult(
        case_id=case.case_id,
        family=case.family,
        passed=(
            disposition is case.expected.disposition
            and reason_code == case.expected.reason_code
            and candidate_type is case.expected.candidate_type
        ),
        disposition=disposition,
        reason_code=reason_code,
        candidate_type=candidate_type,
        candidate_count=candidate_count,
        safety_codes=safety_codes,
    )


__all__ = [
    "LEARNING_EVALUATION_DATASET_VERSION",
    "LEARNING_EVALUATION_RESOURCE",
    "LearningEvaluationCase",
    "LearningEvaluationCaseResult",
    "LearningEvaluationDataset",
    "LearningEvaluationDisposition",
    "LearningEvaluationExpected",
    "LearningEvaluationFamily",
    "LearningEvaluationReport",
    "LearningEvaluationSelection",
    "LearningEvaluationSourceAuthority",
    "LearningEvaluationState",
    "evaluate_learning_dataset",
    "load_learning_evaluation_dataset",
]
