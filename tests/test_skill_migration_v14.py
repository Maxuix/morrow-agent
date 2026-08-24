"""Subplan 65: Operational Store v14 migration and Skill journal rows."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.migrations import (
    V1,
    V2,
    V3,
    V4,
    V5,
    V6,
    V7,
    V8,
    V9,
    V10,
    V11,
    V12,
    V13,
    MigrationRegistry,
)
from morrow.adapters.state.operational import OperationalStore
from morrow.core.skills.catalog import (
    SkillAvailability,
    SkillConflictStatus,
    SkillDefinition,
    SkillVersion,
)
from morrow.core.skills.trust import SourceKind, TrustLevel
from morrow.core.store import StorageError, StorageErrorCode, StoreOpenMode
from test_operational_store import _retry

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


def _v13_registry() -> MigrationRegistry:
    registry = MigrationRegistry(supported_version=13)
    for migration in (V1, V2, V3, V4, V5, V6, V7, V8, V9, V10, V11, V12, V13):
        registry.add(migration)
    return registry


def _store(root, registry=None):
    return OperationalStore(
        root,
        retry_policy=_retry(),
        clock=None,
        maintenance_timeout=0,
        registry=registry,
    )


def _definition(*, scope_id: str | None = None, skill_id: str = "search-tool") -> SkillDefinition:
    return SkillDefinition(
        skill_id=skill_id,
        name="Search Tool",
        source_kind=SourceKind.IMPORTED,
        scope_id=scope_id,
        availability=SkillAvailability.AVAILABLE,
        conflict_status=SkillConflictStatus.NONE,
        effective_trust=TrustLevel.IMPORTED,
    )


def _version(*, scope_id: str | None = None) -> SkillVersion:
    return SkillVersion(
        version_id="skv_0123456789abcdef",
        skill_id="search-tool",
        display_version="1.2.3",
        tree_digest="b" * 64,
        file_count=2,
        total_bytes=42,
        source_kind=SourceKind.IMPORTED,
        scope_id=scope_id,
        provenance="imported:search-tool",
        evidence_refs=("evt_1",),
        effective_trust=TrustLevel.IMPORTED,
        created_at=NOW,
    )


def test_v13_to_v14_migration_and_repeat(tmp_path) -> None:
    root = tmp_path / "state"
    store = _store(root, registry=_v13_registry())
    store.initialize().close()

    migrated = _store(root)
    report = migrated.migrate()
    assert report.from_version == 13
    assert report.to_version == 14
    assert report.applied == ("skill_catalog_foundation",)
    assert report.backup_name
    with migrated.open(StoreOpenMode.READ_WRITE) as handle:
        journal = SqliteOperationalJournal(handle)
        assert journal.list_skill_definitions() == ()
        assert journal.list_skill_versions() == ()

    # Repeat migration is a no-op.
    again = _store(root)
    repeat = again.migrate()
    assert repeat.from_version == 14
    assert repeat.to_version == 14
    assert repeat.applied == ()


def test_interrupted_v14_rolls_back_to_v13(tmp_path) -> None:
    root = tmp_path / "state"
    _store(root, registry=_v13_registry()).initialize().close()

    broken = MigrationRegistry(supported_version=14)
    for migration in (V1, V2, V3, V4, V5, V6, V7, V8, V9, V10, V11, V12, V13):
        broken.add(migration)
    broken_bad = type(
        "Broken14", (), {"version": 14, "name": "broken_skills", "statements": ("THIS IS NOT SQL",)}
    )()
    broken.add(broken_bad)
    with pytest.raises(StorageError) as error:
        _store(root, registry=broken).migrate()
    assert error.value.code is StorageErrorCode.UNAVAILABLE
    assert _store(root).classify().schema_version == 13


def test_future_schema_version_is_refused() -> None:
    with pytest.raises(StorageError) as error:
        MigrationRegistry(supported_version=15)
    assert error.value.code is StorageErrorCode.UNAVAILABLE


def test_skill_journal_round_trip_and_scope_rules(tmp_path) -> None:
    root = tmp_path / "state"
    _store(root).initialize().close()
    with _store(root).open(StoreOpenMode.READ_WRITE) as handle:
        journal = SqliteOperationalJournal(handle)

        def write(txn):
            txn.put_skill_definition(_definition(), updated_at=NOW)
            txn.put_skill_version(_version())
            txn.record_skill_operation(
                operation_id="sop_1",
                scope="global",
                scope_id=None,
                skill_id="search-tool",
                version_id="skv_0123456789abcdef",
                operation="import",
                disposition="applied",
                evidence_digest="c" * 64,
                reason=None,
                created_at=NOW,
            )
            txn.put_skill_definition(_definition(scope_id="ws_1"), updated_at=NOW)

        journal.transact(write)
        definitions = journal.list_skill_definitions()
        assert len(definitions) == 2
        assert any(item.scope_id == "ws_1" for item in definitions)
        versions = journal.list_skill_versions()
        assert versions[0].evidence_refs == ("evt_1",)
        assert versions[0].created_at == NOW
        assert journal.get_skill_definition(None, "search-tool") is not None
        assert journal.list_skill_versions(workspace_id="ws_1") == ()

    with _store(root).open(StoreOpenMode.READ_WRITE) as handle:
        journal = SqliteOperationalJournal(handle)
        assert len(journal.list_skill_definitions()) == 2


def test_skill_journal_rejects_fake_workspace_ids(tmp_path) -> None:
    root = tmp_path / "state"
    _store(root).initialize().close()
    with _store(root).open(StoreOpenMode.READ_WRITE) as handle:
        journal = SqliteOperationalJournal(handle)
        with pytest.raises(StorageError) as error:
            journal.transact(
                lambda txn: txn.put_skill_definition(
                    _definition(scope_id="not-a-workspace"), updated_at=NOW
                )
            )
        assert error.value.code is StorageErrorCode.UNAVAILABLE
        with pytest.raises(StorageError) as error:
            journal.transact(
                lambda txn: txn.put_skill_definition(_definition(scope_id="WS_1"), updated_at=NOW)
            )
        assert error.value.code is StorageErrorCode.UNAVAILABLE
