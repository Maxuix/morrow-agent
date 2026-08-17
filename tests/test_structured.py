from __future__ import annotations

import asyncio

import pytest

from morrow.application.context import ContextBudgetError
from morrow.application.structured import StructuredCompletionError, complete_structured
from morrow.core.models import (
    AssistantMessage,
    FinishReason,
    FunctionToolCall,
    ModelRef,
    ProtocolModel,
    ToolMessage,
    UserMessage,
)
from morrow.runtime.session import Session
from morrow.testing import ScriptedModelProvider, make_context_builder, seed_user_turn


class StructuredValue(ProtocolModel):
    current_goal: str


class RepairDeadlineProvider:
    def __init__(self) -> None:
        self.complete_calls = []
        self.block = asyncio.Event()

    async def complete(self, model, messages):
        del model
        self.complete_calls.append(list(messages))
        if len(self.complete_calls) == 1:
            return "not json"
        await self.block.wait()
        return '{"current_goal":"too late"}'


@pytest.mark.asyncio
async def test_structured_completion_repairs_once_through_context_builder():
    provider = ScriptedModelProvider(["not json", '{"current_goal":"done"}'])
    value, repaired = await complete_structured(
        provider,
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
        Session(session_id="s"),
        StructuredValue,
        "return structured value",
    )
    assert value.current_goal == "done"
    assert repaired is True
    assert len(provider.complete_calls) == 2


@pytest.mark.asyncio
async def test_structured_completion_wraps_context_budget_as_structured_error():
    session = Session(session_id="s")
    seed_user_turn(session, "x" * 200, assistant="y" * 200)
    with pytest.raises(StructuredCompletionError) as exc_info:
        await complete_structured(
            ScriptedModelProvider(['{"current_goal":"done"}']),
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(40),
            session,
            StructuredValue,
            "return structured value",
        )
    assert not isinstance(exc_info.value, ContextBudgetError)
    assert "预算" in str(exc_info.value)


@pytest.mark.asyncio
async def test_structured_completion_stops_after_one_repair():
    provider = ScriptedModelProvider(["bad", "still bad"])
    with pytest.raises(StructuredCompletionError):
        await complete_structured(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            Session(session_id="s"),
            StructuredValue,
            "return structured value",
        )
    assert len(provider.complete_calls) == 2


@pytest.mark.asyncio
async def test_structured_completion_uses_one_deadline_across_repair():
    provider = RepairDeadlineProvider()
    with pytest.raises(TimeoutError):
        await complete_structured(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            Session(session_id="s"),
            StructuredValue,
            "return structured value",
            timeout=0.01,
        )
    assert len(provider.complete_calls) == 2


@pytest.mark.asyncio
async def test_structured_repair_retains_original_instruction_and_target_schema():
    provider = ScriptedModelProvider(["not json", '{"current_goal":"done"}'])
    await complete_structured(
        provider,
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
        Session(session_id="s"),
        StructuredValue,
        "return structured value for the original task",
    )
    repair_prompt = provider.complete_calls[1][-1].content
    assert "return structured value for the original task" in repair_prompt
    assert "current_goal" in repair_prompt
    assert "校验" in repair_prompt


@pytest.mark.asyncio
async def test_structured_projection_never_consumes_tool_envelopes():
    session = Session(session_id="s")
    session.log.begin_turn(UserMessage(content="completed user"))
    session.log.append_assistant(
        AssistantMessage(
            content="intermediate-secret",
            tool_calls=(FunctionToolCall(id="c1", name="lookup", arguments="{}"),),
        )
    )
    session.log.append_tool_result("c1", "tool-envelope-secret")
    session.log.append_assistant(AssistantMessage(content="safe final"))
    session.log.finish_turn(FinishReason.STOP)
    provider = ScriptedModelProvider(['{"current_goal":"done"}'])

    await complete_structured(
        provider,
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
        session,
        StructuredValue,
        "return structured value",
    )

    wire = provider.complete_calls[0]
    assert all(not isinstance(message, ToolMessage) for message in wire)
    assert all(not getattr(message, "tool_calls", ()) for message in wire)
    assert "tool-envelope-secret" not in str(wire)
    assert "intermediate-secret" not in str(wire)
