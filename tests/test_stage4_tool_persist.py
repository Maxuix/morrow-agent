"""Persist-before-effect: Assistant tool calls and intents commit before dispatch."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.application.turns import SessionPersistence
from morrow.core.domain import DurableSession
from morrow.core.execution import EffectClass, ToolExecutionState
from morrow.core.faults import FaultPoint, InjectedFault, OnceFaultInjector
from morrow.core.models import (
    AssistantMessage,
    FunctionToolCall,
    ModelRef,
    ToolApprovalDecision,
)
from morrow.core.store import StoreOpenMode
from morrow.runtime.agent import AgentLoop
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


def _open(tmp_path: Path, *, faults=None, mutation=None):
    store = OperationalStore(
        tmp_path / "state",
        retry_policy=_retry(),
        clock=FixedClock(),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    ids = FixedIdSource()
    session = Session(session_id="ses_1")
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
