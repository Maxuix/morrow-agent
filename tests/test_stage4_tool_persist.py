"""Persist-before-effect: Assistant tool calls and intents commit before dispatch."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.application.grants import CapabilityGrantService
from morrow.application.turns import SessionPersistence
from morrow.core.capabilities import (
    OperationIntent,
    OperationKind,
    PermissionPreset,
    PermissionProfile,
    RiskFlag,
    WorkspaceCapability,
)
from morrow.core.domain import DurableSession
from morrow.core.execution import EffectClass, ToolExecutionState
from morrow.core.faults import FaultPoint, InjectedFault, OnceFaultInjector
from morrow.core.models import (
    AssistantMessage,
    FunctionToolCall,
    ModelRef,
    ToolApprovalDecision,
)
from morrow.core.permissions import (
    UNCONFINED_HOST_WARNING,
    UNCONFINED_HOST_WARNING_DIGEST,
    CapabilityGrant,
    CapabilityName,
    GrantSource,
)
from morrow.core.store import StoreOpenMode
from morrow.runtime.agent import AgentLoop, _ToolCancellationRequested
from morrow.runtime.capabilities import CapabilityPolicy
from morrow.runtime.policy import ToolApproval, ToolExecutionPolicy
from morrow.runtime.session import Session
from morrow.runtime.tools import ToolExecutor, ToolRegistry, make_tool
from morrow.services.files import (
    WorkspaceFileService,
    WorkspaceMutationService,
    WorkspacePathResolver,
)
from morrow.testing import FixedClock, FixedIdSource, ScriptedModelProvider, make_context_builder


class _SpyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = "ok"


def _retry() -> BusyRetryPolicy:
    return BusyRetryPolicy(busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0))


def _open(tmp_path: Path, *, faults=None, mutation=None, session=None, clock=None):
    clock = clock or FixedClock()
    store = OperationalStore(
        tmp_path / "state",
        retry_policy=_retry(),
        clock=clock,
        maintenance_timeout=0,
    )
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    ids = FixedIdSource()
    session = session or Session(session_id="ses_1")
    persistence = SessionPersistence(
        workspace_id="ws_1",
        journal=journal,
        store_session=handle,
        id_source=ids,
        model=ModelRef(provider_id="p", model_id="m"),
        run_policy=make_context_builder().run_policy,
        runtime_instance_id="host-1",
        mutation=mutation,
        faults=faults,
        clock=clock,
    )
    journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
    persistence.attach(session)
    return store, handle, journal, session, persistence


def _spy_executor(calls: list[str]) -> ToolExecutor:
    async def handler(arguments: _SpyArguments) -> object:
        calls.append(arguments.value)
        return {"echo": arguments.value}

    registry = ToolRegistry()
    registry.register(
        make_tool(name="echo", description="echo", arguments_model=_SpyArguments, handler=handler)
    )
    return ToolExecutor(registry.snapshot(), make_context_builder().run_policy)


class _ApproveHost:
    async def request(self, request) -> ToolApprovalDecision:
        assert request.approval_id
        assert UNCONFINED_HOST_WARNING in request.preview
        return ToolApprovalDecision(approved=True)


def _host_executor(seen: list[str], session: Session, *, approval_port=None) -> ToolExecutor:
    class _HostArguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

        value: str = "ok"

    async def handler(arguments: _HostArguments) -> object:
        seen.append(arguments.value)
        return {"echo": arguments.value}

    def resolve(_arguments, _context) -> OperationIntent:
        return OperationIntent(
            kind=OperationKind.PROCESS,
            requires_host=True,
            risk_flags=(RiskFlag.OUTSIDE_WORKSPACE, RiskFlag.NETWORK),
            preview_summary=("opaque host command",),
        )

    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="run_command",
            description="run one opaque host command",
            arguments_model=_HostArguments,
            handler=handler,
            intent_resolver=resolve,
        )
    )
    return ToolExecutor(
        registry.snapshot(),
        make_context_builder().run_policy,
        approval_port=approval_port or _ApproveHost(),
        capability_policy=CapabilityPolicy(
            session.permission_profile,
            session.workspace_capability,
        ),
    )


@pytest.mark.asyncio
async def test_intents_are_visible_from_a_fresh_connection_before_handler(tmp_path):
    seen: list[str] = []
    store, handle, _journal, session, persistence = _open(tmp_path)
    try:
        provider = ScriptedModelProvider(
            [
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(
                            id="c1", name="echo", arguments=json.dumps({"value": "first"})
                        ),
                    )
                ),
                AssistantMessage(content="done"),
            ]
        )
        loop = AgentLoop(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            id_source=FixedIdSource(),
            tool_executor=_spy_executor(seen),
        )
        events = [item async for item in loop.run_task(session, "please")]
        assert events[-1].type == "turn.completed"
        assert seen == ["first"]
    finally:
        handle.close()

    with OperationalStore(
        tmp_path / "state", retry_policy=_retry(), clock=FixedClock(), maintenance_timeout=0
    ).open(StoreOpenMode.READ_WRITE) as reopened:
        journal = SqliteOperationalJournal(reopened)
        run = journal._read_one("SELECT agent_run_id FROM agent_runs LIMIT 1", ())
        listed = journal.list_executions("ws_1", agent_run_id=str(run[0]))
        assert len(listed) == 1
        durable_run = journal.get_agent_run("ws_1", str(run[0]))
        assert durable_run is not None
        assert durable_run.permission_snapshot_id is not None
        assert listed[0].permission_snapshot_id == durable_run.permission_snapshot_id
        assert journal.get_permission_snapshot_for_run("ws_1", str(run[0])) is not None
        assert listed[0].tool_name == "echo"
        assert listed[0].intent.effect_class is EffectClass.UNCONFINED_EXTERNAL_EFFECT
        assert listed[0].state is ToolExecutionState.CLOSED
        assert listed[0].disposition.value == "succeeded"
        records = journal.load_records("ws_1", "ses_1")
        assert any(
            record.payload.get("role") == "assistant" and record.payload.get("tool_calls")
            for record in records
        )


@pytest.mark.asyncio
async def test_handler_does_not_run_when_intent_commit_fails(tmp_path):
    seen: list[str] = []
    faults = OnceFaultInjector(FaultPoint.EXECUTION_INTENT_AFTER_COMMIT)
    store, handle, journal, session, persistence = _open(tmp_path, faults=faults)
    try:
        provider = ScriptedModelProvider(
            [
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(
                            id="c1", name="echo", arguments=json.dumps({"value": "nope"})
                        ),
                    )
                ),
                AssistantMessage(content="done"),
            ]
        )
        loop = AgentLoop(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            id_source=FixedIdSource(),
            tool_executor=_spy_executor(seen),
        )
        with pytest.raises(InjectedFault):
            [item async for item in loop.run_task(session, "please")]
        assert seen == []
        assert journal.list_executions("ws_1", agent_run_id="arun_1") == ()
        assert journal.get_session("ws_1", "ses_1").conversation_position == 1
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_write_file_intent_stores_pre_effect_hashes(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "notes.txt"
    target.write_text("old\n", encoding="utf-8")
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    mutation = WorkspaceMutationService(WorkspaceFileService(WorkspacePathResolver(project)))
    store, handle, journal, session, persistence = _open(tmp_path, mutation=mutation)
    try:
        from morrow.application.local_tools import make_write_file_tool
        from morrow.services.changes import ChangeSetService

        registry = ToolRegistry()
        registry.register(make_write_file_tool(mutation, ChangeSetService()))
        executor = ToolExecutor(registry.snapshot(), make_context_builder().run_policy)
        provider = ScriptedModelProvider(
            [
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(
                            id="c1",
                            name="write_file",
                            arguments=json.dumps(
                                {
                                    "path": "notes.txt",
                                    "content": "new\n",
                                    "mode": "replace",
                                    "expected_sha256": before,
                                }
                            ),
                        ),
                    )
                ),
                AssistantMessage(content="written"),
            ]
        )
        loop = AgentLoop(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            id_source=FixedIdSource(),
            tool_executor=executor,
        )
        [item async for item in loop.run_task(session, "rewrite")]
        run = journal._read_one("SELECT agent_run_id FROM agent_runs LIMIT 1", ())
        listed = journal.list_executions("ws_1", agent_run_id=str(run[0]))
        assert len(listed) == 1
        evidence = listed[0].intent.file_evidence
        assert len(evidence) == 1
        assert evidence[0].relative_path == "notes.txt"
        assert evidence[0].before_sha256 == before
        assert evidence[0].expected_after_sha256 == hashlib.sha256(b"new\n").hexdigest()
        assert evidence[0].expected_size == 4
        assert listed[0].state is ToolExecutionState.CLOSED
        assert listed[0].result_envelope is not None
        assert listed[0].result_envelope.ok is True
    finally:
        handle.close()


class _Deny:
    async def request(self, request) -> ToolApprovalDecision:
        assert request.approval_id
        return ToolApprovalDecision(approved=False)


@pytest.mark.asyncio
async def test_denied_approval_closes_without_handler(tmp_path):
    seen: list[str] = []

    async def handler(arguments: _SpyArguments) -> object:
        seen.append(arguments.value)
        return {"echo": arguments.value}

    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="echo",
            description="echo",
            arguments_model=_SpyArguments,
            handler=handler,
            execution_policy=ToolExecutionPolicy(approval=ToolApproval.REQUIRED),
        )
    )
    executor = ToolExecutor(
        registry.snapshot(), make_context_builder().run_policy, approval_port=_Deny()
    )
    store, handle, journal, session, persistence = _open(tmp_path)
    try:
        provider = ScriptedModelProvider(
            [
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(
                            id="c1", name="echo", arguments=json.dumps({"value": "secret"})
                        ),
                    )
                ),
                AssistantMessage(content="done"),
            ]
        )
        loop = AgentLoop(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            id_source=FixedIdSource(),
            tool_executor=executor,
        )
        [item async for item in loop.run_task(session, "please")]
        assert seen == []
        run = journal._read_one("SELECT agent_run_id FROM agent_runs LIMIT 1", ())
        listed = journal.list_executions("ws_1", agent_run_id=str(run[0]))
        assert listed[0].state is ToolExecutionState.CLOSED
        assert listed[0].disposition.value == "denied"
        approval = journal.get_approval_for_execution("ws_1", listed[0].tool_execution_id)
        assert approval.resolution.value == "denied"
        assert approval.consumed_at is None
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_full_access_host_policy_deny_closes_before_handler(tmp_path):
    seen: list[str] = []
    session = Session(
        session_id="ses_1",
        permission_profile=PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL),
        workspace_capability=WorkspaceCapability(workspace_id="ws_1", root=tmp_path),
    )
    store, handle, journal, session, _persistence = _open(
        tmp_path, session=session, clock=FixedClock()
    )
    try:
        provider = ScriptedModelProvider(
            [
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(
                            id="c1", name="run_command", arguments=json.dumps({"value": "denied"})
                        ),
                    )
                ),
                AssistantMessage(content="done"),
            ]
        )
        loop = AgentLoop(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            id_source=FixedIdSource(),
            tool_executor=_host_executor(seen, session),
        )
        events = [item async for item in loop.run_task(session, "please")]
        assert events[-1].type == "turn.completed"
        assert seen == []
        run = journal._read_one("SELECT agent_run_id FROM agent_runs LIMIT 1", ())
        execution = journal.list_executions("ws_1", agent_run_id=str(run[0]))[0]
        assert execution.intent.policy_verdict.value == "deny"
        assert execution.state is ToolExecutionState.CLOSED
        assert execution.disposition.value == "denied"
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_local_grant_provider_freezes_evidence_before_full_access_host_handler(tmp_path):
    seen: list[str] = []
    clock = FixedClock()
    session = Session(
        session_id="ses_1",
        permission_profile=PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL),
        workspace_capability=WorkspaceCapability(workspace_id="ws_1", root=tmp_path),
    )
    store, handle, journal, session, persistence = _open(tmp_path, session=session, clock=clock)
    try:

        def grant_provider(current_session: Session):
            assert current_session is session
            assert persistence.current_task_run_id == "task_1"
            assert persistence.current_agent_run_id == "arun_1"
            grant = CapabilityGrant(
                grant_id="grt_1",
                workspace_id="ws_1",
                task_run_id=persistence.current_task_run_id,
                agent_run_id=persistence.current_agent_run_id,
                capabilities=(CapabilityName.UNCONFINED_HOST_PROCESS,),
                granted_by=GrantSource.LOCAL_INTERFACE_COMMAND,
                command_id="cmd_1",
                reason="local interface approved one foreground Host run",
                preview_digest=UNCONFINED_HOST_WARNING_DIGEST,
                created_at=clock.now(),
                expires_at=clock.now() + timedelta(minutes=15),
            )
            return CapabilityGrantService(journal, workspace_id="ws_1").create(
                grant, now=clock.now()
            )

        provider = ScriptedModelProvider(
            [
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(
                            id="c1", name="run_command", arguments=json.dumps({"value": "granted"})
                        ),
                    )
                ),
                AssistantMessage(content="done"),
            ]
        )
        loop = AgentLoop(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            id_source=FixedIdSource(),
            clock=clock,
            tool_executor=_host_executor(seen, session),
            grant_provider=grant_provider,
        )
        session.pending_full_access_grant = True
        events = [item async for item in loop.run_task(session, "please")]
        assert events[-1].type == "turn.completed"
        assert seen == ["granted"]
        assert session.pending_full_access_grant is False
        run = journal.get_agent_run("ws_1", "arun_1")
        assert run is not None
        assert run.permission_snapshot_id is not None
        snapshot = journal.get_permission_snapshot_for_run("ws_1", "arun_1")
        assert snapshot is not None
        assert snapshot.grant_id == "grt_1"
        execution = journal.list_executions("ws_1", agent_run_id="arun_1")[0]
        assert execution.permission_snapshot_id == snapshot.permission_snapshot_id
        assert execution.grant_id == "grt_1"
        assert execution.isolation.value == "unconfined_host"
        assert execution.state is ToolExecutionState.CLOSED
        assert execution.disposition.value == "succeeded"
        approval = journal.get_approval_for_execution("ws_1", execution.tool_execution_id)
        assert approval is not None
        assert approval.grant_id == "grt_1"
        assert approval.permission_snapshot_id == snapshot.permission_snapshot_id
        assert approval.consumed_at == clock.now()
    finally:
        handle.close()


class _ExpireHostApproval:
    def __init__(self, clock) -> None:
        self.clock = clock

    async def request(self, request) -> ToolApprovalDecision:
        assert request.approval_id
        assert UNCONFINED_HOST_WARNING in request.preview
        self.clock.value += timedelta(minutes=16)
        return ToolApprovalDecision(approved=True)


@pytest.mark.asyncio
async def test_full_access_host_rechecks_expiry_after_approval_wait(tmp_path):
    seen: list[str] = []
    clock = FixedClock()
    session = Session(
        session_id="ses_1",
        permission_profile=PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL),
        workspace_capability=WorkspaceCapability(workspace_id="ws_1", root=tmp_path),
    )
    _store, handle, journal, session, persistence = _open(tmp_path, session=session, clock=clock)
    try:

        def grant_provider(_current_session: Session):
            grant = CapabilityGrant(
                grant_id="grt_1",
                workspace_id="ws_1",
                task_run_id=persistence.current_task_run_id,
                agent_run_id=persistence.current_agent_run_id,
                capabilities=(CapabilityName.UNCONFINED_HOST_PROCESS,),
                granted_by=GrantSource.LOCAL_INTERFACE_COMMAND,
                command_id="cmd_1",
                reason="local interface approved one foreground Host run",
                preview_digest=UNCONFINED_HOST_WARNING_DIGEST,
                created_at=clock.now(),
                expires_at=clock.now() + timedelta(minutes=15),
            )
            return CapabilityGrantService(journal, workspace_id="ws_1").create(
                grant, now=clock.now()
            )

        provider = ScriptedModelProvider(
            [
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(
                            id="c1", name="run_command", arguments=json.dumps({"value": "expired"})
                        ),
                    )
                ),
                AssistantMessage(content="done"),
            ]
        )
        loop = AgentLoop(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            id_source=FixedIdSource(),
            clock=clock,
            tool_executor=_host_executor(
                seen,
                session,
                approval_port=_ExpireHostApproval(clock),
            ),
            grant_provider=grant_provider,
        )
        session.pending_full_access_grant = True
        events = [item async for item in loop.run_task(session, "please")]
        assert events[-1].type == "turn.completed"
        assert seen == []
        execution = journal.list_executions("ws_1", agent_run_id="arun_1")[0]
        assert execution.state is ToolExecutionState.CLOSED
        assert execution.disposition.value == "denied"
        approval = journal.get_approval_for_execution("ws_1", execution.tool_execution_id)
        assert approval is not None
        assert approval.resolution.value == "expired"
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_active_execution_cancellation_request_cancels_the_underlying_handler():
    started = asyncio.Event()
    cancelled = asyncio.Event()
    current = SimpleNamespace(cancel_requested_at=None)

    class _Committer:
        def get_execution(self, _execution_id):
            return current

    session = Session(session_id="ses_1")
    session.committer = _Committer()
    loop = AgentLoop(
        ScriptedModelProvider(),
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
    )

    async def handler():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(
        loop._await_tool_with_cancellation(
            handler(), session, SimpleNamespace(tool_execution_id="tex_1")
        )
    )
    await started.wait()
    current.cancel_requested_at = FixedClock().now()
    with pytest.raises(_ToolCancellationRequested):
        await task
    assert cancelled.is_set()
