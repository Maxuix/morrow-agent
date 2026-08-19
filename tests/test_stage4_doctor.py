"""Read-only operational diagnosis coverage."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.application.api import OperationalApplicationService
from morrow.application.doctor import OperationalDoctor
from morrow.core.capabilities import AccessScope, ApprovalMode, ProcessIsolation
from morrow.core.domain import (
    AgentRunSnapshot,
    DurableAgentRun,
    DurableSession,
    DurableTaskRun,
    DurableTurn,
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
