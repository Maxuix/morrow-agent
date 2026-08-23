from __future__ import annotations

import json

from morrow.adapters.state.permission_journal import _agent_from_row


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
