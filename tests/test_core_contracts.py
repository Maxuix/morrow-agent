from __future__ import annotations

import ast
from pathlib import Path

import pytest

from morrow.adapters.registry import AdapterRegistry
from morrow.core.events import lifecycle_is_valid, make_event
from morrow.core.models import (
    AgentEvent,
    Decision,
    FinishReason,
    Handoff,
    ModelRef,
    ProviderConfig,
    ProviderModelConfig,
)
from morrow.testing import ScriptedModelProvider


def test_handoff_rejects_normalized_duplicate_decisions():
    with pytest.raises(ValueError):
        Handoff(
            current_goal="g",
            decisions=[Decision(decision="Use YAML"), Decision(decision=" use   yaml ")],
        )


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
