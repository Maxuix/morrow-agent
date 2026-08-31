"""Subplan 68 v15 migration and future-schema regressions."""

from __future__ import annotations

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
    V16,
    V17,
    V18,
    V19,
    V20,
    V21,
    MigrationRegistry,
)
from morrow.adapters.state.operational import OperationalStore
from morrow.core.store import SUPPORTED_SCHEMA_VERSION, StoreOpenMode


def _registry(version: int) -> MigrationRegistry:
    registry = MigrationRegistry(supported_version=version)
    for migration in (
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
        V16,
        V17,
        V18,
        V19,
        V20,
        V21,
    ):
        if migration.version <= version:
            registry.add(migration)
    return registry


def test_v14_to_v15_creates_draft_validation_and_usage_tables(tmp_path) -> None:
    root = tmp_path / "state"
    OperationalStore(root, registry=_registry(14), maintenance_timeout=0).initialize().close()
    report = OperationalStore(root, maintenance_timeout=0).migrate()
    assert report.from_version == 14
    assert report.to_version == SUPPORTED_SCHEMA_VERSION
    assert report.applied == (
        "skill_drafts_and_usage",
        "mcp_control_catalog_and_snapshots",
        "agent_run_observability",
        "agent_run_completion_truth",
        "agent_run_request_evidence",
        "agent_run_long_horizon_observability",
        "agent_run_retry_progress",
        "durable_runtime_control_queue",
        "agent_definition_foundation",
        "workflow_revision_artifact_contracts",
    )
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


def test_v16_to_v17_creates_agent_run_observation_tables(tmp_path) -> None:
    root = tmp_path / "state"
    OperationalStore(root, registry=_registry(16), maintenance_timeout=0).initialize().close()

    report = OperationalStore(root, maintenance_timeout=0).migrate()

    assert report.from_version == 16
    assert report.to_version == SUPPORTED_SCHEMA_VERSION
    assert report.applied == (
        "agent_run_observability",
        "agent_run_completion_truth",
        "agent_run_request_evidence",
        "agent_run_long_horizon_observability",
        "agent_run_retry_progress",
        "durable_runtime_control_queue",
        "agent_definition_foundation",
        "workflow_revision_artifact_contracts",
    )
    with OperationalStore(root, maintenance_timeout=0).open(StoreOpenMode.READ_ONLY) as handle:
        objects = handle.run_read(
            lambda executor: executor.execute(
                "SELECT type, name FROM sqlite_master WHERE name IN ("
                "'agent_run_model_requests', 'agent_run_terminal_metrics', "
                "'agent_run_model_requests_workspace_guard_insert', "
                "'agent_run_terminal_metrics_workspace_guard_insert') ORDER BY name"
            )
        )
    assert objects == (
        ("table", "agent_run_model_requests"),
        ("trigger", "agent_run_model_requests_workspace_guard_insert"),
        ("table", "agent_run_terminal_metrics"),
        ("trigger", "agent_run_terminal_metrics_workspace_guard_insert"),
    )


def test_v21_and_v22_add_retry_progress_and_runtime_control_tables(tmp_path) -> None:
    root = tmp_path / "state"
    OperationalStore(root, registry=_registry(20), maintenance_timeout=0).initialize().close()

    report = OperationalStore(root, maintenance_timeout=0).migrate()

    assert report.from_version == 20
    assert report.to_version == SUPPORTED_SCHEMA_VERSION
    assert report.applied == (
        "agent_run_retry_progress",
        "durable_runtime_control_queue",
        "agent_definition_foundation",
        "workflow_revision_artifact_contracts",
    )
    with OperationalStore(root, maintenance_timeout=0).open(StoreOpenMode.READ_ONLY) as handle:
        objects = handle.run_read(
            lambda executor: executor.execute(
                "SELECT type, name FROM sqlite_master WHERE name LIKE 'agent_run_retry_progress%' "
                "ORDER BY type, name"
            )
        )
    assert objects == (
        ("index", "agent_run_retry_progress_workspace"),
        ("table", "agent_run_retry_progress"),
        ("trigger", "agent_run_retry_progress_workspace_guard_insert"),
        ("trigger", "agent_run_retry_progress_workspace_guard_update"),
    )
