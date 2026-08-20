"""Read-only operational diagnosis coverage."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.application.api import OperationalApplicationService
from morrow.application.artifacts import ArtifactService
from morrow.application.doctor import OperationalDoctor
from morrow.core.artifacts import ArtifactKind
from morrow.core.capabilities import AccessScope, ApprovalMode, ProcessIsolation
from morrow.core.domain import (
    AgentRunSnapshot,
    DurableAgentRun,
    DurableSession,
    DurableTaskRun,
    DurableTurn,
    SessionLifecycle,
    sha256_digest,
)
from morrow.core.models import ModelRef
from morrow.core.permissions import (
    CapabilityGrant,
    CapabilityIsolation,
    CapabilityName,
    GrantSource,
    IsolationLabel,
    PermissionSnapshot,
    capability_grant_digest,
)
from morrow.testing import FixedClock, FixedIdSource


def test_doctor_is_read_only_and_reports_application_event_counts(tmp_path):
    store = OperationalStore(
        tmp_path / "state",
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
    api = OperationalApplicationService(
        journal=journal, workspace_id="ws_1", id_source=FixedIdSource()
    )
    api.create_session(session_id="ses_2", command_id="cmd_1")
    before = store.layout.database.stat().st_mtime_ns
    report = OperationalDoctor(store).inspect("ws_1")
    after = store.layout.database.stat().st_mtime_ns
    handle.close()
    assert report.health.value == "ok"
    assert report.counts["sessions"] == 2
    assert report.counts["application_events"] == 1
    assert before == after
    assert b"/" not in report.json_bytes()


def test_doctor_reports_grant_and_frozen_permission_evidence(tmp_path):
    now = datetime.now(UTC).replace(microsecond=0)
    clock = FixedClock(now)
    store = OperationalStore(
        tmp_path / "state",
        clock=clock,
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    journal.create_session(
        DurableSession(session_id="ses_1", workspace_id="ws_1"),
        task=DurableTaskRun(task_run_id="task_1", session_id="ses_1", workspace_id="ws_1"),
    )
    journal.create_turn(
        "ws_1",
        DurableTurn(
            turn_id="turn_1",
            session_id="ses_1",
            task_run_id="task_1",
            client_message_id="client-1",
        ),
    )
    journal.create_agent_run(
        "ws_1",
        DurableAgentRun(
            agent_run_id="arun_1",
            turn_id="turn_1",
            session_id="ses_1",
            snapshot=AgentRunSnapshot(
                model=ModelRef(provider_id="p", model_id="m"),
                provider_id="p",
                run_policy_digest=sha256_digest("policy"),
                tool_schema_digest=sha256_digest("tools"),
                permission_profile_digest=sha256_digest("profile"),
                runtime_instance_id="runtime-1",
            ),
        ),
    )
    grant = CapabilityGrant(
        grant_id="grt_1",
        workspace_id="ws_1",
        task_run_id="task_1",
        agent_run_id="arun_1",
        capabilities=(CapabilityName.UNCONFINED_HOST_PROCESS,),
        granted_by=GrantSource.LOCAL_INTERFACE_COMMAND,
        command_id="cmd_grant",
        reason="Run one local validation",
        preview_digest=sha256_digest("preview"),
        created_at=now,
        expires_at=now + timedelta(minutes=15),
    )
    journal.put_capability_grant("ws_1", grant)
    snapshot = PermissionSnapshot(
        permission_snapshot_id="psnap_1",
        workspace_id="ws_1",
        session_id="ses_1",
        task_run_id="task_1",
        turn_id="turn_1",
        agent_run_id="arun_1",
        access_scope=AccessScope.FULL_ACCESS,
        approval_mode=ApprovalMode.MANUAL,
        process_isolation=ProcessIsolation.HOST,
        workspace_root_digest=sha256_digest("root"),
        workspace_read_only=False,
        tool_schema_digest=sha256_digest("tools"),
        run_policy_digest=sha256_digest("policy"),
        permission_profile_digest=sha256_digest("profile"),
        grant_id=grant.grant_id,
        grant_digest=capability_grant_digest(grant),
        granted_capabilities=grant.capabilities,
        capability_isolations=(
            CapabilityIsolation(
                capability=CapabilityName.UNCONFINED_HOST_PROCESS,
                isolation=IsolationLabel.UNCONFINED_HOST,
            ),
        ),
        created_at=now,
    )
    journal.freeze_agent_run_permission_snapshot("ws_1", "arun_1", snapshot)
    api = OperationalApplicationService(
        journal=journal,
        workspace_id="ws_1",
        id_source=FixedIdSource(),
        clock=clock.now,
    )
    revoked = api.revoke_grant(
        grant.grant_id,
        reason="User stopped the elevated run",
        expected_row_version=1,
        command_id="cmd_revoke",
    )
    assert revoked.value.revoked_at is not None
    before = store.layout.database.stat().st_mtime_ns
    report = OperationalDoctor(store).inspect("ws_1")
    after = store.layout.database.stat().st_mtime_ns
    handle.close()

    assert report.health.value == "ok"
    assert report.counts["capability_grants"] == 1
    assert report.counts["permission_snapshots"] == 1
    assert report.counts["permission_evidence"] == 1
    assert report.counts["revoked_grants"] == 1
    assert before == after


def test_doctor_reports_archived_session_with_active_task_as_needs_repair(tmp_path):
    store = OperationalStore(
        tmp_path / "state",
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    try:
        journal = SqliteOperationalJournal(handle)
        journal.create_session(
            DurableSession(session_id="ses_1", workspace_id="ws_1"),
            task=DurableTaskRun(
                task_run_id="task_1",
                session_id="ses_1",
                workspace_id="ws_1",
            ),
        )
        # Simulate the contradictory row written by the pre-remediation application.
        handle.run_write(
            lambda executor: executor.execute(
                "UPDATE sessions SET lifecycle = 'archived' WHERE session_id = 'ses_1'"
            )
        )

        report = OperationalDoctor(store).inspect("ws_1")
        assert report.health.value == "needs_repair"
        assert any(issue.code == "archived_active_task" for issue in report.issues)
        assert journal.get_session("ws_1", "ses_1").lifecycle is SessionLifecycle.ARCHIVED
    finally:
        handle.close()


def test_doctor_classifies_global_artifact_candidates_without_cross_workspace_false_orphans(
    tmp_path,
):
    store = OperationalStore(
        tmp_path / "state",
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    try:
        journal = SqliteOperationalJournal(handle)
        journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
        journal.create_session(DurableSession(session_id="ses_2", workspace_id="ws_2"))
        filesystem = FilesystemArtifactStore(store.layout)
        artifacts = ArtifactService(
            journal=journal,
            filesystem=filesystem,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
        )
        managed = artifacts.publish_bytes(
            b"workspace one",
            kind=ArtifactKind.COMMAND_OUTPUT,
            artifact_id="art_managed",
        )
        removable = filesystem.artifacts_dir / "art_removable.artifact"
        removable.write_bytes(b"orphan")
        removable.chmod(0o600)
        unsafe = filesystem.artifacts_dir / "unexpected.txt"
        unsafe.write_bytes(b"unsafe")
        unsafe.chmod(0o600)

        report = OperationalDoctor(store).inspect("ws_2")
        codes = {issue.code for issue in report.issues}
        assert report.counts["artifacts"] == 0
        assert report.counts["orphan_candidates"] == 3
        assert report.counts["artifact_managed_unreferenced"] == 1
        assert report.counts["artifact_unmanaged_removable"] == 1
        assert report.counts["artifact_unsafe_refused"] == 1
        assert {
            "artifact_managed_unreferenced",
            "artifact_unmanaged_removable",
            "artifact_unsafe_refused",
        }.issubset(codes)
        assert "artifact_orphan" not in codes
        assert filesystem.existing_final_path(managed.artifact_id).exists()
    finally:
        handle.close()


def test_doctor_refuses_unsafe_layout_before_candidate_traversal(tmp_path, monkeypatch):
    store = OperationalStore(
        tmp_path / "state",
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    try:
        journal = SqliteOperationalJournal(handle)
        journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
        store.layout.artifacts_tmp.chmod(0o777)
        monkeypatch.setattr(
            FilesystemArtifactStore,
            "orphan_report",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("unsafe layout must not be traversed")
            ),
        )

        report = OperationalDoctor(store).inspect("ws_1")
        assert report.health.value == "needs_repair"
        assert report.counts["orphan_candidates"] == 1
        assert report.counts["artifact_managed_unreferenced"] == 0
        assert report.counts["artifact_unmanaged_removable"] == 0
        assert report.counts["artifact_unsafe_refused"] == 1
        assert any(issue.code == "artifact_unsafe_refused" for issue in report.issues)
    finally:
        handle.close()


def test_doctor_refuses_symlink_data_root_without_traversing_artifacts(tmp_path, monkeypatch):
    target = tmp_path / "state-target"
    original = OperationalStore(
        target,
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    handle = original.initialize()
    try:
        SqliteOperationalJournal(handle).create_session(
            DurableSession(session_id="ses_1", workspace_id="ws_1")
        )
    finally:
        handle.close()

    linked_root = tmp_path / "state-link"
    linked_root.symlink_to(target, target_is_directory=True)
    linked = OperationalStore(
        linked_root,
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    monkeypatch.setattr(
        FilesystemArtifactStore,
        "orphan_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("symlink data root must not be traversed")
        ),
    )

    report = OperationalDoctor(linked).inspect("ws_1")
    assert report.health.value == "needs_repair"
    assert report.counts["orphan_candidates"] == 1
    assert report.counts["artifact_unsafe_refused"] == 1
    assert any(issue.code == "artifact_unsafe_refused" for issue in report.issues)
