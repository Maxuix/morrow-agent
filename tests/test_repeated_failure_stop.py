"""AgentLoop regressions for repeated tool failures.

The workflow-leaf incident burned ~110k tokens re-guessing an impossible
submit call. These tests pin the in-loop stops: a directed hint on the
second identical failure and a run-ending loop stop on the third
field-identified validation failure. Node request budgets stay a durable
admission concern (workflow_terminal=budget_exhausted) and are covered by
the stage7 workflow slices.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ConfigDict, model_validator

from morrow.core.events import lifecycle_is_valid
from morrow.core.models import (
    AgentStopCode,
    AssistantMessage,
    FinishReason,
    FunctionToolCall,
    ModelRef,
    ToolMessage,
)
from morrow.runtime.agent import AgentLoop
from morrow.runtime.session import Session
from morrow.runtime.tool_arguments import CuratedArgumentError
from morrow.runtime.tools import (
    ToolErrorCode,
    ToolExecutionError,
    ToolExecutor,
    ToolRegistry,
    make_tool,
)
from morrow.testing import ScriptedModelProvider, make_context_builder, make_run_policy

MODEL = ModelRef(provider_id="p", model_id="m")


class StubbornArguments(BaseModel):
    """Argument validation always fails the same way, whatever is sent."""

    model_config = ConfigDict(extra="forbid")

    value: str

    @model_validator(mode="after")
    def always_rejected(self):
        raise CuratedArgumentError(
            "empty_submission",
            "提交被拒绝：本节点通过最终答复完成。请直接把最终报告作为普通回复发送。",
        )


class GrepArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


async def _stubborn_handler(_: StubbornArguments) -> object:
    return {"never": "reached"}


async def _not_found_handler(_: GrepArguments) -> object:
    raise ToolExecutionError(ToolErrorCode.NOT_FOUND, "没有找到")


def _executor(tool) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(tool)
    return ToolExecutor(registry.snapshot(), make_run_policy())


def _stubborn_executor() -> ToolExecutor:
    return _executor(
        make_tool(
            name="submit_node_result",
            description="stub",
            arguments_model=StubbornArguments,
            handler=_stubborn_handler,
        )
    )


def _not_found_executor() -> ToolExecutor:
    return _executor(
        make_tool(
            name="grep",
            description="stub",
            arguments_model=GrepArguments,
            handler=_not_found_handler,
        )
    )


def _provider(*responses):
    return ScriptedModelProvider(list(responses))


def _call(call_id: str, name: str, value: str) -> FunctionToolCall:
    return FunctionToolCall(id=call_id, name=name, arguments=json.dumps({"value": value}))


async def _collect(aiter):
    return [item async for item in aiter]


def _tool_messages(session: Session) -> list[ToolMessage]:
    return [m for m in session.log.messages_view() if isinstance(m, ToolMessage)]


def _error_events(events):
    return [event for event in events if event.type == "error"]


@pytest.mark.asyncio
async def test_second_identical_failure_carries_a_directed_hint():
    provider = _provider(
        AssistantMessage(tool_calls=(_call("c1", "submit_node_result", "a"),)),
        AssistantMessage(tool_calls=(_call("c2", "submit_node_result", "a"),)),
        AssistantMessage(content="final report"),
    )
    session = Session(session_id="s")
    loop = AgentLoop(provider, MODEL, make_context_builder(), tool_executor=_stubborn_executor())

    events = await _collect(loop.run_task(session, "question"))

    assert lifecycle_is_valid(events)
    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
    envelopes = [json.loads(m.content)["error"] for m in _tool_messages(session)]
    assert len(envelopes) == 2
    assert "hint" not in envelopes[0]
    assert "第 2 次" in envelopes[1]["hint"]
    assert envelopes[0]["reason"] == "empty_submission"


@pytest.mark.asyncio
async def test_third_identical_validation_failure_stops_the_run():
    provider = _provider(
        AssistantMessage(tool_calls=(_call("c1", "submit_node_result", "a"),)),
        AssistantMessage(tool_calls=(_call("c2", "submit_node_result", "a"),)),
        AssistantMessage(tool_calls=(_call("c3", "submit_node_result", "a"),)),
        AssistantMessage(content="unreachable"),
    )
    session = Session(session_id="s")
    loop = AgentLoop(provider, MODEL, make_context_builder(), tool_executor=_stubborn_executor())

    events = await _collect(loop.run_task(session, "question"))

    assert lifecycle_is_valid(events)
    errors = _error_events(events)
    assert errors[-1].payload["stop_code"] == AgentStopCode.LOOP_DETECTED.value
    assert "submit_node_result" in errors[-1].payload["message"]
    # The failing results stay in history; the run is never dressed up as a
    # completion.
    assert [m.tool_call_id for m in _tool_messages(session)] == ["c1", "c2", "c3"]
    assert events[-1].payload["finish_reason"] == FinishReason.ERROR.value


@pytest.mark.asyncio
async def test_operational_failures_without_a_field_path_stay_hint_only():
    provider = _provider(
        *[AssistantMessage(tool_calls=(_call(f"c{i}", "grep", "v"),)) for i in range(1, 5)],
        AssistantMessage(content="adjusted and done"),
    )
    session = Session(session_id="s")
    loop = AgentLoop(provider, MODEL, make_context_builder(), tool_executor=_not_found_executor())

    events = await _collect(loop.run_task(session, "question"))

    assert lifecycle_is_valid(events)
    assert events[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert len(_tool_messages(session)) == 4
    assert not _error_events(events)
