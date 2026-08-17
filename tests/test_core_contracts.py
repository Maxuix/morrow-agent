from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from morrow.adapters.registry import AdapterRegistry
from morrow.core.events import lifecycle_is_valid, make_event
from morrow.core.models import (
    AgentEvent,
    AssistantMessage,
    FinishReason,
    FunctionToolCall,
    Message,
    ModelEvent,
    ModelFinishReason,
    ModelRef,
    ProviderConfig,
    ProviderModelConfig,
    SystemMessage,
    ToolDefinition,
    ToolFunction,
    ToolMessage,
    UserMessage,
)
from morrow.testing import ScriptedModelProvider


def test_public_event_lifecycle_and_unknown_fields_are_tolerated():
    events = [
        make_event(
            event_type="turn.started", event_id="e1", session_id="s", turn_id="t", sequence=1
        ),
        make_event(
            event_type="text.delta",
            event_id="e2",
            session_id="s",
            turn_id="t",
            sequence=2,
            payload={"text": "hi"},
        ),
        make_event(
            event_type="turn.completed",
            event_id="e3",
            session_id="s",
            turn_id="t",
            sequence=3,
            payload={"finish_reason": FinishReason.CANCELLED.value},
        ),
    ]
    parsed = AgentEvent.model_validate({**events[0].model_dump(), "future_field": "ignored"})
    assert parsed.type == "turn.started"
    assert lifecycle_is_valid(events)


def test_second_adapter_is_dynamic_and_does_not_change_core():
    registry = AdapterRegistry()
    first = object()
    second = object()
    registry.register("first", lambda config, credential: first)
    registry.register("second", lambda config, credential: second)
    config = ProviderConfig(
        adapter="second",
        base_url="https://example.test",
        models={"m": ProviderModelConfig(api_model_id="m")},
    )
    assert registry.create(config, "secret") is second


def test_core_has_no_infrastructure_imports():
    forbidden = {"openai", "typer", "rich", "yaml", "keyring", "filelock", "prompt_toolkit"}
    core_root = Path(__file__).parents[1] / "src" / "morrow" / "core"
    for path in core_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        imported.update(
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert imported.isdisjoint(forbidden), (path, imported & forbidden)


@pytest.mark.asyncio
async def test_scripted_provider_preserves_order():
    provider = ScriptedModelProvider([["a", "b"]])
    messages = []
    chunks = []
    async for event in provider.stream(ModelRef(provider_id="p", model_id="m"), messages):
        chunks.append(event.text or event.kind)
    assert chunks == ["a", "b", "completed"]


# --- Stage 2 wire protocol contracts -----------------------------------------


def _tool_call(**overrides) -> FunctionToolCall:
    values = {"id": "call_1", "name": "lookup_record", "arguments": "{}"}
    values.update(overrides)
    return FunctionToolCall(**values)


def test_message_union_is_discriminated_by_role():
    adapter = TypeAdapter(Message)
    parsed = adapter.validate_python({"role": "user", "content": "hi"})
    assert isinstance(parsed, UserMessage)
    parsed = adapter.validate_python(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "name": "calculate", "arguments": "{}"}],
        }
    )
    assert isinstance(parsed, AssistantMessage)
    assert parsed.tool_calls[0].name == "calculate"


@pytest.mark.parametrize("name", ["", "has space", "中文", "x" * 65, "a/b"])
def test_function_tool_name_must_match_allowed_pattern(name):
    with pytest.raises(ValidationError):
        _tool_call(name=name)
    with pytest.raises(ValidationError):
        ToolFunction(name=name, description="d", parameters={"type": "object"})


def test_tool_definition_requires_non_empty_description_and_parameters():
    with pytest.raises(ValidationError):
        ToolFunction(name="lookup_record", description="  ", parameters={"type": "object"})
    definition = ToolDefinition(
        function=ToolFunction(
            name="lookup_record",
            description="Look up an injected in-memory record.",
            parameters={"type": "object", "properties": {}},
        )
    )
    assert definition.type == "function"
    with pytest.raises(ValidationError):
        ToolDefinition(function={"name": "x", "description": "d", "parameters": {}} | {"type": "?"})


def test_tool_call_ids_and_tool_message_ids_must_be_non_empty():
    with pytest.raises(ValidationError):
        _tool_call(id=" ")
    with pytest.raises(ValidationError):
        ToolMessage(tool_call_id="", content="{}")


def test_assistant_requires_content_or_tool_call_and_unique_ids():
    with pytest.raises(ValidationError):
        AssistantMessage(content=None, tool_calls=())
    with pytest.raises(ValidationError):
        AssistantMessage(content="   ")
    with pytest.raises(ValidationError):
        AssistantMessage(
            tool_calls=(_tool_call(id="same"), _tool_call(id="same", name="calculate"))
        )
    mixed = AssistantMessage(
        content="thinking",
        tool_calls=(_tool_call(), _tool_call(id="call_2", name="calculate")),
    )
    assert mixed.content == "thinking"
    assert len(mixed.tool_calls) == 2


def test_tool_call_arguments_stay_untouched_string():
    weird = '{"a": 1, "b": [2, 3] }  '
    call = _tool_call(arguments=weird)
    assert call.arguments == weird


def test_protocol_variants_reject_extras_and_are_immutable():
    with pytest.raises(ValidationError):
        UserMessage(content="hi", role="user", extra="no")
    with pytest.raises(ValidationError):
        SystemMessage(content="hi", future="no")
    with pytest.raises(ValidationError):
        ToolMessage(tool_call_id="c", content="{}", extra="no")
    with pytest.raises(ValidationError):
        _tool_call(extra="no")
    user = UserMessage(content="hi")
    with pytest.raises(ValidationError):
        user.content = "changed"


def test_assistant_tool_calls_reject_list_input_as_ordered_tuple_contract():
    assistant = AssistantMessage.model_validate(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "name": "calculate", "arguments": "{}"},
                {"id": "c2", "name": "lookup_record", "arguments": "{}"},
            ],
        }
    )
    assert isinstance(assistant.tool_calls, tuple)


def test_model_finish_reason_is_separate_from_public_finish_reason():
    assert ModelFinishReason.TOOL_CALLS.value == "tool_calls"
    assert not hasattr(FinishReason, "TOOL_CALLS")
    assert not hasattr(ModelFinishReason, "CANCELLED")


def test_completed_model_event_carries_assembled_assistant_and_normalized_reason():
    event = ModelEvent(
        kind="completed",
        finish_reason=ModelFinishReason.STOP,
        message=AssistantMessage(content="final"),
    )
    assert event.message is not None
    assert event.message.content == "final"
    with pytest.raises(ValidationError):
        ModelEvent(kind="completed", finish_reason="vendor_custom_reason")


def test_message_union_variants_cover_exactly_four_roles():
    adapter = TypeAdapter(Message)
    samples = {
        "system": SystemMessage(content="s"),
        "user": UserMessage(content="u"),
        "assistant": AssistantMessage(content="a"),
        "tool": ToolMessage(tool_call_id="c", content="{}"),
    }
    for sample in samples.values():
        assert adapter.validate_python(sample.model_dump()) == sample
    with pytest.raises(ValidationError):
        adapter.validate_python({"role": "tool_result", "content": "x"})
