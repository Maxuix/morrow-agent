"""Computer-use permission evidence, with v9 shell grants left byte-compatible."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.application.api import OperationalApplicationService
from morrow.application.api_permissions import (
    grant_create_command_payload,
    grant_created_event_payload,
)
from morrow.application.doctor import OperationalDoctor
from morrow.application.grants import CapabilityGrantError, validate_capability_subset
from morrow.application.turn_permissions import (
    RunPermissionCoordinator,
    build_permission_snapshot,
)
from morrow.core.application import ApplicationError
from morrow.core.capabilities import (
    OperationIntent,
    OperationKind,
    PermissionPreset,
    PermissionProfile,
    PolicyVerdict,
    WorkspaceCapability,
)
from morrow.core.computer_use import (
    ComputerUseAppIdentity,
    ComputerUseContractError,
    ComputerUseDelivery,
    ComputerUseImageShare,
    ComputerUseOperation,
    ComputerUseScope,
    ComputerUseWindowBoundary,
    ComputerUseWindowIdentity,
    decode_computer_use_scope,
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
    DurableToolExecution,
    EffectClass,
    PreparedIntent,
    ToolExecutionState,
    assert_handler_may_enter,
)
from morrow.core.models import ModelRef
from morrow.core.permissions import (
    COMPUTER_USE_HOST_WARNING_DIGEST,
    COMPUTER_USE_PERMISSION_SCHEMA_VERSION,
    COMPUTER_USE_POLICY_VERSION,
    PERMISSION_SCHEMA_VERSION,
    CapabilityGrant,
    CapabilityName,
    IsolationLabel,
    PermissionEvidenceError,
    capability_grant_digest,
    decode_capability_payload,
    encode_capability_payload,
)
from morrow.core.store import StorageError
from morrow.runtime.capabilities import CapabilityPolicy, CapabilityReason
from morrow.runtime.session import Session
from morrow.testing import FixedClock, FixedIdSource

NOW = datetime(2026, 1, 1, tzinfo=UTC)
FULL_ACCESS_PROFILE_DIGEST = sha256_digest(
    canonical_json_bytes(
        PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL).model_dump(mode="json")
    )
)


def _scope(**overrides) -> ComputerUseScope:
    values = {
        "generation": 1,
        "workspace_id": "ws_1",
        "task_run_id": "task_1",
        "agent_run_id": "arun_1",
        "apps": (ComputerUseAppIdentity(bundle_id="com.example.Notes"),),
        "window_boundary": ComputerUseWindowBoundary.WINDOW,
        "operations": (ComputerUseOperation.OBSERVE, ComputerUseOperation.ACTION),
        "delivery": ComputerUseDelivery.FOREGROUND,
        "image_share": ComputerUseImageShare.NONE,
    }
    values.update(overrides)
    return decode_computer_use_scope({"schema_version": 1, **values})


def _grant(**overrides) -> CapabilityGrant:
    values = {
        "grant_id": "grt_1",
        "workspace_id": "ws_1",
        "task_run_id": "task_1",
        "agent_run_id": "arun_1",
        "capabilities": (CapabilityName.COMPUTER_USE_HOST,),
        "command_id": "cmd_1",
        "reason": "Observe the named notes window",
        "preview_digest": COMPUTER_USE_HOST_WARNING_DIGEST,
        "policy_version": COMPUTER_USE_POLICY_VERSION,
        "schema_version": COMPUTER_USE_PERMISSION_SCHEMA_VERSION,
        "computer_use_scope": _scope(),
        "created_at": NOW,
        "expires_at": NOW + timedelta(minutes=15),
    }
    values.update(overrides)
    return CapabilityGrant(**values)


def _shell_grant() -> CapabilityGrant:
    return CapabilityGrant(
        grant_id="grt_1",
        workspace_id="ws_1",
        task_run_id="task_1",
        agent_run_id="arun_1",
        capabilities=(CapabilityName.UNCONFINED_HOST_PROCESS,),
        command_id="cmd_1",
        reason="Run one explicitly approved validation",
        preview_digest=sha256_digest("preview"),
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )


def _intent(
    tool_name: str, effect: EffectClass, *, approval: bool, ordinal: int = 1
) -> PreparedIntent:
    return PreparedIntent(
        tool_name=tool_name,
        call_id=f"call-{tool_name}",
        ordinal=ordinal,
        arguments_digest=sha256_digest(tool_name),
        schema_digest=sha256_digest("schema"),
        permission_context_digest=sha256_digest("profile"),
        effect_class=effect,
        requires_approval=approval,
        preview=(f"{tool_name} preview",),
    )


def _execution(tool_name: str, effect: EffectClass, *, approval: bool, isolation, grant_id):
    intent = _intent(tool_name, effect, approval=approval)
    return DurableToolExecution(
        tool_execution_id="tex_1",
        workspace_id="ws_1",
        session_id="ses_1",
        task_run_id="task_1",
        turn_id="turn_1",
        agent_run_id="arun_1",
        call_id=intent.call_id,
        ordinal=1,
        tool_name=tool_name,
        intent=intent,
        state=ToolExecutionState.EXECUTING,
        permission_snapshot_id="psnap_1",
        grant_id=grant_id,
        isolation=isolation,
    )


def _open(tmp_path):
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
    return store, handle, journal, api


def test_v9_bytes_and_digest_stay_stable_when_schema10_changes():
    shell = _shell_grant()
    encoded = encode_capability_payload(
        schema_version=PERMISSION_SCHEMA_VERSION,
        capabilities=shell.capabilities,
        computer_use_scope=None,
    )
    assert encoded == '["unconfined_host_process"]'
    names, scope = decode_capability_payload(encoded, schema_version=PERMISSION_SCHEMA_VERSION)
    assert names == shell.capabilities
    assert scope is None
    historical = {
        "grant_id": shell.grant_id,
        "workspace_id": shell.workspace_id,
        "task_run_id": shell.task_run_id,
        "agent_run_id": shell.agent_run_id,
        "capabilities": ("unconfined_host_process",),
        "granted_by": "local_interface_command",
        "command_id": shell.command_id,
        "reason": shell.reason,
        "preview_digest": shell.preview_digest,
        "policy_version": "stage4-permissions-v1",
        "schema_version": 9,
        "created_at": shell.created_at.isoformat(),
        "expires_at": shell.expires_at.isoformat(),
    }
    assert capability_grant_digest(shell) == sha256_digest(canonical_json_bytes(historical))
    revoked = shell.model_copy(
        update={
            "revoked_at": NOW + timedelta(minutes=1),
            "revocation_reason": "user stopped the run",
            "row_version": 2,
        }
    )
    assert capability_grant_digest(revoked) == capability_grant_digest(shell)
    computer = _grant()
    assert capability_grant_digest(computer) != capability_grant_digest(shell)
    changed = computer.model_copy(update={"computer_use_scope": _scope(generation=2)})
    assert capability_grant_digest(changed) != capability_grant_digest(computer)
    payload = grant_create_command_payload(
        task_run_id="task_1",
        agent_run_id="arun_1",
        capabilities=shell.capabilities,
        reason=shell.reason,
        preview_digest=shell.preview_digest,
        expires_at=None,
        grant_id=None,
        computer_use_scope=None,
    )
    assert set(payload) == {
        "task_run_id",
        "agent_run_id",
        "capabilities",
        "reason",
        "preview_digest",
        "expires_at",
        "grant_id",
    }
    event = grant_created_event_payload(computer)
    assert "computer_use_scope_digest" in event
    assert "com.example.Notes" not in str(event)
    with pytest.raises(ValidationError, match="do not match"):
        _grant(computer_use_scope=_scope(agent_run_id="arun_2"))
    with pytest.raises(CapabilityGrantError, match="computer use is disabled"):
        validate_capability_subset((CapabilityName.COMPUTER_USE_HOST,))
    with pytest.raises(CapabilityGrantError, match="Stage 4 only grants unconfined_host_process"):
        validate_capability_subset(("network",))
    with pytest.raises(CapabilityGrantError, match="requires a scope"):
        validate_capability_subset((CapabilityName.COMPUTER_USE_HOST,), computer_use_enabled=True)
    selected = _scope(
        schema_version=2,
        windows=(
            ComputerUseWindowIdentity(
                app=ComputerUseAppIdentity(bundle_id="com.example.Notes"),
                window_identity="cwin_selected",
            ),
        ),
    )
    ordered = validate_capability_subset(
        (CapabilityName.COMPUTER_USE_HOST, CapabilityName.UNCONFINED_HOST_PROCESS),
        computer_use_enabled=True,
        computer_use_scope=selected,
    )
    assert ordered == (
        CapabilityName.UNCONFINED_HOST_PROCESS,
        CapabilityName.COMPUTER_USE_HOST,
    )


def test_policy_and_settings_do_not_grant_desktop_access(tmp_path):
    workspace = WorkspaceCapability(workspace_id="ws_1", root=tmp_path)
    observe = OperationIntent(kind=OperationKind.COMPUTER_OBSERVE)
    action = OperationIntent(kind=OperationKind.COMPUTER_ACTION)
    manual = CapabilityPolicy(PermissionProfile.from_preset(PermissionPreset.MANUAL), workspace)
    denied = manual.evaluate(observe, allow_computer_use=True)
    assert denied.verdict is PolicyVerdict.DENY
    assert denied.reason_codes == (CapabilityReason.COMPUTER_USE_NOT_ALLOWED,)
    full = CapabilityPolicy(
        PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL), workspace
    )
    assert full.evaluate(observe).reason_codes == (CapabilityReason.COMPUTER_USE_GRANT_REQUIRED,)
    assert full.evaluate(observe, allow_computer_use=True).verdict is PolicyVerdict.ALLOW
    approved = full.evaluate(action, allow_computer_use=True)
    assert approved.verdict is PolicyVerdict.REQUIRE_APPROVAL
    assert approved.reason_codes == (CapabilityReason.COMPUTER_ACTION_APPROVAL_REQUIRED,)
    readonly = CapabilityPolicy(
        PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL),
        workspace.model_copy(update={"read_only": True}),
    )
    assert readonly.evaluate(observe, allow_computer_use=True).reason_codes == (
        CapabilityReason.READ_ONLY_SESSION,
    )
    external = full.evaluate(
        OperationIntent(kind=OperationKind.EXTERNAL_EFFECT), allow_computer_use=True
    )
    assert external.reason_codes == (CapabilityReason.EXTERNAL_EFFECT_NOT_ENABLED,)
    store, handle, journal, _api = _open(tmp_path)
    try:
        assert journal.list_capability_grants("ws_1") == ()
    finally:
        handle.close()
    assert store.layout.data_root.exists()


def test_create_grant_rejects_disabled_wrong_digest_and_a_second_grant(tmp_path):
    _store, handle, _journal, api = _open(tmp_path)
    try:
        with pytest.raises(ApplicationError, match="computer use is disabled"):
            api.create_grant(
                task_run_id="task_1",
                agent_run_id="arun_1",
                capabilities=(CapabilityName.COMPUTER_USE_HOST,),
                reason="Observe the named notes window",
                preview_digest=COMPUTER_USE_HOST_WARNING_DIGEST,
                command_id="cmd_disabled",
            )
        selected = _scope(
            schema_version=2,
            windows=(
                ComputerUseWindowIdentity(
                    app=ComputerUseAppIdentity(bundle_id="com.example.Notes"),
                    window_identity="cwin_selected",
                ),
            ),
        )
        with pytest.raises(ApplicationError, match="selected windows"):
            api.create_grant(
                task_run_id="task_1",
                agent_run_id="arun_1",
                capabilities=(CapabilityName.COMPUTER_USE_HOST,),
                reason="Observe the named notes window",
                preview_digest=COMPUTER_USE_HOST_WARNING_DIGEST,
                command_id="cmd_legacy",
                computer_use_enabled=True,
                computer_use_scope=_scope(),
            )
        with pytest.raises(ApplicationError, match="warning digest"):
            api.create_grant(
                task_run_id="task_1",
                agent_run_id="arun_1",
                capabilities=(CapabilityName.COMPUTER_USE_HOST,),
                reason="Observe the named notes window",
                preview_digest=sha256_digest("preview"),
                command_id="cmd_digest",
                computer_use_enabled=True,
                computer_use_scope=selected,
            )
        created = api.create_grant(
            task_run_id="task_1",
            agent_run_id="arun_1",
            capabilities=(CapabilityName.COMPUTER_USE_HOST,),
            reason="Observe the named notes window",
            preview_digest=COMPUTER_USE_HOST_WARNING_DIGEST,
            command_id="cmd_computer",
            computer_use_enabled=True,
            computer_use_scope=selected,
        )
        with pytest.raises(ApplicationError) as conflict:
            api.create_grant(
                task_run_id="task_1",
                agent_run_id="arun_1",
                capabilities=(CapabilityName.COMPUTER_USE_HOST,),
                reason="A second desktop authority",
                preview_digest=COMPUTER_USE_HOST_WARNING_DIGEST,
                command_id="cmd_second",
                computer_use_enabled=True,
                computer_use_scope=selected.model_copy(update={"generation": 2}),
            )
        assert conflict.value.code.value == "conflict"
        assert created.value.computer_use_scope == selected
    finally:
        handle.close()


def test_legacy_app_scope_cannot_create_a_new_grant(tmp_path):
    _store, handle, _journal, api = _open(tmp_path)
    try:
        with pytest.raises(ApplicationError, match="selected windows"):
            api.create_grant(
                task_run_id="task_1",
                agent_run_id="arun_1",
                capabilities=(CapabilityName.COMPUTER_USE_HOST,),
                reason="Observe the named notes window",
                preview_digest=COMPUTER_USE_HOST_WARNING_DIGEST,
                command_id="cmd_legacy",
                computer_use_enabled=True,
                computer_use_scope=_scope(),
            )
    finally:
        handle.close()


def test_computer_grant_roundtrip_isolation_and_revocation(tmp_path):
    _store, handle, journal, api = _open(tmp_path)
    scope = _scope(
        schema_version=2,
        windows=(
            ComputerUseWindowIdentity(
                app=ComputerUseAppIdentity(bundle_id="com.example.Notes"),
                window_identity="cwin_selected",
            ),
        ),
    )
    try:
        created = api.create_grant(
            task_run_id="task_1",
            agent_run_id="arun_1",
            capabilities=(CapabilityName.COMPUTER_USE_HOST,),
            reason="Observe the named notes window",
            preview_digest=COMPUTER_USE_HOST_WARNING_DIGEST,
            command_id="cmd_computer",
            computer_use_enabled=True,
            computer_use_scope=scope,
        )
        raw = handle.run_read(
            lambda executor: executor.execute("SELECT capabilities_json FROM capability_grants")
        )
        assert '"computer_use_host"' in raw[0][0]
        assert '"windows"' in raw[0][0]
        session = Session(
            session_id="ses_1",
            permission_profile=PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL),
            workspace_capability=WorkspaceCapability(workspace_id="ws_1", root=tmp_path),
        )
        coordinator = RunPermissionCoordinator(
            journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=FixedClock(NOW).now,
        )
        snapshot = coordinator.freeze(
            session,
            agent_run_id="arun_1",
            task_run_id="task_1",
            turn_id="turn_1",
            now=NOW,
        )
        assert snapshot.schema_version == COMPUTER_USE_PERMISSION_SCHEMA_VERSION
        assert snapshot.policy_version == COMPUTER_USE_POLICY_VERSION
        assert snapshot.computer_use_scope == created.value.computer_use_scope
        assert snapshot.isolation_label is IsolationLabel.COMPUTER_USE_HOST
        assert coordinator.active_grant_evidence(snapshot, now=NOW) == (None, None)
        assert (
            coordinator.has_active_unconfined_grant(
                _execution(
                    "computer_observe",
                    EffectClass.BOUNDED_EXTERNAL_READ,
                    approval=False,
                    isolation=IsolationLabel.COMPUTER_USE_HOST,
                    grant_id=created.value.grant_id,
                ),
                now=NOW,
            )
            is False
        )
        stored = journal.put_execution(
            "ws_1",
            _execution(
                "computer_observe",
                EffectClass.BOUNDED_EXTERNAL_READ,
                approval=False,
                isolation=IsolationLabel.COMPUTER_USE_HOST,
                grant_id=created.value.grant_id,
            ).model_copy(update={"permission_snapshot_id": snapshot.permission_snapshot_id}),
        )
        with pytest.raises(StorageError, match="does not authorize a host shell"):
            journal.put_execution(
                "ws_1",
                DurableToolExecution(
                    tool_execution_id="tex_2",
                    workspace_id="ws_1",
                    session_id="ses_1",
                    task_run_id="task_1",
                    turn_id="turn_1",
                    agent_run_id="arun_1",
                    call_id="call-bash",
                    ordinal=2,
                    tool_name="bash",
                    intent=_intent(
                        "bash",
                        EffectClass.UNCONFINED_EXTERNAL_EFFECT,
                        approval=True,
                        ordinal=2,
                    ),
                    state=ToolExecutionState.AWAITING_APPROVAL,
                    permission_snapshot_id=snapshot.permission_snapshot_id,
                    grant_id=created.value.grant_id,
                    isolation=IsolationLabel.UNCONFINED_HOST,
                ),
            )
        mutated = created.value.model_copy(
            update={
                "computer_use_scope": _scope(generation=2),
                "revoked_at": NOW + timedelta(minutes=1),
                "revocation_reason": "widen the window",
                "row_version": 2,
            }
        )
        with pytest.raises(StorageError, match="immutable"):
            journal.save_capability_grant("ws_1", mutated, expected_row_version=1)
        report = OperationalDoctor(_store).inspect("ws_1")
        assert "permission_grant_evidence" not in {issue.code for issue in report.issues}
        api.revoke_grant(
            created.value.grant_id,
            reason="user stopped the desktop run",
            expected_row_version=1,
            command_id="cmd_revoke",
        )

        class Port:
            calls = 0

            def observe_window(self, request):
                self.calls += 1

        port = Port()
        with pytest.raises(PermissionEvidenceError):
            coordinator.assert_handler_may_enter(stored, now=NOW)
            port.observe_window(None)
        assert port.calls == 0
    finally:
        handle.close()


def test_shell_and_computer_grants_do_not_imply_each_other(tmp_path):
    session = Session(
        session_id="ses_1",
        permission_profile=PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL),
        workspace_capability=WorkspaceCapability(workspace_id="ws_1", root=tmp_path),
    )
    base = AgentRunSnapshot(
        model=ModelRef(provider_id="p", model_id="m"),
        provider_id="p",
        run_policy_digest=sha256_digest("policy"),
        tool_schema_digest=sha256_digest("tools"),
        permission_profile_digest=FULL_ACCESS_PROFILE_DIGEST,
        runtime_instance_id="runtime-1",
    )
    dual = _grant(
        capabilities=(
            CapabilityName.UNCONFINED_HOST_PROCESS,
            CapabilityName.COMPUTER_USE_HOST,
        )
    )
    snapshot = build_permission_snapshot(
        session,
        workspace_id="ws_1",
        base_snapshot=base,
        permission_snapshot_id="psnap_1",
        task_run_id="task_1",
        turn_id="turn_1",
        agent_run_id="arun_1",
        grant=dual,
        created_at=NOW,
    )
    assert snapshot.isolation_label is None
    assert snapshot.isolation_for_tool("bash") is IsolationLabel.UNCONFINED_HOST
    assert snapshot.isolation_for_tool("computer_action") is IsolationLabel.COMPUTER_USE_HOST
    computer = _execution(
        "computer_observe",
        EffectClass.BOUNDED_EXTERNAL_READ,
        approval=False,
        isolation=IsolationLabel.COMPUTER_USE_HOST,
        grant_id=dual.grant_id,
    )
    with pytest.raises(PermissionEvidenceError, match="does not authorize computer use"):
        assert_handler_may_enter(
            computer,
            None,
            now=NOW,
            permission_snapshot=build_permission_snapshot(
                session,
                workspace_id="ws_1",
                base_snapshot=base,
                permission_snapshot_id="psnap_1",
                task_run_id="task_1",
                turn_id="turn_1",
                agent_run_id="arun_1",
                grant=_shell_grant(),
                created_at=NOW,
            ),
            grant=_shell_grant(),
        )
    with pytest.raises(PermissionEvidenceError, match="does not authorize a host shell"):
        assert_handler_may_enter(
            _execution(
                "bash",
                EffectClass.UNCONFINED_EXTERNAL_EFFECT,
                approval=True,
                isolation=IsolationLabel.UNCONFINED_HOST,
                grant_id="grt_1",
            ),
            None,
            now=NOW,
            permission_snapshot=snapshot,
            grant=_grant(),
        )
    with pytest.raises(ComputerUseContractError) as wider:
        coordinator_scope = _scope(
            apps=(
                ComputerUseAppIdentity(bundle_id="com.example.Notes"),
                ComputerUseAppIdentity(bundle_id="com.example.Mail"),
            )
        )
        from morrow.core.computer_use import reject_scope_expansion

        reject_scope_expansion(_scope(), coordinator_scope)
    assert wider.value.code == "scope_expansion"


def test_shell_snapshot_json_remains_a_list(tmp_path):
    _store, handle, journal, api = _open(tmp_path)
    try:
        created = api.create_grant(
            task_run_id="task_1",
            agent_run_id="arun_1",
            capabilities=(CapabilityName.UNCONFINED_HOST_PROCESS,),
            reason="Run one explicitly approved validation",
            preview_digest=sha256_digest("preview"),
            command_id="cmd_shell",
        )
        raw = handle.run_read(
            lambda executor: executor.execute(
                "SELECT capabilities_json, schema_version FROM capability_grants"
            )
        )
        assert raw == (('["unconfined_host_process"]', 9),)
        assert created.value.schema_version == PERMISSION_SCHEMA_VERSION
        assert created.value.computer_use_scope is None
        assert journal.get_capability_grant("ws_1", created.value.grant_id) == created.value
    finally:
        handle.close()
