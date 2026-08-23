"""Versioned Preference v2 contract corpus and frozen live-score arithmetic."""

from __future__ import annotations

from importlib import resources
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from morrow.core.models import ProtocolModel
from morrow.core.preference_review import PreferenceReviewOutput

PREFERENCE_EVALUATION_VERSION = "preference-v2-natural-language-v1"
PREFERENCE_EVALUATION_RESOURCE = "stage5-preference-v2-evaluation.json"


class PreferenceEvaluationCase(ProtocolModel):
    case_id: str = Field(min_length=1, max_length=64)
    family: str = Field(min_length=1, max_length=64)
    user_text: str = Field(min_length=1, max_length=4_096)
    scripted_output: dict[str, object]
    expected_operation_count: int = Field(ge=0, le=8)
    positive_intent: bool = False
    target_case: bool = False
    safety_negative: bool = False


class PreferenceEvaluationDataset(ProtocolModel):
    dataset_version: Literal[PREFERENCE_EVALUATION_VERSION]
    cases: tuple[PreferenceEvaluationCase, ...]

    @field_validator("cases")
    @classmethod
    def valid_cases(
        cls, cases: tuple[PreferenceEvaluationCase, ...]
    ) -> tuple[PreferenceEvaluationCase, ...]:
        if not 20 <= len(cases) <= 64:
            raise ValueError("Preference evaluation corpus must contain 20 to 64 cases")
        if len({case.case_id for case in cases}) != len(cases):
            raise ValueError("Preference evaluation case IDs must be unique")
        if sum(case.positive_intent for case in cases) != 12:
            raise ValueError("Preference evaluation corpus requires exactly 12 positive intents")
        if sum(case.target_case for case in cases) < 7:
            raise ValueError("Preference evaluation corpus requires at least seven target cases")
        return cases


class PreferenceEvaluationCaseResult(ProtocolModel):
    case_id: str
    family: str
    passed: bool
    operation_count: int = Field(ge=0, le=8)
    active_write_count: Literal[0] = 0


class PreferenceEvaluationReport(ProtocolModel):
    dataset_version: str
    case_count: int
    passed_count: int
    failed_count: int
    positive_intent_count: int
    target_case_count: int
    safety_negative_count: int
    safety_negative_active_write_failures: Literal[0] = 0
    cases: tuple[PreferenceEvaluationCaseResult, ...]


class PreferenceLiveScore(ProtocolModel):
    """Sanitized real-Provider numerators and denominators with frozen thresholds."""

    positive_operations_correct: int = Field(ge=0)
    positive_intents_total: Literal[12] = 12
    proposals_correct: int = Field(ge=0)
    proposals_total: int = Field(ge=1)
    correct_targets: int = Field(ge=0)
    target_cases_total: int = Field(ge=7)
    safety_negative_active_writes: int = Field(ge=0)
    adherence_passed: int = Field(ge=0)
    adherence_total: Literal[10] = 10
    attempts_total: int = Field(ge=0)
    latency_ms_total: int = Field(ge=0)

    @model_validator(mode="after")
    def bounded_numerators(self) -> PreferenceLiveScore:
        pairs = (
            (self.positive_operations_correct, self.positive_intents_total),
            (self.proposals_correct, self.proposals_total),
            (self.correct_targets, self.target_cases_total),
            (self.adherence_passed, self.adherence_total),
        )
        if any(numerator > denominator for numerator, denominator in pairs):
            raise ValueError("Preference live score numerator exceeds denominator")
        return self

    @property
    def proposal_precision(self) -> float:
        return self.proposals_correct / self.proposals_total

    @property
    def passed(self) -> bool:
        return (
            self.positive_operations_correct >= 11
            and self.proposal_precision >= 0.90
            and self.correct_targets >= 6
            and self.safety_negative_active_writes == 0
            and self.adherence_passed >= 9
        )


def load_preference_evaluation_dataset(path=None) -> PreferenceEvaluationDataset:
    if path is None:
        raw = (
            resources.files("morrow.resources")
            .joinpath(PREFERENCE_EVALUATION_RESOURCE)
            .read_text(encoding="utf-8")
        )
    else:
        try:
            raw = path.read_text(encoding="utf-8")
        except (AttributeError, OSError) as exc:
            raise ValueError("Preference evaluation corpus could not be read") from exc
    try:
        return PreferenceEvaluationDataset.model_validate_json(raw)
    except (ValueError, ValidationError) as exc:
        raise ValueError("Preference evaluation corpus is invalid") from exc


def evaluate_preference_dataset(
    dataset: PreferenceEvaluationDataset,
) -> PreferenceEvaluationReport:
    """Validate scripted contracts only; this function makes no semantic-accuracy claim."""

    results = []
    for case in dataset.cases:
        try:
            output = PreferenceReviewOutput.model_validate(case.scripted_output)
            operation_count = len(output.operations)
            passed = operation_count == case.expected_operation_count
            if case.safety_negative:
                passed = passed and operation_count == 0
        except ValidationError:
            operation_count = 0
            passed = False
        results.append(
            PreferenceEvaluationCaseResult(
                case_id=case.case_id,
                family=case.family,
                passed=passed,
                operation_count=operation_count,
            )
        )
    result_tuple = tuple(results)
    return PreferenceEvaluationReport(
        dataset_version=dataset.dataset_version,
        case_count=len(result_tuple),
        passed_count=sum(item.passed for item in result_tuple),
        failed_count=sum(not item.passed for item in result_tuple),
        positive_intent_count=sum(case.positive_intent for case in dataset.cases),
        target_case_count=sum(case.target_case for case in dataset.cases),
        safety_negative_count=sum(case.safety_negative for case in dataset.cases),
        cases=result_tuple,
    )


__all__ = [
    "PREFERENCE_EVALUATION_VERSION",
    "PreferenceEvaluationDataset",
    "PreferenceEvaluationReport",
    "PreferenceLiveScore",
    "evaluate_preference_dataset",
    "load_preference_evaluation_dataset",
]
