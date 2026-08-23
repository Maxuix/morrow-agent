from __future__ import annotations

import json

from morrow.adapters.state.permission_journal import _agent_from_row
from morrow.adapters.state.preference_snapshot_compat import decode_agent_run_snapshot


def test_historical_agent_run_snapshot_projects_legacy_preferences_without_rewriting():
    digest = "a" * 64
    raw = {
        "schema_version": 1,
        "created_at": "2026-01-01T00:00:00+00:00",
        "global_preferences": {"language": "中文"},
        "workspace_preferences": {"instructions": ["只解释关键设计。"]},
        "model": {"provider_id": "p", "model_id": "m"},
        "provider_id": "p",
        "source_revisions": [],
        "run_policy_digest": digest,
        "tool_schema_digest": digest,
        "permission_profile_digest": digest,
        "runtime_instance_id": "host-1",
    }
    row = (
        "arun_1",
        "turn_1",
        "ses_1",
        None,
        json.dumps(raw, ensure_ascii=False),
        1767225600,
        None,
    )

    run = _agent_from_row(row)

    assert run.snapshot.legacy_preferences is not None
    assert run.snapshot.legacy_preferences.language == "中文"
    assert run.snapshot.legacy_preferences.instructions == ["只解释关键设计。"]
    assert json.loads(str(row[4])) == raw


def _production_snapshot(**overrides):
    digest = "a" * 64
    raw = {
        "profile": None,
        "preferences": {
            "language": "中文",
            "response_detail": None,
            "instructions": ["只解释关键设计。"],
        },
        "model": {"provider_id": "p", "model_id": "m"},
        "provider_id": "p",
        "source_revisions": [],
        "run_policy_digest": digest,
        "tool_schema_digest": digest,
        "permission_profile_digest": digest,
        "runtime_instance_id": "host-1",
    }
    raw.update(overrides)
    return raw


def test_classic_production_snapshot_decodes_top_level_preferences_without_rewriting():
    raw = _production_snapshot()
    before = json.loads(json.dumps(raw, ensure_ascii=False))

    snapshot = decode_agent_run_snapshot(raw)

    assert snapshot.legacy_preferences is not None
    assert snapshot.legacy_preferences.language == "中文"
    assert raw == before


def test_s60_production_snapshot_keeps_frozen_projection_and_decodes_fixed_projection():
    digest = "b" * 64
    raw = _production_snapshot(
        frozen_preferences=[
            {
                "preference_id": "pref_one",
                "statement": "先给代码。",
                "scope": "workspace",
                "revision": 1,
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ],
        preference_projection_digest=digest,
        preference_source_scopes=["workspace"],
        preference_refresh_status="ok",
    )

    snapshot = decode_agent_run_snapshot(raw)

    assert snapshot.legacy_preferences is not None
    assert snapshot.legacy_preferences.language == "中文"
    assert snapshot.frozen_preferences[0].preference_id == "pref_one"
    assert snapshot.preference_projection_digest == digest
