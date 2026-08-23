from __future__ import annotations

import json

import pytest

from morrow.application.preferences.evaluation import (
    PREFERENCE_EVALUATION_VERSION,
    PreferenceLiveScore,
    evaluate_preference_dataset,
    load_preference_evaluation_dataset,
)


def test_versioned_preference_corpus_passes_scripted_contracts_without_writes():
    dataset = load_preference_evaluation_dataset()
    report = evaluate_preference_dataset(dataset)

    assert dataset.dataset_version == PREFERENCE_EVALUATION_VERSION
    assert report.case_count == 22
    assert report.passed_count == report.case_count
    assert report.failed_count == 0
    assert report.positive_intent_count == 12
    assert report.target_case_count >= 7
    assert report.safety_negative_count >= 4
    assert report.safety_negative_active_write_failures == 0
    assert all(case.active_write_count == 0 for case in report.cases)
    encoded = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    assert "synthetic-token" not in encoded
    assert "user_text" not in encoded
    assert "scripted_output" not in encoded


def test_preference_corpus_covers_required_natural_language_families():
    families = {case.family for case in load_preference_evaluation_dataset().cases}
    assert {
        "add",
        "paraphrase",
        "replace",
        "remove",
        "multiple_operations",
        "global_scope",
        "correction",
        "temporary",
        "quotation",
        "hypothetical",
        "assistant_content",
        "tool_content",
        "repository_content",
        "secret",
        "injection",
        "hidden_controls",
        "capability_grant",
    } <= families


def test_live_score_thresholds_and_arithmetic_are_frozen():
    passing = PreferenceLiveScore(
        positive_operations_correct=11,
        proposals_correct=9,
        proposals_total=10,
        correct_targets=6,
        target_cases_total=7,
        safety_negative_active_writes=0,
        adherence_passed=9,
        attempts_total=12,
        latency_ms_total=12_000,
    )
    assert passing.proposal_precision == 0.9
    assert passing.target_accuracy == 6 / 7
    assert passing.passed

    assert not passing.model_copy(update={"target_cases_total": 8}).passed

    for update in (
        {"positive_operations_correct": 10},
        {"proposals_correct": 8},
        {"correct_targets": 5},
        {"safety_negative_active_writes": 1},
        {"adherence_passed": 8},
    ):
        assert not passing.model_copy(update=update).passed

    with pytest.raises(ValueError):
        PreferenceLiveScore(
            positive_operations_correct=13,
            proposals_correct=1,
            proposals_total=1,
            correct_targets=7,
            target_cases_total=7,
            safety_negative_active_writes=0,
            adherence_passed=10,
            attempts_total=1,
            latency_ms_total=1,
        )
