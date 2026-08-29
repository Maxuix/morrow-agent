from __future__ import annotations

from datetime import UTC, datetime

import yaml

from morrow.adapters.state.preference_yaml import PreferenceYamlLoadStatus, PreferenceYamlStore
from morrow.adapters.state.yaml import GlobalConfigYamlStore, ProjectStateYamlStore

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _old_global() -> dict:
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


def test_normal_loader_accepts_only_current_global_schema(tmp_path):
    store = PreferenceYamlStore(tmp_path)
    store.global_path.write_text(
        yaml.safe_dump(_old_global(), allow_unicode=True), encoding="utf-8"
    )

    loaded = store.load_global()

    assert loaded.status is PreferenceYamlLoadStatus.UNSUPPORTED_SCHEMA
    assert loaded.error == "unsupported_global_schema"


def test_global_facade_migrates_once_then_runtime_reads_current_only(tmp_path):
    store = PreferenceYamlStore(tmp_path)
    store.global_path.write_text(
        yaml.safe_dump(_old_global(), allow_unicode=True), encoding="utf-8"
    )

    migrated = GlobalConfigYamlStore(tmp_path).load()

    assert migrated.value.schema_version == 2
    assert migrated.revision == 8
    assert [entry.statement for entry in migrated.value.preferences.entries] == [
        "回答时默认使用 中文。",
        "回答默认保持简洁。",
        "保留代码格式。",
    ]
    assert store.load_global().status is PreferenceYamlLoadStatus.OK
    assert store.global_path.with_suffix(".yaml.bak").is_file()


def test_workspace_facade_migrates_once_then_returns_current_document(tmp_path):
    store = PreferenceYamlStore(tmp_path)
    path = store.workspace_path("ws_one")
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "revision": 3,
                "updated_at": NOW.isoformat(),
                "state": "present",
                "preferences": {"instructions": ["先给结论。"]},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    migrated = ProjectStateYamlStore(tmp_path).load_preferences("ws_one")

    assert migrated.value.schema_version == 3
    assert migrated.revision == 4
    assert migrated.value.entries[0].statement == "先给结论。"
    assert store.load_workspace("ws_one").status is PreferenceYamlLoadStatus.OK


def test_normal_loader_rejects_future_and_corrupt_documents(tmp_path):
    store = PreferenceYamlStore(tmp_path)
    store.global_path.write_text("schema_version: 99\nrevision: 5\n", encoding="utf-8")
    assert store.load_global().status is PreferenceYamlLoadStatus.UNSUPPORTED_SCHEMA

    store.global_path.write_text("not: [valid", encoding="utf-8")
    assert store.load_global().status is PreferenceYamlLoadStatus.CORRUPT
