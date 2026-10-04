"""The reproducible native regression chooses public refs and stops after one input."""

import json
import runpy
from pathlib import Path

import pytest

from morrow.core.models import ModelRef, ToolMessage


@pytest.fixture
def controller(monkeypatch):
    directory = Path(__file__).parents[1] / "evals/computer_use"
    monkeypatch.syspath_prepend(str(directory))
    return runpy.run_path(str(directory / "run_capabilities_fixture.py"))["FixtureController"]


async def decision(transport, result):
    messages = [
        ToolMessage(tool_call_id="fixture", content=json.dumps({"ok": True, "result": result}))
    ]
    return [
        e
        async for e in transport.stream(
            ModelRef(provider_id="fixture", model_id="fixture"), messages
        )
    ][-1].message


async def test_exact_attribute_target_is_same_public_reference(controller):
    transport = controller("postcondition_attribute", "hybrid")
    transport.turn = 2
    response = await decision(
        transport,
        {
            "observation_id": "cobs_current",
            "elements": [
                {"role": "axbutton", "label": "Increment", "element_ref": "celem_current"}
            ],
        },
    )
    args = json.loads(response.tool_calls[0].arguments)
    assert args["action"]["element_ref"] == "celem_current"
    assert args["action"]["postcondition"]["element_ref"] == "celem_current"
    assert transport.scripted_provider is True


async def test_semantic_scroll_uses_container_and_no_coordinates(controller):
    transport = controller("scroll", "semantic")
    transport.turn = 2
    response = await decision(
        transport,
        {
            "observation_id": "cobs_current",
            "elements": [
                {"role": "axscrollarea", "label": "fixture-scroll", "element_ref": "celem_scroll"}
            ],
        },
    )
    args = json.loads(response.tool_calls[0].arguments)
    assert args["action"] == {
        "type": "scroll",
        "element_ref": "celem_scroll",
        "direction": "down",
        "amount": 3,
    }


async def test_unknown_outcome_is_never_retried(controller):
    transport = controller("double_click", "hybrid")
    transport.turn = 3
    response = await decision(transport, {"outcome": {"status": "unknown"}})
    assert response.tool_calls == ()
