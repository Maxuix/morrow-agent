"""Lane A / P03: AgentLoop turn interruption with typed interrupted/user_pause.

Default behavior is guarded by the existing suites (test_agent_guardrails,
test_agent_tool_loop): plain chat passes no pause control and keeps its exact
path. These tests exercise the optional ``TurnPauseControl`` seam only.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel, ConfigDict

from morrow.core.events import lifecycle_is_valid
from morrow.core.execution_pause import LocalTurnPauseControl, TurnPauseSignal
from morrow.core.models import (
    AssistantMessage,
    FinishReason,
    FunctionToolCall,
    ModelEvent,
    ModelRef,
    ToolApprovalDecision,
    ToolApprovalRequest,
    ToolEffect,
)
from morrow.runtime.agent import AgentLoop
from morrow.runtime.conversation import TurnTerminalRecord
from morrow.runtime.session import Session
from morrow.runtime.tool_cycle import ToolCycleExecutor, ToolPauseRequested
from morrow.runtime.tools import ToolExecutor, ToolRegistry, make_tool
from morrow.testing import make_context_builder, make_run_policy

MODEL = ModelRef(provider_id="p", model_id="m")


class EchoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


def _call(call_id: str, value: str) -> FunctionToolCall:
    return FunctionToolCall(id=call_id, name="echo", arguments=json.dumps({"value": value}))


async def _collect(aiter):
    return [item async for item in aiter]


def _terminal(events) -> dict:
    completed = [event for event in events if event.type == "turn.completed"]
    assert len(completed) == 1
    return completed[0].payload


def _terminal_record(session: Session) -> TurnTerminalRecord:
    terminals = [
        record
        for record in session.log.snapshot().records
        if isinstance(record, TurnTerminalRecord)
    ]
    assert len(terminals) == 1
    return terminals[0]


class _HangingProvider:
    """A provider that never delivers the first token until closed."""

    def __init__(self, *, text_deltas: tuple[str, ...] = ()) -> None:
        self.text_deltas = text_deltas
        self.closed = False

    async def stream(self, model, messages, tools=(), *, generation=None):
        del model, messages, tools, generation
        gate = asyncio.Event()
        try:
            for delta in self.text_deltas:
                yield ModelEvent(kind="text_delta", text=delta)
                await asyncio.sleep(0)
            await gate.wait()
            yield ModelEvent(kind="activity", activity="reasoning")
        finally:
            self.closed = True


def _echo_executor(interrupt_after: int | None = None, control=None, calls=None):
    async def handler(arguments: EchoArguments) -> object:
        if calls is not None:
            calls.append(arguments.value)
        if interrupt_after is not None and len(calls or ()) >= interrupt_after:
            control.interrupt("s", TurnPauseSignal(control_generation=3, reason="user_interrupt"))
        return {"echo": arguments.value}

    registry = ToolRegistry()
    registry.register(
        make_tool(name="echo", description="echo", arguments_model=EchoArguments, handler=handler)
    )
    return ToolExecutor(registry.snapshot(), make_run_policy())


@pytest.mark.asyncio
async def test_a01_pause_with_no_first_token_interrupts_and_never_cancels_the_task():
    provider = _HangingProvider()
    control = LocalTurnPauseControl()
    session = Session(session_id="s")
    loop = AgentLoop(provider, MODEL, make_context_builder(), pause_control=control)

    events = []
    async for event in loop.run_task(session, "question"):
        events.append(event)
        if event.type == "status.changed" and event.payload.get("status") == "awaiting_model":
            control.interrupt("s", TurnPauseSignal(control_generation=1, reason="user_interrupt"))

    payload = _terminal(events)
    assert lifecycle_is_valid(events)
    assert payload["finish_reason"] == "interrupted"
    assert payload["stop_code"] == "user_pause"
    assert payload["reason"] == "user_pause"
    assert payload["control_generation"] == 1
    # The unbounded model wait was closed through the provider stream.
    assert provider.closed is True
    # The turn terminal is typed interrupted; no error event, no cancel cascade.
    terminal = _terminal_record(session)
    assert terminal.finish_reason is FinishReason.INTERRUPTED
    assert terminal.stop_code is None or terminal.stop_code.value == "user_pause"
    assert session.log.has_active_turn is False


@pytest.mark.asyncio
async def test_a02_pause_during_stream_keeps_partial_text_as_bounded_fragment():
    provider = _HangingProvider(text_deltas=("我正在把登录页", "改为蓝色", "，还没有完成" * 400))
    control = LocalTurnPauseControl()
    session = Session(session_id="s")
    loop = AgentLoop(provider, MODEL, make_context_builder(), pause_control=control)

    events = []
    async for event in loop.run_task(session, "question"):
        events.append(event)
        if event.type == "status.changed" and event.payload.get("status") == "model_responding":
            control.interrupt("s", TurnPauseSignal(control_generation=2, reason="user_interrupt"))

    payload = _terminal(events)
    assert lifecycle_is_valid(events)
    assert payload["finish_reason"] == "interrupted"
    assert payload["stop_code"] == "user_pause"
    # The fragment is bounded and never committed as a full reply.
    assert 0 < payload["text_length"] <= 8192
    messages = session.log.messages_view()
    assert not any(message.role == "assistant" for message in messages)


@pytest.mark.asyncio
async def test_a03_a04_pause_after_committed_result_keeps_results_and_marks_rest_not_executed():
    control = LocalTurnPauseControl()
    calls: list[str] = []
    provider_script = [
        AssistantMessage(tool_calls=(_call("c1", "one"), _call("c2", "two"), _call("c3", "three"))),
    ]
    from morrow.testing import ScriptedModelProvider

    provider = ScriptedModelProvider(provider_script)
    session = Session(session_id="s")
    loop = AgentLoop(
        provider,
        MODEL,
        make_context_builder(),
        tool_executor=_echo_executor(interrupt_after=1, control=control, calls=calls),
        pause_control=control,
    )

    events = await _collect(loop.run_task(session, "question"))

    payload = _terminal(events)
    assert lifecycle_is_valid(events)
    assert payload["finish_reason"] == "interrupted"
    assert calls == ["one"]
    # The committed result for c1 is preserved exactly once.
    tool_messages = [message for message in session.log.messages_view() if message.role == "tool"]
    assert [message.tool_call_id for message in tool_messages] == ["c1", "c2", "c3"]
    assert json.loads(tool_messages[0].content)["result"] == {"echo": "one"}
    # c2/c3 never executed: synthetic not-executed envelopes keep the
    # tool-call/result pairing grammar-legal.
    assert json.loads(tool_messages[1].content)["error"]["code"] == "cancelled"
    assert json.loads(tool_messages[2].content)["error"]["code"] == "cancelled"
    # c2/c3 never executed and are recorded as interrupted call IDs.
    terminal = _terminal_record(session)
    assert terminal.interrupted_call_ids == ("c2", "c3")
    assert payload["interrupted_call_ids"] == ["c2", "c3"]


@pytest.mark.asyncio
async def test_pause_signal_at_loop_top_blocks_new_model_admission():
    control = LocalTurnPauseControl()
    from morrow.testing import ScriptedModelProvider

    provider = ScriptedModelProvider([AssistantMessage(content="done")])
    session = Session(session_id="s")
    loop = AgentLoop(
        provider,
        MODEL,
        make_context_builder(),
        pause_control=control,
    )
    control.interrupt("s", TurnPauseSignal(control_generation=4, reason="user_interrupt"))

    events = await _collect(loop.run_task(session, "question"))

    payload = _terminal(events)
    assert lifecycle_is_valid(events)
    assert payload["finish_reason"] == "interrupted"
    # The model request was never admitted.
    assert provider.stream_calls == []


class _HangingApprovalPort:
    """Records the approval request and never resolves until released."""

    def __init__(self) -> None:
        self.requests: list[ToolApprovalRequest] = []
        self._gate = asyncio.Event()

    async def request(self, request: ToolApprovalRequest) -> ToolApprovalDecision:
        self.requests.append(request)
        await self._gate.wait()
        return ToolApprovalDecision(approved=True)

    def release(self) -> None:
        self._gate.set()


@pytest.mark.asyncio
async def test_a05_pause_wakes_approval_wait_without_resolving_it():
    executor_stub = ToolCycleExecutor.__new__(ToolCycleExecutor)
    executor_stub.tool_executor = None
    executor_stub.run_policy = make_run_policy()
    control = LocalTurnPauseControl()
    executor_stub.pause_control = control

    class _ApprovalExecutorStub:
        calls = 0

        async def request_approval(self, request):
            _ApprovalExecutorStub.calls += 1
            gate_hang = asyncio.Event()
            try:
                await gate_hang.wait()
            except asyncio.CancelledError:
                _ApprovalExecutorStub.calls -= 1
                raise
            return ToolApprovalDecision(approved=False)

    executor_stub.tool_executor = _ApprovalExecutorStub()
    session = Session(session_id="s")

    async def scenario():
        waiter_task = asyncio.ensure_future(
            executor_stub._wait_approval(
                session,
                ToolApprovalRequest(
                    call_id="c1",
                    effect=ToolEffect.PERSISTENT_WRITE,
                    preview=(),
                    reason_codes=(),
                    approval_id="ap1",
                ),
            )
        )
        await asyncio.sleep(0.01)
        control.interrupt("s", TurnPauseSignal(control_generation=9, reason="user_interrupt"))
        with pytest.raises(ToolPauseRequested) as err:
            await waiter_task
        assert err.value.signal.control_generation == 9

    await scenario()
    # The approval request was interrupted, not answered: nothing was approved
    # or denied on the user's behalf.
    assert _ApprovalExecutorStub.calls == 0


@pytest.mark.asyncio
async def test_approval_decision_delivered_before_pause_still_wins():
    class _QuickApprovalPort:
        async def request_approval(self, request: ToolApprovalRequest) -> ToolApprovalDecision:
            return ToolApprovalDecision(approved=False)

    control = LocalTurnPauseControl()
    session = Session(session_id="s")
    executor_stub = ToolCycleExecutor.__new__(ToolCycleExecutor)
    executor_stub.pause_control = control
    executor_stub.tool_executor = _QuickApprovalPort()
    control.interrupt("s", TurnPauseSignal(control_generation=1, reason="user_interrupt"))

    decision = await executor_stub._wait_approval(
        session,
        ToolApprovalRequest(
            call_id="c1", effect=ToolEffect.NONE, preview=(), reason_codes=(), approval_id="ap2"
        ),
    )
    assert decision is not None and decision.approved is False
