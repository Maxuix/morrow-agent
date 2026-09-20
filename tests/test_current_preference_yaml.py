from __future__ import annotations

from datetime import UTC, datetime

import yaml

from morrow.adapters.state.preference_yaml import PreferenceYamlLoadStatus, PreferenceYamlStore

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _unsupported_global() -> dict:
    return {
        "schema_version": 1,
        "revision": 7,
        "updated_at": NOW.isoformat(),
        "preferences": {
            "language": "中文",
            "response_detail": "concise",
            "instructions": ["保留代码格式。"],
        },
        "providers": {},
    }


def test_loader_accepts_only_current_global_schema(tmp_path):
    store = PreferenceYamlStore(tmp_path)
    store.global_path.write_text(
        yaml.safe_dump(_unsupported_global(), allow_unicode=True), encoding="utf-8"
    )

    loaded = store.load_global()

    assert loaded.status is PreferenceYamlLoadStatus.UNSUPPORTED_SCHEMA
    assert loaded.error == "unsupported_global_schema"


def test_normal_loader_rejects_future_and_corrupt_documents(tmp_path):
    store = PreferenceYamlStore(tmp_path)
    store.global_path.write_text("schema_version: 99\nrevision: 5\n", encoding="utf-8")
    assert store.load_global().status is PreferenceYamlLoadStatus.UNSUPPORTED_SCHEMA

    store.global_path.write_text("not: [valid", encoding="utf-8")
    assert store.load_global().status is PreferenceYamlLoadStatus.CORRUPT
