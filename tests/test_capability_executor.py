from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ConfigDict

from morrow.core.capabilities import (
    ChangeToolFact,
    OperationIntent,
    OperationKind,
    PermissionProfile,
    PolicyVerdict,
    RiskFlag,
    ToolHandlerOutcome,
    ToolRunContext,
    WorkspaceCapability,
)
from morrow.core.models import AssistantMessage, FunctionToolCall, ModelRef, ToolApprovalDecision
from morrow.runtime.agent import AgentLoop
from morrow.runtime.capabilities import CapabilityPolicy
from morrow.runtime.tools import ToolErrorCode, ToolExecutor, ToolRegistry, make_tool
from morrow.testing import ScriptedModelProvider, make_context_builder, make_run_policy


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = "value"


class Approval:
    def __init__(self, approved: bool = True) -> None:
        self.approved = approved
        self.requests = []

    async def request(self, request):
        self.requests.append(request)
        return ToolApprovalDecision(approved=self.approved)


def _call(name: str = "probe") -> FunctionToolCall:
    return FunctionToolCall(id="call-1", name=name, arguments='{"value":"secret"}')


def _policy():
    return CapabilityPolicy(
        PermissionProfile(),
        WorkspaceCapability(workspace_id="w1", root=__import__("pathlib").Path("/workspace")),
    )


@pytest.mark.asyncio
async def test_capability_denial_happens_before_preview_approval_and_handler():
    calls = []

    async def handler(_: Arguments):
        calls.append("handler")
        return ToolHandlerOutcome(payload={"ok": True})

    def resolve(_: Arguments, __):
        return OperationIntent(
            kind=OperationKind.WORKSPACE_READ,
            risk_flags=(RiskFlag.NETWORK,),
            preview_summary=("must not be shown",),
        )

    def preview(_):
        raise AssertionError("denied operation must not build an approval preview")

    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="probe",
            description="probe",
            arguments_model=Arguments,
            handler=handler,
            approval_preview=preview,
            intent_resolver=resolve,
        )
    )
    approval = Approval()
    outcome = await ToolExecutor(
        registry.snapshot(),
        make_run_policy(),
        approval_port=approval,
        capability_policy=_policy(),
    ).execute(_call())

    assert outcome.error_code is ToolErrorCode.PERMISSION_DENIED
    assert calls == []
    assert approval.requests == []


@pytest.mark.asyncio
async def test_policy_approval_request_contains_only_sanitized_reason_metadata():
    async def handler(_: Arguments):
        return ToolHandlerOutcome(payload={"saved": True})

    def resolve(_: Arguments, __):
        return OperationIntent(
            kind=OperationKind.WORKSPACE_WRITE,
            preview_summary=("write one bounded file",),
        )

    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="probe",
            description="probe",
            arguments_model=Arguments,
            handler=handler,
            intent_resolver=resolve,
        )
    )
    approval = Approval()
    outcome = await ToolExecutor(
        registry.snapshot(),
        make_run_policy(),
        approval_port=approval,
        capability_policy=_policy(),
    ).execute(_call())

    assert outcome.ok is True
    request = approval.requests[0]
    assert request.policy_verdict == "require_approval"
    assert request.reason_codes == ("workspace_write_approval_required",)
    assert request.preview == ("write one bounded file",)
    assert "secret" not in str(request.model_dump())


@pytest.mark.asyncio
async def test_typed_handler_result_uses_complete_semantic_json_budget():
    async def handler(_: Arguments):
        return ToolHandlerOutcome(payload={"content": "x" * 2000})

    def resolve(_: Arguments, __):
        return OperationIntent(kind=OperationKind.INTERNAL_READ)

    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="probe",
            description="probe",
            arguments_model=Arguments,
            handler=handler,
            intent_resolver=resolve,
        )
    )
    outcome = await ToolExecutor(
        registry.snapshot(), make_run_policy(), capability_policy=_policy()
    ).execute(_call(), result_limit=180)

    parsed = json.loads(outcome.envelope)
    assert outcome.ok is True
    assert outcome.truncated is True
    assert len(outcome.envelope) <= 180
    assert parsed["result"]["truncated"] is True
    assert parsed["result"]["field"] == "content"
    assert isinstance(parsed["result"]["content"], str)


@pytest.mark.asyncio
async def test_execute_with_context_collects_ordered_sanitized_facts():
    fact = ChangeToolFact(
        call_id="call-1",
        tool_name="probe",
        ordinal=1,
        approval_verdict=PolicyVerdict.ALLOW,
        relative_paths=("src/example.py",),
        operation="patch",
        changed_lines=1,
        changed_bytes=2,
    )

    async def handler(_: Arguments):
        return ToolHandlerOutcome(payload={"changed": True}, facts=(fact,))

    def resolve(_: Arguments, __):
        return OperationIntent(kind=OperationKind.WORKSPACE_READ)

    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="probe",
            description="probe",
            arguments_model=Arguments,
            handler=handler,
            intent_resolver=resolve,
        )
    )
    run = ToolRunContext(run_id="run-1", session_id="session-1")
    outcome = await ToolExecutor(
        registry.snapshot(), make_run_policy(), capability_policy=_policy()
    ).execute_with_context(_call(), run_context=run, ordinal=1, total=1)

    assert outcome.facts == (fact,)
    assert run.facts == (fact,)


@pytest.mark.asyncio
async def test_agent_loop_retains_only_latest_completed_run_facts_on_session():
    fact = ChangeToolFact(
        call_id="call-1",
        tool_name="probe",
        ordinal=1,
        approval_verdict=PolicyVerdict.ALLOW,
        relative_paths=("src/example.py",),
        operation="patch",
        changed_lines=1,
        changed_bytes=2,
    )

    async def handler(_: Arguments):
        return ToolHandlerOutcome(payload={"changed": True}, facts=(fact,))

    def resolve(_: Arguments, __):
        return OperationIntent(kind=OperationKind.WORKSPACE_READ)

    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="probe",
            description="probe",
            arguments_model=Arguments,
            handler=handler,
            intent_resolver=resolve,
        )
    )
    executor = ToolExecutor(registry.snapshot(), make_run_policy(), capability_policy=_policy())
    provider = ScriptedModelProvider(
        [
            AssistantMessage(tool_calls=(_call(),)),
            AssistantMessage(content="done"),
        ]
    )
    from morrow.runtime.session import Session

    session = Session(session_id="session-1")
    events = [
        event
        async for event in AgentLoop(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            tool_executor=executor,
        ).run_task(session, "change it")
    ]

    assert events[-1].payload["finish_reason"] == "stop"
    assert session.latest_run_id == events[0].turn_id
    assert session.latest_tool_facts == (fact,)
