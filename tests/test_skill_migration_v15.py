"""Subplan 68 v15 migration and future-schema regressions."""

from __future__ import annotations

import pytest

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
    V14,
    V15,
    MigrationRegistry,
)
from morrow.adapters.state.operational import OperationalStore
from morrow.core.store import StorageError, StorageErrorCode, StoreOpenMode


def _registry(version: int) -> MigrationRegistry:
    registry = MigrationRegistry(supported_version=version)
    for migration in (V1, V2, V3, V4, V5, V6, V7, V8, V9, V10, V11, V12, V13, V14, V15):
        if migration.version <= version:
            registry.add(migration)
    return registry


def test_v14_to_v15_creates_draft_validation_and_usage_tables(tmp_path) -> None:
    root = tmp_path / "state"
    OperationalStore(root, registry=_registry(14), maintenance_timeout=0).initialize().close()
    report = OperationalStore(root, maintenance_timeout=0).migrate()
    assert report.from_version == 14
    assert report.to_version == 15
    assert report.applied == ("skill_drafts_and_usage",)
    with OperationalStore(root, maintenance_timeout=0).open(StoreOpenMode.READ_ONLY) as handle:
        names = handle.run_read(
            lambda executor: executor.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'skill_%' "
                "ORDER BY name"
            )
        )
    assert {row[0] for row in names} >= {
        "skill_drafts",
        "skill_draft_validations",
        "skill_usage",
    }


def test_v16_is_still_reserved_for_future_mcp_work(tmp_path) -> None:
    with pytest.raises(StorageError) as error:
        MigrationRegistry(supported_version=16)
    assert error.value.code is StorageErrorCode.UNAVAILABLE
