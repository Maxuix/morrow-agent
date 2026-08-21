from __future__ import annotations

from datetime import UTC, datetime

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.adapters.state.preference_yaml import PreferenceYamlStore
from morrow.application.preferences.bridge import (
    candidate_operations,
    preferences_from_entries,
)
from morrow.application.preferences.queries import PreferenceQueries
from morrow.application.preferences.recovery import PreferenceWriteRecovery
from morrow.application.preferences.tool import (
    ManagePreferenceOperation,
    ManagePreferencesArguments,
    PreferenceManagementService,
    make_preference_management_tool,
)
from morrow.application.preferences.writer import (
    PreferenceWriter,
    PreferenceWriterConflict,
    PreferenceWriterError,
)
from morrow.bootstrap import build_application, build_session_application
from morrow.core.learning import LearningCandidateOperation
from morrow.core.learning_payloads import PreferenceCandidatePayload
from morrow.core.models import ModelRef, ProviderConfig, ProviderModelConfig
from morrow.core.preference_documents import PreferenceDocument
from morrow.core.preference_models import PreferenceLifecycleOperation, PreferenceOperation
from morrow.core.preference_persistence_models import PreferenceWriteBatchStatus
from morrow.testing import FixedClock, FixedIdSource, ScriptedModelProvider

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _writer(tmp_path):
    yaml_store = PreferenceYamlStore(tmp_path / "config")
    operational = OperationalStore(tmp_path / "state", clock=FixedClock(NOW), maintenance_timeout=0)
    session = operational.initialize()
    journal = SqliteOperationalJournal(session)
    writer = PreferenceWriter(
        yaml_store,
        journal.preference_journal,
        "ws_1",
        id_source=FixedIdSource(),
        clock=FixedClock(NOW),
    )
    return yaml_store, operational, session, journal, writer


def test_prepare_apply_global_preserves_full_config_aggregate(tmp_path):
    yaml_store, operational, session, _journal, writer = _writer(tmp_path)
    provider = ProviderConfig(
        adapter="test",
        base_url="https://example.invalid",
        models={"model": ProviderModelConfig(api_model_id="model")},
    )
    seeded = yaml_store.write_global(
        yaml_store.load_global().value.model_copy(
            update={
                "providers": {"provider": provider},
                "active_model": ModelRef(provider_id="provider", model_id="model"),
            }
        )
    )
    operation = PreferenceOperation(operation="add", scope="global", statement="先给出可运行代码。")
    prepared = writer.prepare("global", seeded.revision, "cmd_one", (operation,))
    result = writer.apply(prepared)

    loaded = yaml_store.load_global()
    assert result.batch.status is PreferenceWriteBatchStatus.FINALIZED
    assert loaded.revision == seeded.revision + 1
    assert loaded.value is not None
    assert loaded.value.providers["provider"] == provider
    assert loaded.value.active_model == ModelRef(provider_id="provider", model_id="model")
    assert loaded.value.preferences.entries[0].statement == "先给出可运行代码。"
    session.close()
    operational.layout.database.exists()


def test_workspace_writer_is_idempotent_and_rejects_command_reuse(tmp_path):
    yaml_store, operational, session, _journal, writer = _writer(tmp_path)
    operation = PreferenceOperation(operation="add", scope="workspace", statement="使用中文回答。")
    prepared = writer.prepare("workspace", 0, "cmd_one", (operation,))
    first = writer.apply(prepared)
    replay = writer.apply(prepared)
    assert first.batch.batch_id == replay.batch.batch_id
    assert replay.replayed is True
    assert yaml_store.load_workspace("ws_1").revision == 1

    different = PreferenceOperation(
        operation="add", scope="workspace", statement="先解释关键设计。"
    )
    with pytest.raises(PreferenceWriterConflict):
        writer.prepare("workspace", 0, "cmd_one", (different,))
    session.close()
    operational.layout.database.exists()


def test_prepare_rejects_mixed_scope_before_persistence(tmp_path):
    _yaml_store, operational, session, _journal, writer = _writer(tmp_path)
    operations = (
        PreferenceOperation(operation="add", scope="workspace", statement="工作区规则。"),
        PreferenceOperation(operation="add", scope="global", statement="全局规则。"),
    )
    with pytest.raises(Exception, match="one durable document scope"):
        writer.prepare("workspace", 0, "cmd_one", operations)
    session.close()
    operational.layout.database.exists()


