"""Offline Stage 2 vertical E2E: model → tool → model → final answer."""

from __future__ import annotations

import asyncio
import json

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.bootstrap import build_application, build_session_application
from morrow.core.events import lifecycle_is_valid
from morrow.core.models import (
    AssistantMessage,
    FinishReason,
    FunctionToolCall,
    ModelRef,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from morrow.runtime.agent import AgentLoop
from morrow.runtime.session import Session
from morrow.runtime.tools import (
    ToolErrorCode,
    ToolExecutor,
    ToolRegistry,
    ToolSet,
    make_calculate_tool,
    make_lookup_record_tool,
)
from morrow.testing import ScriptedModelProvider, make_context_builder, make_run_policy

MODEL = ModelRef(provider_id="p", model_id="m")
DEMO_RECORDS = {
    ("plans", "pro"): {"monthly_price": 79.0},
    ("regions", "de"): {"tax_rate": 0.19},
}


def _demo_tool_set() -> ToolSet:
    registry = ToolRegistry()
    registry.register(make_lookup_record_tool(DEMO_RECORDS))
    registry.register(make_calculate_tool())
    return registry.snapshot()


def _call(call_id: str, name: str, arguments: str) -> FunctionToolCall:
    return FunctionToolCall(id=call_id, name=name, arguments=arguments)


def _story_script() -> list[AssistantMessage]:
    return [
        AssistantMessage(
            tool_calls=(_call("c1", "lookup_record", '{"dataset": "plans", "key": "pro"}'),)
        ),
        AssistantMessage(
            tool_calls=(_call("c2", "lookup_record", '{"dataset": "regions", "key": "de"}'),)
        ),
        AssistantMessage(
            tool_calls=(
                _call(
                    "c3",
                    "calculate",
                    '{"operation": "multiply", "values": [79.0, 3, 1.19]}',
                ),
            )
        ),
        AssistantMessage(content="含税三个月总价是 282.03 元。"),
    ]


async def _collect(aiter):
    return [item async for item in aiter]


def records_finish(session: Session) -> list[FinishReason]:
    return [
        record.finish_reason
        for record in session.log.snapshot().records
        if hasattr(record, "finish_reason")
    ]


def _assert_calls_pair_with_results(session: Session) -> None:
    messages = session.messages
    for index, message in enumerate(messages):
        if isinstance(message, AssistantMessage) and message.tool_calls:
            expected_ids = [call.id for call in message.tool_calls]
            following = []
            for later in messages[index + 1 :]:
                if isinstance(later, ToolMessage):
                    following.append(later.tool_call_id)
                elif isinstance(later, AssistantMessage):
                    break
            assert following[: len(expected_ids)] == expected_ids


@pytest.mark.asyncio
async def test_two_tool_step_story_completes_with_legal_history():
    provider = ScriptedModelProvider(_story_script())
    session = Session(session_id="s")
    loop = AgentLoop(
        provider,
        MODEL,
        make_context_builder(),
        tool_executor=ToolExecutor(_demo_tool_set(), make_run_policy()),
    )

    events = await _collect(loop.run_task(session, "帮我算一下 pro 方案三个月含税总价"))

    assert lifecycle_is_valid(events)
    assert events[0].type == "turn.started"
    assert [event.type for event in events].count("turn.completed") == 1
    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value

    tool_rounds = [
        message
        for message in session.messages
        if isinstance(message, AssistantMessage) and message.tool_calls
    ]
    assert len(tool_rounds) >= 2
    _assert_calls_pair_with_results(session)
    assert session.messages[-1].content == "含税三个月总价是 282.03 元。"
    calculate_result = [
        message
        for message in session.messages
        if isinstance(message, ToolMessage) and message.tool_call_id == "c3"
    ][0]
    assert json.loads(calculate_result.content)["result"]["value"] == pytest.approx(282.03)

    # Ordered provider requests: each later call sees the previous results.
    assert len(provider.stream_calls) == 4
    assert [type(m).__name__ for m in provider.stream_calls[0]] == [
        "SystemMessage",
        "SystemMessage",
        "UserMessage",
    ]
    for request_index, call_id in ((1, "c1"), (2, "c2"), (3, "c3")):
        last = provider.stream_calls[request_index][-1]
        assert isinstance(last, ToolMessage) and last.tool_call_id == call_id

    # Terminal records never enter provider payloads; every payload item is a Message.
    for request in provider.stream_calls:
        assert all(
            isinstance(m, (SystemMessage, UserMessage, AssistantMessage, ToolMessage))
            for m in request
        )

    # Tools are announced on every request.
    for sent in provider.stream_tools:
        assert {tool.function.name for tool in sent} == {
            "lookup_record",
            "calculate",
        }

    # One history source: the derived view equals the log projection.
    assert session.messages == session.log.messages_view()


@pytest.mark.asyncio
async def test_ordinary_chat_can_finish_without_calling_advertised_guarded_tools(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
    )
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(["你好，我是 Morrow。"])
    session_app = build_session_application(app, identity, provider=provider, model=MODEL)

    items = [item async for item in session_app.orchestrator.stream("你好")]
    events = [item for item in items if hasattr(item, "sequence")]
    assert lifecycle_is_valid(events)
    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert [type(m).__name__ for m in session_app.session.messages] == [
        "UserMessage",
        "AssistantMessage",
    ]
    assert records_finish(session_app.session) == [FinishReason.STOP]
    assert {tool.function.name for tool in provider.stream_tools[0]} == {
        "lookup_record",
        "calculate",
        "update_configuration",
    }


@pytest.mark.asyncio
async def test_mixed_content_intermediate_text_persists_but_only_final_completes():
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                content="我先查一下价格。",
                tool_calls=(_call("c1", "lookup_record", '{"dataset": "plans", "key": "pro"}'),),
            ),
            AssistantMessage(content="价格查到了，pro 每月 79 元。"),
        ]
    )
    session = Session(session_id="s")
    loop = AgentLoop(
        provider,
        MODEL,
        make_context_builder(),
        tool_executor=ToolExecutor(_demo_tool_set(), make_run_policy()),
    )

    events = await _collect(loop.run_task(session, "查一下 pro 价格"))

    assert lifecycle_is_valid(events)
    contents = [
        message.content for message in session.messages if isinstance(message, AssistantMessage)
    ]
    assert contents == ["我先查一下价格。", "价格查到了，pro 每月 79 元。"]
    assert records_finish(session) == [FinishReason.STOP]


