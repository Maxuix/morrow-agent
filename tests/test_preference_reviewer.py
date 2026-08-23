from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from morrow.adapters.models.preference_reviewer import ModelPreferenceReviewer
from morrow.application.preferences.reviewer import PreferenceReviewRunner
from morrow.core.domain import sha256_digest
from morrow.core.models import ModelErrorCode, ModelRef
from morrow.core.preference_documents import PreferenceReviewSnapshot
from morrow.core.preference_review import (
    PreferenceReviewContext,
    PreferenceReviewerError,
    PreferenceReviewOutput,
)
from morrow.testing import FixedClock, FixedIdSource, ScriptedPreferenceReviewer
from test_preference_proposals import _journal, _operation, _source

NOW = datetime(2026, 1, 1, tzinfo=UTC)
MODEL = ModelRef(provider_id="test", model_id="reviewer")


class RecordingProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def complete(self, model, messages):
        self.calls.append((model, list(messages)))
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


def _context(*, message: str = "请以后先给代码，再解释关键设计。") -> PreferenceReviewContext:
    snapshot = PreferenceReviewSnapshot()
    return PreferenceReviewContext(
        workspace_id="ws_1",
        job_id="prjob_one",
        turn_id="turn_one",
        current_user_record_id="rec_one",
        current_user_message=message,
        evidence_id="pev_one",
        active_snapshot=snapshot,
        source_global_revision=0,
        source_workspace_revision=0,
    )


def _response(*, evidence_id: str = "pev_one") -> str:
    return json.dumps(
        {
            "operations": [
                {
                    "operation": "add",
                    "scope": "workspace",
                    "statement": "先给出可运行代码，再解释关键设计。",
                    "evidence_ids": [evidence_id],
                }
            ]
        },
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_preference_reviewer_uses_one_complete_no_tool_schema_request():
    provider = RecordingProvider([_response()])
    reviewer = ModelPreferenceReviewer(provider)

    result = await reviewer.review(_context(), model=MODEL, timeout_seconds=1.0)

    assert len(result.operations) == 1
    assert len(provider.calls) == 1
    messages = provider.calls[0][1]
    assert [message.role for message in messages] == ["system", "user"]
    assert "preference-v3" in messages[0].content
    assert '"operations"' in messages[1].content
    assert '"schema_version":"preference-operations-v2"' in messages[1].content
    assert '"session"' not in messages[1].content
    assert "CandidateDraftBatch" not in messages[1].content
    assert "one operation for every independent" in messages[1].content
    assert "Use global only" in messages[1].content
    assert "quotations" in messages[1].content


@pytest.mark.asyncio
async def test_preference_reviewer_never_repairs_malformed_output():
    provider = RecordingProvider(["not json", _response()])
    reviewer = ModelPreferenceReviewer(provider)

    with pytest.raises(PreferenceReviewerError) as error:
        await reviewer.review(_context(), model=MODEL, timeout_seconds=1.0)

    assert error.value.code is ModelErrorCode.INVALID_RESPONSE
    assert error.value.category == "invalid_json"
    assert len(provider.calls) == 1
    assert reviewer.last_repair_used is False


@pytest.mark.asyncio
async def test_preference_reviewer_rejects_invented_evidence_and_session_scope():
    provider = RecordingProvider([_response(evidence_id="pev_other")])
    reviewer = ModelPreferenceReviewer(provider)
    with pytest.raises(PreferenceReviewerError) as error:
        await reviewer.review(_context(), model=MODEL, timeout_seconds=1.0)
    assert error.value.category == "invented_evidence"

    provider = RecordingProvider(
        [
            json.dumps(
                {
                    "operations": [
                        {
                            "operation": "add",
                            "scope": "session",
                            "statement": "临时规则",
                            "evidence_ids": ["pev_one"],
                        }
                    ]
                }
            )
        ]
    )
    with pytest.raises(PreferenceReviewerError) as error:
        await ModelPreferenceReviewer(provider).review(_context(), model=MODEL, timeout_seconds=1.0)
    assert error.value.category == "session_scope"


@pytest.mark.asyncio
async def test_preference_reviewer_enforces_request_budget_before_provider_call():
    provider = RecordingProvider([_response()])
    reviewer = ModelPreferenceReviewer(provider, request_char_limit=256)

    with pytest.raises(PreferenceReviewerError) as error:
        await reviewer.review(_context(message="完整消息 " * 100), model=MODEL, timeout_seconds=1.0)

    assert error.value.category == "request_budget"
    assert provider.calls == []


@pytest.mark.asyncio
async def test_preference_review_runner_persists_scripted_paraphrase_operations(tmp_path):
    store, session, journal = _journal(tmp_path)
    try:
        job, evidence = _source(
            entries=(
                ("pref_replace", "旧结构", "active", "workspace", 1, 0),
                ("pref_remove", "旧规则", "active", "workspace", 1, 0),
            )
        )
        message = "这条消息偏好代码示例和清晰结构。"
        evidence = evidence.model_copy(
            update={
                "excerpt_redacted": message,
                "excerpt_bytes": len(message.encode()),
                "content_digest": sha256_digest(message),
            }
        )
        journal.put_preference_job_with_evidence("ws_1", job, evidence)
        reviewer = ScriptedPreferenceReviewer(
            [
                PreferenceReviewOutput(
                    operations=(
                        _operation("add", statement="代码示例优先，说明只保留关键设计。"),
                        _operation(
                            "replace", preference_id="pref_replace", statement="回答保持清晰结构。"
                        ),
                        _operation("remove", preference_id="pref_remove"),
                    )
                )
            ]
        )
        result = await PreferenceReviewRunner(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=FixedClock(NOW),
            reviewer=reviewer,
            model=MODEL,
        ).run(
            job.job_id,
            current_user_message=message,
            current_user_record_id="rec_current",
        )
        assert len(reviewer.calls) == 1
        assert len(result.proposals) == 3
        assert [item.operation.operation.value for item in result.proposals] == [
            "add",
            "replace",
            "remove",
        ]
    finally:
        session.close()
        assert store.layout.database.exists()


@pytest.mark.asyncio
async def test_preference_review_runner_zero_operations_is_success_without_proposals(tmp_path):
    store, session, journal = _journal(tmp_path)
    try:
        job, evidence = _source()
        message = "这条消息没有足够信息形成长期规则。"
        evidence = evidence.model_copy(
            update={
                "excerpt_redacted": message,
                "excerpt_bytes": len(message.encode()),
                "content_digest": sha256_digest(message),
            }
        )
        journal.put_preference_job_with_evidence("ws_1", job, evidence)
        result = await PreferenceReviewRunner(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=FixedClock(NOW),
            reviewer=ScriptedPreferenceReviewer([PreferenceReviewOutput()]),
            model=MODEL,
        ).run(
            job.job_id,
            current_user_message=message,
            current_user_record_id="rec_current",
        )
        assert result.proposals == ()
        assert journal.list_preference_proposals("ws_1", limit=10) == ()
    finally:
        session.close()
        assert store.layout.database.exists()