def test_recovery_retries_batch_left_prepared_before_yaml(tmp_path):
    yaml_store, operational, session, _journal, writer = _writer(tmp_path)

    def fail(point: str) -> None:
        if point == "temporary_write":
            raise OSError("injected")

    yaml_store.failure_injector = fail
    prepared = writer.prepare(
        "workspace",
        0,
        "cmd_one",
        (PreferenceOperation(operation="add", scope="workspace", statement="保留原意。"),),
    )
    with pytest.raises(PreferenceWriterError, match="publication failed"):
        writer.apply(prepared)
    assert (
        writer.journal.get_preference_write_batch("ws_1", prepared.batch.batch_id).status
        is PreferenceWriteBatchStatus.PREPARED
    )

    yaml_store.failure_injector = None
    result = PreferenceWriteRecovery(writer).recover_batch(prepared.batch.batch_id)
    assert result.batch.status is PreferenceWriteBatchStatus.FINALIZED
    assert yaml_store.load_workspace("ws_1").revision == 1
    session.close()
    operational.layout.database.exists()


def test_recovery_finishes_when_yaml_applied_before_sqlite_finalize(tmp_path, monkeypatch):
    yaml_store, operational, session, journal, writer = _writer(tmp_path)
    prepared = writer.prepare(
        "workspace",
        0,
        "cmd_one",
        (PreferenceOperation(operation="add", scope="workspace", statement="先完成工作。"),),
    )
    preference_journal = journal.preference_journal
    original = preference_journal.save_preference_write_batch

    def fail_once(*args, **kwargs):
        monkeypatch.setattr(preference_journal, "save_preference_write_batch", original)
        raise RuntimeError("injected finalize boundary")

    monkeypatch.setattr(preference_journal, "save_preference_write_batch", fail_once)
    with pytest.raises(PreferenceWriterError):
        writer.apply(prepared)
    assert yaml_store.load_workspace("ws_1").revision == 1
    assert (
        preference_journal.get_preference_write_batch("ws_1", prepared.batch.batch_id).status
        is PreferenceWriteBatchStatus.PREPARED
    )

    result = PreferenceWriteRecovery(writer).recover_batch(prepared.batch.batch_id)
    assert result.batch.status is PreferenceWriteBatchStatus.FINALIZED
    session.close()
    operational.layout.database.exists()


def test_writer_persists_enable_disable_lifecycle_in_same_saga(tmp_path):
    yaml_store, operational, session, journal, writer = _writer(tmp_path)
    added = writer.apply(
        writer.prepare(
            "workspace",
            0,
            "cmd_add",
            (PreferenceOperation(operation="add", scope="workspace", statement="可禁用规则。"),),
        )
    )
    preference_id = added.document.entries[0].preference_id
    disabled = writer.apply(
        writer.prepare(
            "workspace",
            1,
            "cmd_disable",
            (),
            lifecycle_operations=(
                PreferenceLifecycleOperation(
                    operation="disable", scope="workspace", preference_id=preference_id
                ),
            ),
        )
    )
    assert disabled.document.entries[0].status.value == "disabled"
    stored = journal.preference_journal.get_preference_write_batch("ws_1", disabled.batch.batch_id)
    assert stored is not None
    assert stored.lifecycle_operations[0].operation.value == "disable"
    enabled = writer.apply(
        writer.prepare(
            "workspace",
            2,
            "cmd_enable",
            (),
            lifecycle_operations=(
                PreferenceLifecycleOperation(
                    operation="enable", scope="workspace", preference_id=preference_id
                ),
            ),
        )
    )
    assert enabled.document.entries[0].status.value == "active"
    session.close()
    operational.layout.database.exists()