class _GatedExecutor(ToolExecutor):
    def __init__(self, tool_set: ToolSet, gate_call_id: str) -> None:
        super().__init__(tool_set, make_run_policy())
        self.gate_call_id = gate_call_id
        self.started = asyncio.Event()

    async def execute(self, call, **kwargs):
        if call.id == self.gate_call_id:
            self.started.set()
            await asyncio.sleep(10)
        return await super().execute(call, **kwargs)


class _ExplodingExecutor(ToolExecutor):
    def __init__(self, tool_set: ToolSet, explode_call_id: str) -> None:
        super().__init__(tool_set, make_run_policy())
        self.explode_call_id = explode_call_id

    async def execute(self, call, **kwargs):
        if call.id == self.explode_call_id:
            raise RuntimeError("integrated explosion")
        return await super().execute(call, **kwargs)


@pytest.mark.asyncio
async def test_integrated_cancellation_mid_tool_batch_closes_and_recovers():
    provider = ScriptedModelProvider(_story_script())
    session = Session(session_id="s")
    executor = _GatedExecutor(_demo_tool_set(), gate_call_id="c3")
    loop = AgentLoop(provider, MODEL, make_context_builder(), tool_executor=executor)

    task = asyncio.create_task(_collect(loop.run_task(session, "算总价")))
    await executor.started.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    records = session.log.snapshot().records
    assert records[-1].finish_reason == FinishReason.CANCELLED
    assert session.log.unresolved_call_ids == ()
    tool_messages = [m for m in session.messages if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in tool_messages] == ["c1", "c2", "c3"]
    assert json.loads(tool_messages[2].content)["error"]["code"] == ToolErrorCode.CANCELLED.value
    assert json.loads(tool_messages[0].content)["result"] == {"monthly_price": 79.0}

    provider.responses = [AssistantMessage(content="重新计算完成。")]
    recovered = await _collect(loop.run_task(session, "再试一次"))
    assert recovered[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert session.messages[-1].content == "重新计算完成。"


@pytest.mark.asyncio
async def test_integrated_internal_failure_mid_tool_batch_closes_and_recovers():
    provider = ScriptedModelProvider(_story_script())
    session = Session(session_id="s")
    executor = _ExplodingExecutor(_demo_tool_set(), explode_call_id="c2")
    loop = AgentLoop(provider, MODEL, make_context_builder(), tool_executor=executor)

    events = await _collect(loop.run_task(session, "算总价"))

    assert lifecycle_is_valid(events)
    errors = [event for event in events if event.type == "error"]
    assert len(errors) == 1
    assert errors[0].payload["stop_code"] == "internal"
    assert events[-1].payload["finish_reason"] == FinishReason.ERROR.value
    tool_messages = [m for m in session.messages if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in tool_messages] == ["c1", "c2"]
    assert json.loads(tool_messages[1].content)["error"]["code"] == ToolErrorCode.INTERNAL.value
    assert records_finish(session) == [FinishReason.ERROR]

    provider.responses = [AssistantMessage(content="恢复后的回答。")]
    recovered = await _collect(loop.run_task(session, "继续"))
    assert recovered[-1].payload["finish_reason"] == FinishReason.STOP.value
