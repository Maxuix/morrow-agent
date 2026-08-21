"""Opt-in live smoke evaluation for the no-tool Learning Reviewer."""

from __future__ import annotations

import json
import os

import pytest

from morrow.adapters.models.learning_reviewer import ModelLearningReviewer
from morrow.adapters.models.openai_compatible import make_openai_compatible
from morrow.core.models import ModelRef, ProviderConfig, ProviderModelConfig
from test_stage5_learning_reviewer import _context


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_learning_reviewer_uses_only_a_synthetic_context(tmp_path):
    credential = os.environ.get("MORROW_OPENCODE_GO_API_KEY")
    if not credential:
        pytest.skip("set MORROW_OPENCODE_GO_API_KEY for the explicit Stage 5 Live checklist")
    model_id = os.environ.get("MORROW_LEARNING_MODEL_ID", "deepseek-v4-flash")
    provider_config = ProviderConfig(
        adapter="openai-compatible",
        base_url=os.environ.get(
            "MORROW_LEARNING_PROVIDER_BASE_URL", "https://opencode.ai/zen/go/v1"
        ),
        models={model_id: ProviderModelConfig(api_model_id=model_id)},
    )
    provider = make_openai_compatible(provider_config, credential)
    reviewer = ModelLearningReviewer(provider)
    result = await reviewer.review(
        _context(),
        model=ModelRef(provider_id="opencode-go", model_id=model_id),
        timeout_seconds=30.0,
    )

    report = {
        "candidate_count": len(result.drafts),
        "repair_used": reviewer.last_repair_used,
        "prompt_version": reviewer.prompt_version,
        "schema_version": reviewer.schema_version,
    }
    (tmp_path / "learning-live-report.json").write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    assert len(result.drafts) <= 3
