from __future__ import annotations

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.adapters.state.preference_yaml import PreferenceYamlStore
from morrow.application.backup import OperationalBackupService
from morrow.application.configuration import UpdateConfigurationArguments
from morrow.core.domain import AgentRunSnapshot
from morrow.core.preference_documents import (
    GlobalConfig,
    PreferenceEntriesPayload,
    WorkspacePreferenceDocument,
)
from morrow.core.preference_models import PreferenceEntry, PreferenceScope
from morrow.core.store import StoreOpenMode
from test_preference_doctor_backup import _state
from test_preference_store import NOW


def _entry(preference_id: str, statement: str, scope: PreferenceScope) -> PreferenceEntry:
    return PreferenceEntry(
        preference_id=preference_id,
        statement=statement,
        scope=scope,
        created_at=NOW,
        updated_at=NOW,
    )


def test_active_schemas_expose_generic_preferences_and_profile_only_configuration():
    snapshot_fields = AgentRunSnapshot.model_json_schema()["properties"]
    assert "preferences" not in snapshot_fields
    assert "frozen_preferences" in snapshot_fields
    assert "legacy_preferences" not in snapshot_fields

    tool_schema = UpdateConfigurationArguments.model_json_schema()["properties"]
    assert tool_schema["scope"]["const"] == "workspace"
    assert tool_schema["target"]["const"] == "profile"
    encoded = str(tool_schema).casefold()
    assert "response_detail" not in encoded
    assert "instructions" not in encoded


def test_isolated_restore_keeps_yaml_authority_and_sqlite_audit_separate(tmp_path):
    source_store, source_handle, journal = _state(tmp_path / "source")
    try:
        yaml_store = PreferenceYamlStore(source_store.layout.data_root)
        yaml_store.write_global(
            GlobalConfig(
                preferences=PreferenceEntriesPayload(
                    entries=(_entry("pref_global", "默认使用中文回复。", PreferenceScope.GLOBAL),)
                )
            ),
            expected_revision=0,
        )
        yaml_store.write_workspace(
            "ws_1",
            WorkspacePreferenceDocument(
                entries=(
                    _entry(
                        "pref_workspace",
                        "代码回答先给可运行示例。",
                        PreferenceScope.WORKSPACE,
                    ),
                )
            ),
            expected_revision=0,
        )
        backup = OperationalBackupService(source_store, journal=journal)
        created = backup.create("isolated-preference-restore")
        bundle = source_store.layout.backups_dir / created.bundle_name
        assert backup.verify(bundle).references_ok
        assert not any("credential" in path.name for path in bundle.rglob("*"))

        destination_root = tmp_path / "restored"
        assert backup.restore(bundle, destination_root).ok
        destination_store = OperationalStore(destination_root)
        destination_yaml = PreferenceYamlStore(destination_root)

        restored_global = destination_yaml.load_global()
        restored_workspace = destination_yaml.load_workspace("ws_1")
        assert restored_global.value.preferences.entries[0].preference_id == "pref_global"
        assert restored_workspace.value.entries[0].preference_id == "pref_workspace"
        with destination_store.open(StoreOpenMode.DIAGNOSE) as restored_handle:
            restored_journal = SqliteOperationalJournal(restored_handle)
            assert restored_journal.count_preference_review_jobs("ws_1") == 1
            assert restored_journal.get_preference_evidence_for_job("ws_1", "prjob_one")
    finally:
        source_handle.close()
