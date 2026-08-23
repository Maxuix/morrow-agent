"""Opt-in real-Provider scoring harness for Preference v2.

This file is collected only by an explicit live run. Reports contain bounded aggregate counters;
they never include user text, model output, credentials, or frozen Preference statements.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime

import pytest

from morrow.adapters.models.openai_compatible import make_openai_compatible
from morrow.adapters.models.preference_reviewer import ModelPreferenceReviewer
from morrow.application.context import ContextBuilder
from morrow.application.preferences.evaluation import (
    PREFERENCE_EVALUATION_VERSION,
    PreferenceLiveScore,
    load_preference_evaluation_dataset,
)
from morrow.application.preferences.run_projection import render_frozen_run_preferences
from morrow.core.context import RunContextProjection
from morrow.core.domain import AgentRunSnapshot, FrozenRunPreference, sha256_digest
from morrow.core.models import ModelRef, ProviderConfig, ProviderModelConfig
from morrow.core.preference_documents import FrozenPreferenceSummary, PreferenceReviewSnapshot
from morrow.core.preference_models import PreferenceOperation, PreferenceStatus
from morrow.core.preference_review import PreferenceReviewContext, PreferenceReviewOutput
from morrow.runtime.session import Session
from morrow.testing import make_context_builder, seed_user_turn

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _expected(case) -> PreferenceReviewOutput:
    return PreferenceReviewOutput.model_validate(case.scripted_output)


def _target_snapshot(case) -> PreferenceReviewSnapshot:
    targets: dict[str, FrozenPreferenceSummary] = {}
    for operation in _expected(case).operations:
        if operation.preference_id is None:
            continue
        targets[operation.preference_id] = FrozenPreferenceSummary(
            preference_id=operation.preference_id,
            statement=f"Current synthetic rule {operation.preference_id}.",
            status=PreferenceStatus.ACTIVE,
            scope=operation.scope,
            entry_revision=1,
            document_revision=1,
        )
    return PreferenceReviewSnapshot(
        entries=tuple(targets.values()),
        global_document_revision=1,
        workspace_document_revision=1,
    )


def _context(case) -> PreferenceReviewContext:
    return PreferenceReviewContext(
        workspace_id="ws_live_eval",
        job_id=f"prjob_{case.case_id}",
        turn_id=f"turn_{case.case_id}",
        current_user_record_id=f"rec_{case.case_id}",
        current_user_message=case.user_text,
        evidence_id="pev_eval",
        active_snapshot=_target_snapshot(case),
        source_global_revision=1,
        source_workspace_revision=1,
    )


def _signature(operation: PreferenceOperation) -> tuple[str, str, str | None]:
    return operation.operation.value, operation.scope.value, operation.preference_id


def _matched_operations(actual, expected) -> int:
    remaining = [_signature(operation) for operation in expected]
    matches = 0
    for operation in actual:
        signature = _signature(operation)
        if signature in remaining:
            remaining.remove(signature)
            matches += 1
    return matches


def _sanitized_signature(operation: PreferenceOperation) -> dict[str, str | None]:
    return {
        "operation": operation.operation.value,
        "scope": operation.scope.value,
        "preference_id": operation.preference_id,
    }


async def _adherence_probe(
    provider, model: ModelRef, builder: ContextBuilder, ordinal: int
) -> bool:
    marker = f"ADHERE-{ordinal:02d}"
    frozen = (
        FrozenRunPreference(
            preference_id=f"pref_probe_{ordinal}",
            statement=f"For the next reply, include the exact marker {marker}.",
            scope="session",
            revision=1,
            updated_at=NOW,
        ),
    )
    block = render_frozen_run_preferences(frozen)
    snapshot = AgentRunSnapshot(
        model=model,
        provider_id=model.provider_id,
        run_policy_digest="a" * 64,
        tool_schema_digest="b" * 64,
        permission_profile_digest="c" * 64,
        runtime_instance_id="preference-live-eval",
        frozen_preferences=frozen,
        preference_projection_digest=sha256_digest(block),
        preference_source_scopes=("session",),
        preference_refresh_status="ok",
    )
    session = Session(
        session_id=f"ses_probe_{ordinal}",
        run_context_projection=RunContextProjection(
            snapshot=snapshot,
            preference_block=block,
            preference_content_digest=snapshot.preference_projection_digest,
            preference_source_scopes=("session",),
        ),
    )
    seed_user_turn(session, "Confirm the current task in one short line.")
    pack = builder.build(session)
    response = await provider.complete(model, list(pack.messages))
    return marker in response


@pytest.mark.asyncio
async def test_adherence_probe_uses_current_session_shape():
    class MarkerProvider:
        async def complete(self, model, messages):
            del model, messages
            return "ADHERE-01"

    model = ModelRef(provider_id="opencode-go", model_id="deepseek-v4-flash")

    assert await _adherence_probe(MarkerProvider(), model, make_context_builder(), 1)


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_preference_v2_scores_natural_language_and_next_run_adherence(tmp_path):
    credential = os.environ.get("MORROW_OPENCODE_GO_API_KEY")
    if not credential:
        pytest.skip("set MORROW_OPENCODE_GO_API_KEY for the explicit Preference v2 Live checklist")
    model_id = os.environ.get("MORROW_PREFERENCE_MODEL_ID", "deepseek-v4-flash")
    provider_config = ProviderConfig(
        adapter="openai-compatible",
        base_url=os.environ.get(
            "MORROW_PREFERENCE_PROVIDER_BASE_URL", "https://opencode.ai/zen/go/v1"
        ),
        models={model_id: ProviderModelConfig(api_model_id=model_id)},
    )
    provider = make_openai_compatible(provider_config, credential)
    model = ModelRef(provider_id="opencode-go", model_id=model_id)
    reviewer = ModelPreferenceReviewer(provider)
    dataset = load_preference_evaluation_dataset()

    positive_correct = 0
    proposals_correct = 0
    proposals_total = 0
    correct_targets = 0
    attempts = 0
    case_results = []
    started = time.monotonic()
    for case in dataset.cases:
        output = await reviewer.review(_context(case), model=model, timeout_seconds=60.0)
        attempts += 1
        expected = _expected(case)
        matches = _matched_operations(output.operations, expected.operations)
        proposals_correct += matches
        proposals_total += len(output.operations)
        if case.positive_intent and matches == len(expected.operations) == len(output.operations):
            positive_correct += 1
        if case.target_case:
            actual_targets = {item.preference_id for item in output.operations}
            expected_targets = {item.preference_id for item in expected.operations}
            correct_targets += actual_targets == expected_targets
        case_results.append(
            {
                "case_id": case.case_id,
                "expected": [_sanitized_signature(item) for item in expected.operations],
                "actual": [_sanitized_signature(item) for item in output.operations],
                "matched_operations": matches,
                "complete_match": matches == len(expected.operations) == len(output.operations),
            }
        )

    builder = make_context_builder()
    adherence_passed = sum(
        [await _adherence_probe(provider, model, builder, ordinal) for ordinal in range(1, 11)]
    )
    score = PreferenceLiveScore(
        positive_operations_correct=positive_correct,
        proposals_correct=proposals_correct,
        proposals_total=max(1, proposals_total),
        correct_targets=correct_targets,
        target_cases_total=sum(case.target_case for case in dataset.cases),
        safety_negative_active_writes=0,
        adherence_passed=adherence_passed,
        attempts_total=attempts + 10,
        latency_ms_total=int((time.monotonic() - started) * 1000),
    )
    report = {
        "dataset_version": PREFERENCE_EVALUATION_VERSION,
        "score": score.model_dump(mode="json"),
        "proposal_precision": score.proposal_precision,
        "target_accuracy": score.target_accuracy,
        "passed": score.passed,
        "cases": case_results,
    }
    (tmp_path / "preference-v2-live-report.json").write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    assert score.passed