def test_manage_preferences_is_an_approved_configuration_write(tmp_path):
    yaml_store, operational, session, _journal, writer = _writer(tmp_path)
    service = PreferenceManagementService(writer, PreferenceQueries(yaml_store, "ws_1"))
    arguments = ManagePreferencesArguments(
        scope="workspace",
        operations=(ManagePreferenceOperation(operation="add", statement="明确要求才持久化。"),),
    )
    tool = make_preference_management_tool(service)
    assert tool.definition.function.name == "manage_preferences"
    assert tool.execution_policy.approval.value == "required"
    assert tool.execution_policy.effect.value == "persistent_write"
    assert service.preflight(arguments)[0] == "Preference batch (1 operations)"
    result = service.apply(arguments, command_id="cmd_manage")
    assert result["status"] == "applied"
    assert result["revision"] == 1
    session.close()
    operational.layout.database.exists()


def test_manage_preferences_applies_one_multi_operation_document_revision(tmp_path):
    yaml_store, operational, session, _journal, writer = _writer(tmp_path)
    service = PreferenceManagementService(writer, PreferenceQueries(yaml_store, "ws_1"))
    arguments = ManagePreferencesArguments(
        scope="workspace",
        operations=(
            ManagePreferenceOperation(operation="add", statement="先给结论。"),
            ManagePreferenceOperation(operation="add", statement="再解释关键设计。"),
        ),
    )

    result = service.apply(arguments, command_id="cmd_multi")

    assert result["revision"] == 1
    assert len(result["preference_ids"]) == 2
    document = PreferenceQueries(yaml_store, "ws_1").document("workspace")
    assert document.revision == 1
    assert [entry.revision for entry in document.entries] == [1, 1]
    session.close()
    operational.layout.database.exists()


def test_generic_authority_registers_manage_preferences_for_a_new_session(tmp_path):
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
        id_source=FixedIdSource(),
    )
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    store = OperationalStore(app.data_root.root, clock=FixedClock(NOW), maintenance_timeout=0)
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    writer = PreferenceWriter(
        PreferenceYamlStore(app.data_root.root),
        journal,
        identity.workspace_id,
        id_source=FixedIdSource(),
        clock=FixedClock(NOW),
    )
    writer.apply(
        writer.prepare(
            "workspace",
            0,
            "cmd_seed_runtime",
            (PreferenceOperation(operation="add", scope="workspace", statement="新会话可见。"),),
        )
    )
    handle.close()

    session_app = build_session_application(
        app,
        identity,
        provider=ScriptedModelProvider(["done"]),
        model=ModelRef(provider_id="p", model_id="m"),
    )

    names = {
        tool.function.name
        for tool in session_app.orchestrator.runtime.loop.tool_executor.definitions
    }
    assert "manage_preferences" in names


def test_legacy_preference_candidate_bridge_preserves_generic_tombstones():
    document = PreferenceDocument(scope="workspace")
    payload = PreferenceCandidatePayload(path="instructions", value=("先给结论。",))
    candidate = type("Candidate", (), {"operation": LearningCandidateOperation.APPEND})()
    operations = candidate_operations(candidate, payload, document)
    assert operations[0].operation.value == "add"
    assert operations[0].statement == "先给结论。"

    from morrow.core.preference_operations import reduce_preference_document

    after = reduce_preference_document(document, operations)
    projected = preferences_from_entries(after.entries)
    assert projected.instructions == ["先给结论。"]


def test_legacy_candidate_bridge_maps_instruction_tuple_and_scalar_replace():
    document = PreferenceDocument(scope="workspace")
    instruction_payload = PreferenceCandidatePayload(
        path="instructions", value=("先给结论。", "再解释关键设计。")
    )
    append = type("Candidate", (), {"operation": LearningCandidateOperation.APPEND})()
    operations = candidate_operations(append, instruction_payload, document)
    assert [item.statement for item in operations] == ["先给结论。", "再解释关键设计。"]

    from morrow.core.preference_operations import reduce_preference_document

    document = reduce_preference_document(document, operations)
    replace = type("Candidate", (), {"operation": LearningCandidateOperation.SET})()
    language = PreferenceCandidatePayload(path="language", value="中文")
    added = candidate_operations(replace, language, document)
    assert added[0].operation.value == "add"
    document = reduce_preference_document(document, added)
    language_id = next(
        entry.preference_id
        for entry in document.entries
        if entry.statement.startswith("回答时默认使用")
    )
    changed_language = PreferenceCandidatePayload(path="language", value="English")
    replaced = candidate_operations(replace, changed_language, document)
    assert replaced[0].operation.value == "replace"
    assert replaced[0].preference_id == language_id
