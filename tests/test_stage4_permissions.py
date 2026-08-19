"""Focused contracts for the v9 permission-evidence substrate."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.application.api import OperationalApplicationService
from morrow.application.grants import (
    CapabilityGrantError,
    CapabilityGrantService,
    validate_capability_subset,
)
from morrow.application.prepared import prepare_cycle_executions
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.capabilities import (
    AccessScope,
    ApprovalMode,
    OperationIntent,
    OperationKind,
    PermissionPreset,
    PermissionProfile,
    ProcessIsolation,
    RiskFlag,
    ToolRunContext,
    WorkspaceCapability,
)
from morrow.core.domain import (
    AgentRunSnapshot,
    DurableAgentRun,
    DurableSession,
    DurableTaskRun,
    DurableTurn,
    canonical_json_bytes,
    sha256_digest,
)
from morrow.core.execution import (
    DurableApproval,
    DurableToolExecution,
    EffectClass,
    PreparedIntent,
    ToolExecutionDisposition,
    ToolExecutionState,
    approval_preview_digest,
    assert_handler_may_enter,
    intent_hash,
)
from morrow.core.models import AssistantMessage, FunctionToolCall, ModelRef
from morrow.core.permissions import (
    CAPABILITY_GRANT_ID_PREFIX,
    CapabilityGrant,
    CapabilityIsolation,
    CapabilityName,
    GrantSource,
    IsolationLabel,
    PermissionEvidenceError,
    PermissionSnapshot,
    assert_grant_snapshot_matches,
    capability_grant_digest,
)
from morrow.core.store import SUPPORTED_SCHEMA_VERSION, StorageError
from morrow.runtime.capabilities import CapabilityPolicy
from morrow.runtime.session import Session
from morrow.runtime.tools import ToolExecutor, ToolRegistry, make_tool
from morrow.testing import FixedClock, FixedIdSource, make_context_builder

NOW = datetime(2026, 1, 1, tzinfo=UTC)
FULL_ACCESS_PROFILE_DIGEST = sha256_digest(
    canonical_json_bytes(
        PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL).model_dump(mode="json")
    )
)


def _grant(**overrides) -> CapabilityGrant:
    values = {
        "grant_id": "grt_1",
        "workspace_id": "ws_1",
        "task_run_id": "task_1",
        "agent_run_id": "arun_1",
        "capabilities": (CapabilityName.UNCONFINED_HOST_PROCESS,),
        "granted_by": GrantSource.LOCAL_INTERFACE_COMMAND,
        "command_id": "cmd_1",
        "reason": "Run the requested local validation command",
        "preview_digest": sha256_digest("preview"),
        "created_at": NOW,
        "expires_at": NOW + timedelta(minutes=15),
    }
    values.update(overrides)
    return CapabilityGrant(**values)


def _snapshot(**overrides) -> PermissionSnapshot:
    values = {
        "permission_snapshot_id": "psnap_1",
        "workspace_id": "ws_1",
        "session_id": "ses_1",
        "task_run_id": "task_1",
        "turn_id": "turn_1",
        "agent_run_id": "arun_1",
        "access_scope": AccessScope.WORKSPACE,
        "approval_mode": ApprovalMode.MANUAL,
        "process_isolation": ProcessIsolation.HOST,
        "workspace_root_digest": sha256_digest("workspace-root"),
        "workspace_read_only": False,
        "tool_schema_digest": sha256_digest("tools"),
        "run_policy_digest": sha256_digest("policy"),
        "permission_profile_digest": sha256_digest("profile"),
        "created_at": NOW,
    }
    values.update(overrides)
    return PermissionSnapshot(**values)


def test_grant_is_strict_run_bound_and_only_exposes_the_stage4_capability():
    grant = _grant()
    assert grant.grant_id.startswith(f"{CAPABILITY_GRANT_ID_PREFIX}_")
    assert grant.is_active(NOW + timedelta(seconds=1))
    assert not grant.is_active(grant.expires_at)
    with pytest.raises(ValidationError):
        _grant(capabilities=("network",))
    with pytest.raises(ValidationError):
        _grant(granted_by="model_output")
    with pytest.raises(ValidationError):
        _grant(expires_at=NOW + timedelta(days=2))
    with pytest.raises(ValidationError):
        _grant(reason="contains a password")
    with pytest.raises(ValidationError):
        CapabilityGrant.model_validate({**grant.model_dump(), "new_field": True}, strict=True)


@pytest.mark.parametrize(
    "source",
    ("model_output", "tool_handler", "project_file", "recovery_record", "profile"),
)
def test_non_interface_sources_cannot_create_or_extend_a_grant(source):
    with pytest.raises(ValidationError):
        _grant(granted_by=source)
    with pytest.raises(CapabilityGrantError):
        validate_capability_subset(("network",))


def test_snapshot_requires_complete_and_matching_elevated_evidence():
    plain = _snapshot()
    assert plain.grant_id is None
    assert plain.isolation_label is None

    grant = _grant()
    elevated = _snapshot(
        access_scope=AccessScope.FULL_ACCESS,
        grant_id=grant.grant_id,
        grant_digest=capability_grant_digest(grant),
        granted_capabilities=grant.capabilities,
        capability_isolations=(
            CapabilityIsolation(
                capability=CapabilityName.UNCONFINED_HOST_PROCESS,
                isolation=IsolationLabel.UNCONFINED_HOST,
            ),
        ),
    )
    assert elevated.isolation_label is IsolationLabel.UNCONFINED_HOST
    with pytest.raises(ValidationError):
        _snapshot(grant_id=grant.grant_id)
    with pytest.raises(ValidationError):
        _snapshot(
            grant_id=grant.grant_id,
            grant_digest=sha256_digest("grant"),
            granted_capabilities=grant.capabilities,
            capability_isolations=(
                CapabilityIsolation(
                    capability=CapabilityName.UNCONFINED_HOST_PROCESS,
                    isolation=IsolationLabel.WORKSPACE,
                ),
            ),
        )


def test_grant_evidence_rejects_cross_scope_and_revoked_reuse():
    grant = _grant()
    snapshot = _snapshot(
        access_scope=AccessScope.FULL_ACCESS,
        grant_id=grant.grant_id,
        grant_digest=capability_grant_digest(grant),
        granted_capabilities=grant.capabilities,
        capability_isolations=(
            CapabilityIsolation(
                capability=CapabilityName.UNCONFINED_HOST_PROCESS,
                isolation=IsolationLabel.UNCONFINED_HOST,
            ),
        ),
    )
    with pytest.raises(PermissionEvidenceError, match="outside"):
        assert_grant_snapshot_matches(
            snapshot,
            grant,
            now=NOW,
            workspace_id="ws_2",
            task_run_id="task_1",
            agent_run_id="arun_1",
        )
    with pytest.raises(PermissionEvidenceError, match="subjects"):
        assert_grant_snapshot_matches(
            snapshot,
            grant,
            now=NOW,
            workspace_id="ws_1",
            task_run_id="task_2",
            agent_run_id="arun_1",
        )
    revoked = CapabilityGrant.model_validate(
        grant.model_dump(
            mode="python",
            round_trip=True,
        )
        | {
            "revoked_at": NOW + timedelta(minutes=1),
            "revocation_reason": "user stopped the run",
            "row_version": 2,
        },
        strict=True,
    )
    assert capability_grant_digest(revoked) == capability_grant_digest(grant)
    revoked_snapshot = PermissionSnapshot.model_validate(
        snapshot.model_copy(update={"grant_digest": capability_grant_digest(revoked)}).model_dump(),
        strict=True,
    )
    with pytest.raises(PermissionEvidenceError, match="expired or revoked"):
        assert_grant_snapshot_matches(
            revoked_snapshot,
            revoked,
            now=NOW + timedelta(minutes=2),
            workspace_id="ws_1",
            task_run_id="task_1",
            agent_run_id="arun_1",
        )


def test_v9_schema_keeps_old_tables_and_adds_permission_evidence_tables(tmp_path):
    store = OperationalStore(
        tmp_path / "state",
        retry_policy=BusyRetryPolicy(sleep=lambda _delay: None, rng=random.Random(0)),
        clock=FixedClock(),
        maintenance_timeout=0,
    )
    session = store.initialize()
    try:
        assert session.schema_version == SUPPORTED_SCHEMA_VERSION == 9
        rows = session.run_read(
            lambda executor: executor.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('capability_grants', 'permission_snapshots', 'agent_runs') "
                "ORDER BY name"
            )
        )
        assert rows == (("agent_runs",), ("capability_grants",), ("permission_snapshots",))
        columns = session.run_read(
            lambda executor: executor.execute("PRAGMA table_info(agent_runs)")
        )
        assert any(row[1] == "permission_snapshot_id" for row in columns)
        execution_columns = session.run_read(
            lambda executor: executor.execute("PRAGMA table_info(tool_executions)")
        )
        assert {"permission_snapshot_id", "grant_id", "isolation"} <= {
            str(row[1]) for row in execution_columns
        }
    finally:
        session.close()


def test_grant_commands_are_receipted_workspace_scoped_and_revocable(tmp_path):
    store = OperationalStore(
        tmp_path / "state",
        retry_policy=BusyRetryPolicy(sleep=lambda _delay: None, rng=random.Random(0)),
        clock=FixedClock(NOW),
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
                permission_profile_digest=FULL_ACCESS_PROFILE_DIGEST,
                runtime_instance_id="runtime-1",
            ),
        ),
    )
    api = OperationalApplicationService(
        journal=journal,
        workspace_id="ws_1",
        id_source=FixedIdSource(),
        clock=FixedClock(NOW).now,
    )
    try:
        created = api.create_grant(
            task_run_id="task_1",
            agent_run_id="arun_1",
            capabilities=(CapabilityName.UNCONFINED_HOST_PROCESS,),
            reason="Run one explicitly approved validation",
            preview_digest=sha256_digest("preview"),
            command_id="cmd_grant",
        )
        assert created.value.grant_id == "grt_1"
        assert created.receipt is not None
        assert created.receipt.operation == "grant_create"
        replay = api.create_grant(
            task_run_id="task_1",
            agent_run_id="arun_1",
            capabilities=(CapabilityName.UNCONFINED_HOST_PROCESS,),
            reason="Run one explicitly approved validation",
            preview_digest=sha256_digest("preview"),
            command_id="cmd_grant",
        )
        assert replay.receipt is not None
        assert replay.receipt.disposition.value == "replay"
        with pytest.raises(ApplicationError) as error:
            api.create_grant(
                task_run_id="task_1",
                agent_run_id="arun_1",
                capabilities=(CapabilityName.UNCONFINED_HOST_PROCESS,),
                reason="A second active authority",
                preview_digest=sha256_digest("preview-2"),
                command_id="cmd_grant_2",
            )
        assert error.value.code is ApplicationErrorCode.CONFLICT
        revoked = api.revoke_grant(
            created.value.grant_id,
            reason="User stopped the elevated run",
            expected_row_version=1,
            command_id="cmd_revoke",
        )
        assert revoked.value.revoked_at is not None
        assert revoked.value.row_version == 2
        assert api.get_grant(created.value.grant_id).revoked_at is not None
    finally:
        handle.close()


def test_grant_command_rejects_an_ordinary_agent_run(tmp_path):
    store = OperationalStore(
        tmp_path / "state",
        retry_policy=BusyRetryPolicy(sleep=lambda _delay: None, rng=random.Random(0)),
        clock=FixedClock(NOW),
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
                permission_profile_digest=sha256_digest("ordinary-profile"),
                runtime_instance_id="runtime-1",
            ),
        ),
    )
    api = OperationalApplicationService(
        journal=journal,
        workspace_id="ws_1",
        id_source=FixedIdSource(),
        clock=FixedClock(NOW).now,
    )
    try:
        with pytest.raises(ApplicationError) as error:
            api.create_grant(
                task_run_id="task_1",
                agent_run_id="arun_1",
                capabilities=(CapabilityName.UNCONFINED_HOST_PROCESS,),
                reason="Attempt to elevate an ordinary run",
                preview_digest=sha256_digest("preview"),
                command_id="cmd_grant",
            )
        assert error.value.code is ApplicationErrorCode.INVALID
    finally:
        handle.close()


def test_elevated_prepared_host_preview_contains_unconfined_warning(tmp_path):
    class Arguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

    async def handler(_arguments):
        return {"ok": True}

    def resolve(_arguments, _context):
        return OperationIntent(
            kind=OperationKind.PROCESS,
            requires_host=True,
            risk_flags=(RiskFlag.OUTSIDE_WORKSPACE, RiskFlag.NETWORK),
            preview_summary=("opaque host command",),
        )

    session = Session(
        session_id="ses_1",
        permission_profile=PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL),
        workspace_capability=WorkspaceCapability(workspace_id="ws_1", root=tmp_path),
    )
    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="run_command",
            description="Run one opaque host command",
            arguments_model=Arguments,
            handler=handler,
            intent_resolver=resolve,
        )
    )
    executor = ToolExecutor(
        registry.snapshot(),
        make_context_builder().run_policy,
        capability_policy=CapabilityPolicy(
            session.permission_profile, session.workspace_capability
        ),
    )

    executions = prepare_cycle_executions(
        AssistantMessage(
            tool_calls=(FunctionToolCall(id="call-1", name="run_command", arguments="{}"),)
        ),
        session=session,
        tool_executor=executor,
        run_context=ToolRunContext(run_id="turn_1", session_id="ses_1"),
        id_source=FixedIdSource(),
        workspace_id="ws_1",
        task_run_id="task_1",
        turn_id="turn_1",
        agent_run_id="arun_1",
        isolation=ProcessIsolation.HOST,
        permission_snapshot_id="psnap_1",
        grant_id="grt_1",
        isolation_label=IsolationLabel.UNCONFINED_HOST,
    )

    assert executions[0].intent.requires_approval
    assert any(line.startswith("unconfined_host:") for line in executions[0].intent.preview)
    assert executions[0].isolation is IsolationLabel.UNCONFINED_HOST


def test_grant_evidence_is_not_attached_to_structured_tool_executions(tmp_path):
    class Arguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

    async def handler(_arguments):
        return {"ok": True}

    def resolve(_arguments, _context):
        return OperationIntent(
            kind=OperationKind.INTERNAL_READ,
            preview_summary=("structured local read",),
        )

    session = Session(
        session_id="ses_1",
        permission_profile=PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL),
        workspace_capability=WorkspaceCapability(workspace_id="ws_1", root=tmp_path),
    )
    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="calculate",
            description="Structured local read",
            arguments_model=Arguments,
            handler=handler,
            intent_resolver=resolve,
        )
    )
    executor = ToolExecutor(
        registry.snapshot(),
        make_context_builder().run_policy,
        capability_policy=CapabilityPolicy(
            session.permission_profile, session.workspace_capability
        ),
    )

    executions = prepare_cycle_executions(
        AssistantMessage(
            tool_calls=(FunctionToolCall(id="call-1", name="calculate", arguments="{}"),)
        ),
        session=session,
        tool_executor=executor,
        run_context=ToolRunContext(run_id="turn_1", session_id="ses_1"),
        id_source=FixedIdSource(),
        workspace_id="ws_1",
        task_run_id="task_1",
        turn_id="turn_1",
        agent_run_id="arun_1",
        isolation=ProcessIsolation.HOST,
        permission_snapshot_id="psnap_1",
        grant_id="grt_1",
        isolation_label=IsolationLabel.UNCONFINED_HOST,
    )

    assert executions[0].permission_snapshot_id == "psnap_1"
    assert executions[0].grant_id is None
    assert executions[0].isolation is None


def test_permission_snapshot_freezes_once_and_links_to_the_agent_run(tmp_path):
    store = OperationalStore(
        tmp_path / "state",
        retry_policy=BusyRetryPolicy(sleep=lambda _delay: None, rng=random.Random(0)),
        clock=FixedClock(NOW),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    try:
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
        snapshot = _snapshot()
        frozen = journal.freeze_agent_run_permission_snapshot("ws_1", "arun_1", snapshot)
        assert frozen.permission_snapshot_id == snapshot.permission_snapshot_id
        assert journal.get_permission_snapshot_for_run("ws_1", "arun_1") == snapshot
        assert journal.get_agent_run("ws_1", "arun_1").permission_snapshot_id == "psnap_1"
        with pytest.raises(StorageError):
            journal.freeze_agent_run_permission_snapshot(
                "ws_1", "arun_1", snapshot.model_copy(update={"permission_snapshot_id": "psnap_2"})
            )
    finally:
        handle.close()


def test_permission_snapshot_must_match_the_frozen_agent_run_and_blocks_late_grants(tmp_path):
    store = OperationalStore(
        tmp_path / "state",
        retry_policy=BusyRetryPolicy(sleep=lambda _delay: None, rng=random.Random(0)),
        clock=FixedClock(NOW),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    try:
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
        with pytest.raises(StorageError):
            journal.freeze_agent_run_permission_snapshot(
                "ws_1",
                "arun_1",
                _snapshot(tool_schema_digest=sha256_digest("different-tools")),
            )
        journal.freeze_agent_run_permission_snapshot("ws_1", "arun_1", _snapshot())
        with pytest.raises(CapabilityGrantError, match="frozen"):
            CapabilityGrantService(journal, workspace_id="ws_1").create(_grant(), now=NOW)
    finally:
        handle.close()


def test_grant_revocation_invalidates_pending_approval_and_requests_active_cancellation(tmp_path):
    store = OperationalStore(
        tmp_path / "state",
        retry_policy=BusyRetryPolicy(sleep=lambda _delay: None, rng=random.Random(0)),
        clock=FixedClock(NOW),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)

    def intent(call_id: str, ordinal: int) -> PreparedIntent:
        return PreparedIntent(
            tool_name="run_command",
            call_id=call_id,
            ordinal=ordinal,
            arguments_digest=sha256_digest(f"args-{call_id}"),
            schema_digest=sha256_digest("schema"),
            permission_context_digest=sha256_digest("profile"),
            effect_class=EffectClass.UNCONFINED_EXTERNAL_EFFECT,
            requires_approval=True,
            preview=("unconfined_host: test preview",),
        )

    def execution(call_id: str, ordinal: int, *, state: ToolExecutionState, disposition=None):
        prepared = intent(call_id, ordinal)
        return DurableToolExecution(
            tool_execution_id=f"tex_{ordinal}",
            workspace_id="ws_1",
            session_id="ses_1",
            task_run_id="task_1",
            turn_id="turn_1",
            agent_run_id="arun_1",
            call_id=call_id,
            ordinal=ordinal,
            tool_name="run_command",
            intent=prepared,
            state=state,
            disposition=disposition or ToolExecutionDisposition.PENDING,
            permission_snapshot_id="psnap_1",
            grant_id="grt_1",
            isolation=IsolationLabel.UNCONFINED_HOST,
        )

    try:
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
        grant = _grant()
        journal.put_capability_grant("ws_1", grant)
        snapshot = _snapshot(
            access_scope=AccessScope.FULL_ACCESS,
            grant_id=grant.grant_id,
            grant_digest=capability_grant_digest(grant),
            granted_capabilities=grant.capabilities,
            capability_isolations=(
                CapabilityIsolation(
                    capability=CapabilityName.UNCONFINED_HOST_PROCESS,
                    isolation=IsolationLabel.UNCONFINED_HOST,
                ),
            ),
        )
        journal.freeze_agent_run_permission_snapshot("ws_1", "arun_1", snapshot)
        ordinary_intent = PreparedIntent(
            tool_name="calculate",
            call_id="ordinary-call",
            ordinal=4,
            arguments_digest=sha256_digest("ordinary-args"),
            schema_digest=sha256_digest("ordinary-schema"),
            permission_context_digest=sha256_digest("ordinary-policy"),
            effect_class=EffectClass.PURE,
        )
        ordinary = journal.put_execution(
            "ws_1",
            DurableToolExecution(
                tool_execution_id="tex_ordinary",
                workspace_id="ws_1",
                session_id="ses_1",
                task_run_id="task_1",
                turn_id="turn_1",
                agent_run_id="arun_1",
                call_id="ordinary-call",
                ordinal=4,
                tool_name="calculate",
                intent=ordinary_intent,
                permission_snapshot_id="psnap_1",
            ),
        )
        assert ordinary.grant_id is None
        assert ordinary.isolation is None
        pending = journal.put_execution(
            "ws_1", execution("call-1", 1, state=ToolExecutionState.AWAITING_APPROVAL)
        )
        journal.put_approval(
            "ws_1",
            DurableApproval(
                approval_id="apr_1",
                tool_execution_id=pending.tool_execution_id,
                intent_hash=intent_hash(pending.intent),
                tool_schema_digest=pending.intent.schema_digest,
                permission_context_digest=pending.intent.permission_context_digest,
                requested_scope="unconfined_host_process:run_command",
                preview=pending.intent.preview,
                preview_digest=approval_preview_digest(pending.intent.preview),
                permission_snapshot_id="psnap_1",
                grant_id="grt_1",
                isolation=IsolationLabel.UNCONFINED_HOST,
                created_at=NOW,
                expires_at=NOW + timedelta(minutes=5),
            ),
        )
        active = journal.put_execution(
            "ws_1", execution("call-2", 2, state=ToolExecutionState.EXECUTING)
        )
        completed = journal.put_execution(
            "ws_1",
            execution(
                "call-3",
                3,
                state=ToolExecutionState.CLOSED,
                disposition=ToolExecutionDisposition.UNKNOWN,
            ),
        )
        api = OperationalApplicationService(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=FixedClock(NOW).now,
        )
        result = api.revoke_grant(
            grant.grant_id,
            reason="stop elevated command",
            expected_row_version=1,
            command_id="cmd_revoke_effects",
        )
        assert result.value.revoked_at == NOW
        revoked_approval = journal.get_approval("ws_1", "apr_1")
        assert revoked_approval is not None
        assert revoked_approval.revoked_at == NOW
        assert revoked_approval.resolution.value == "denied"
        denied_pending = journal.get_execution("ws_1", pending.tool_execution_id)
        assert denied_pending is not None
        assert denied_pending.state is ToolExecutionState.CLOSED
        assert denied_pending.disposition is ToolExecutionDisposition.DENIED
        cancelled = journal.get_execution("ws_1", active.tool_execution_id)
        assert cancelled is not None
        assert cancelled.cancel_requested_at == NOW
        preserved = journal.get_execution("ws_1", completed.tool_execution_id)
        assert preserved is not None
        assert preserved.cancel_requested_at is None
        replay = api.revoke_grant(
            grant.grant_id,
            reason="stop elevated command",
            expected_row_version=1,
            command_id="cmd_revoke_effects",
        )
        assert replay.receipt is not None
        assert replay.receipt.disposition.value == "replay"
    finally:
        handle.close()


def test_full_access_handler_entry_uses_persisted_evidence_and_fails_closed_without_grant():
    execution = DurableToolExecution(
        tool_execution_id="tex_1",
        workspace_id="ws_1",
        session_id="ses_1",
        task_run_id="task_1",
        turn_id="turn_1",
        agent_run_id="arun_1",
        call_id="call-1",
        ordinal=1,
        tool_name="run_command",
        intent=PreparedIntent(
            tool_name="run_command",
            call_id="call-1",
            ordinal=1,
            arguments_digest=sha256_digest("args"),
            schema_digest=sha256_digest("schema"),
            permission_context_digest=sha256_digest("profile"),
            effect_class=EffectClass.UNCONFINED_EXTERNAL_EFFECT,
            requires_approval=True,
        ),
        state=ToolExecutionState.EXECUTING,
        permission_snapshot_id="psnap_1",
    )
    snapshot = _snapshot(
        access_scope=AccessScope.FULL_ACCESS,
        approval_mode=ApprovalMode.MANUAL,
        process_isolation=ProcessIsolation.HOST,
    )

    with pytest.raises(PermissionEvidenceError, match="capability grant"):
        assert_handler_may_enter(
            execution,
            None,
            now=NOW,
            permission_snapshot=snapshot,
        )
