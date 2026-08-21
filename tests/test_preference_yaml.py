from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from morrow.adapters.state.preference_migration import (
    PreferenceYamlDecodeError,
    decode_global_config,
    decode_legacy_agent_run_preferences,
    decode_workspace_preferences,
    legacy_entries_from_preferences,
    preference_id_from_legacy,
)
from morrow.adapters.state.preference_yaml import PreferenceYamlLoadStatus, PreferenceYamlStore
from morrow.core.preference_models import PreferenceScope

NOW = datetime(2026, 1, 1, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures" / "stage5_preference_v2"


def _legacy_global() -> dict:
    return {
        "schema_version": 1,
        "revision": 7,
        "updated_at": NOW.isoformat(),
        "preferences": {
            "language": "中文",
            "response_detail": "concise",
            "instructions": ["回答默认保持简洁。", "  保留代码格式。  "],
        },
        "providers": {
            "demo": {
                "adapter": "openai_compatible",
                "base_url": "https://example.invalid/v1",
                "models": {"small": {"api_model_id": "small"}},
            }
        },
        "active_model": {"provider_id": "demo", "model_id": "small"},
    }


def test_legacy_mapping_is_ordered_deduplicated_and_id_stable():
    entries = legacy_entries_from_preferences(
        PreferenceScope.GLOBAL,
        _legacy_global()["preferences"],
        timestamp=NOW,
    )
    assert [entry.statement for entry in entries] == [
        "回答时默认使用 中文。",
        "回答默认保持简洁。",
        "保留代码格式。",
    ]
    assert entries[1].preference_id == preference_id_from_legacy(
        "global", "response_detail", "concise", 0
    )
    repeat = legacy_entries_from_preferences(
        "global", _legacy_global()["preferences"], timestamp=NOW
    )
    assert repeat == entries


def test_global_v1_decodes_without_publishing_and_preserves_aggregate():
    value = decode_global_config(_legacy_global())
    assert value.schema_version == 2
    assert value.revision == 7
    assert value.providers["demo"].models["small"].api_model_id == "small"
    assert value.active_model is not None
    assert value.active_model.model_id == "small"
    assert len(value.preferences.entries) == 3


def test_workspace_v2_clear_and_v3_generic_decode_are_separate_from_profile():
    cleared = decode_workspace_preferences({"schema_version": 2, "revision": 3, "state": "cleared"})
    assert cleared.schema_version == 3
    assert cleared.state == "cleared"
    assert cleared.entries is None

    present = decode_workspace_preferences(
        {
            "schema_version": 2,
            "revision": 4,
            "state": "present",
            "preferences": {"language": "English"},
        }
    )
    assert present.entries is not None
    assert present.entries[0].scope is PreferenceScope.WORKSPACE


def test_store_prepare_publish_migration_is_atomic_backed_up_and_occ_checked(tmp_path):
    store = PreferenceYamlStore(tmp_path)
    store.global_path.write_text(
        yaml.safe_dump(_legacy_global(), allow_unicode=True), encoding="utf-8"
    )

    loaded = store.load_global()
    assert loaded.status is PreferenceYamlLoadStatus.OK
    assert loaded.migrated is True
    plan = store.prepare_global_migration()
    assert plan is not None
    published = store.publish_migration(plan)
    assert published.value is not None
    assert published.value.schema_version == 2
    assert published.revision == 8
    assert store.global_path.with_suffix(".yaml.bak").is_file()
    assert store.load_global().migrated is False
    assert store.migrate_global() is None

    replay = store.publish_migration(plan)
    assert replay.revision == published.revision


def test_store_refuses_future_and_corrupt_documents_without_leaking_payload(tmp_path):
    store = PreferenceYamlStore(tmp_path)
    store.global_path.write_text("schema_version: 99\nrevision: 5\n", encoding="utf-8")
    future = store.load_global()
    assert future.status is PreferenceYamlLoadStatus.UNSUPPORTED_SCHEMA
    assert future.error == "future_global_schema"

    store.global_path.write_text("not: [valid", encoding="utf-8")
    corrupt = store.load_global()
    assert corrupt.status is PreferenceYamlLoadStatus.CORRUPT


def test_migration_publish_failure_keeps_primary_document_recoverable(tmp_path):
    def fail(point: str) -> None:
        if point == "replace":
            raise OSError("injected publish failure")

    store = PreferenceYamlStore(tmp_path, failure_injector=fail)
    store.global_path.write_text(
        yaml.safe_dump(_legacy_global(), allow_unicode=True), encoding="utf-8"
    )
    plan = store.prepare_global_migration()
    assert plan is not None
    with pytest.raises(OSError):
        store.publish_migration(plan)
    loaded = store.load_global()
    assert loaded.migrated is True
    assert loaded.revision == 7


def test_historical_agent_run_decoder_does_not_rewrite_or_merge_current_state():
    entries = decode_legacy_agent_run_preferences(
        {
            "global_preferences": {"language": "中文"},
            "workspace_preferences": {"instructions": ["只解释关键设计。"]},
        }
    )
    assert [(entry.scope.value, entry.statement) for entry in entries] == [
        ("global", "回答时默认使用 中文。"),
        ("workspace", "只解释关键设计。"),
    ]


def test_legacy_decoder_rejects_unknown_fixed_detail():
    with pytest.raises(PreferenceYamlDecodeError) as error:
        legacy_entries_from_preferences("global", {"response_detail": "verbose"}, timestamp=NOW)
    assert error.value.code == "invalid_legacy_response_detail"


def test_compatibility_fixture_inventory_covers_decode_only_cases():
    global_raw = yaml.safe_load((FIXTURES / "legacy_global.yaml").read_text(encoding="utf-8"))
    workspace_raw = yaml.safe_load((FIXTURES / "legacy_workspace.yaml").read_text(encoding="utf-8"))
    cleared_raw = yaml.safe_load((FIXTURES / "cleared_workspace.yaml").read_text(encoding="utf-8"))
    assert decode_global_config(global_raw).schema_version == 2
    assert decode_workspace_preferences(workspace_raw).state == "present"
    assert decode_workspace_preferences(cleared_raw).state == "cleared"
    assert decode_legacy_agent_run_preferences(
        json.loads((FIXTURES / "legacy_agent_run.json").read_text(encoding="utf-8"))
    )
