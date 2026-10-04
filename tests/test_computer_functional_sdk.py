"""Exact-object readback and newly supported variants use one admitted input."""

import json
from types import SimpleNamespace

import pytest

from morrow.adapters.computer_use.action_inputs import (
    FUNCTIONAL_SDK_VERSION,
    NativeAttributeInput,
    read_exact_enabled,
)
from morrow.core.computer_admission import admit_execute
from morrow.core.computer_use import (
    ClickAction,
    ComputerUseDelivery,
    ObservationImageRef,
    ScrollAction,
    parse_computer_action,
)
from morrow.core.runtime_policy import ComputerUseSettings
from test_computer_use_actions import setup


def publish(read):
    return read.observation.model_copy(
        update={
            "image": ObservationImageRef(
                artifact_id="art_1",
                sha256="a" * 64,
                mime="image/png",
                byte_size=8,
                width=20,
                height=10,
                tool_execution_id="tex_1",
            )
        }
    )


@pytest.mark.parametrize("enabled,expected", [(True, "passed"), (False, "failed")])
async def test_same_old_token_attribute_is_read_after_one_click(enabled, expected):
    session, native, _, read, request = await setup()
    session._sdk.__version__ = FUNCTIONAL_SDK_VERSION
    original = native.call_tool

    async def call(name, arguments):
        if name != "read_element_attribute":
            return await original(name, arguments)
        data = json.loads(arguments)
        assert data["element_token"] == "tok-hidden"
        assert data["pid"] == 4242 and data["window_id"] == 9001
        assert [n for n, _ in native.calls].count("click") == 1
        native.calls.append((name, data))
        return SimpleNamespace(
            is_error=False,
            degraded=False,
            structured_json=json.dumps(
                {
                    "identity": "exact",
                    "attribute": "enabled",
                    "status": "readable",
                    "value": enabled,
                }
            ),
        )

    native.call_tool = call
    ref = read.observation.elements[0].element_ref
    action = parse_computer_action(
        {
            "type": "click",
            "element_ref": ref,
            "postcondition": {
                "type": "attribute_equals",
                "element_ref": ref,
                "attribute": "enabled",
                "value": "true",
            },
        }
    )
    outcome = await session.execute_one(
        admit_execute(request(action)),
        authority=lambda: None,
    )
    assert outcome.status == "completed" and outcome.postcondition == expected
    assert [n for n, _ in native.calls].count("click") == 1
    assert [n for n, _ in native.calls].count("read_element_attribute") == 1


@pytest.mark.parametrize(
    "data",
    [
        None,
        "not json",
        "[]",
        '{"identity":"selector"}',
        '{"identity":"exact","attribute":"enabled","status":"readable","value":1}',
        '{"identity":"exact","attribute":"enabled","status":"unavailable"}',
    ],
)
async def test_missing_or_weak_attribute_evidence_is_unavailable(data):
    async def call(name, arguments):
        assert name == "read_element_attribute"
        return SimpleNamespace(is_error=False, degraded=False, structured_json=data)

    result = await read_exact_enabled(
        SimpleNamespace(call_tool=call),
        NativeAttributeInput(
            pid=1,
            window_id=2,
            session="run",
            element_token="private-token",
        ),
    )
    assert result is None


async def test_foreground_wheel_keeps_delivery_and_one_native_entry():
    session, native, _, read, request = await setup(ComputerUseDelivery.FOREGROUND)
    session._sdk.__version__ = FUNCTIONAL_SDK_VERSION
    action = ScrollAction(type="scroll", x=1, y=1, direction="down", amount=3)
    outcome = await session.execute_one(
        admit_execute(
            request(action).model_copy(update={"observation": publish(read)}),
            settings=ComputerUseSettings(enabled=True),
        ),
        authority=lambda: None,
    )
    assert outcome.delivery is ComputerUseDelivery.FOREGROUND
    inputs = [(n, p) for n, p in native.calls if n == "scroll"]
    assert len(inputs) == 1
    assert inputs[0][1]["delivery_mode"] == "foreground"
    assert inputs[0][1]["amount"] == 3


async def test_background_element_double_uses_physical_token_route_once():
    session, native, _, read, request = await setup(ComputerUseDelivery.BACKGROUND)
    session._sdk.__version__ = FUNCTIONAL_SDK_VERSION

    async def call(name, arguments):
        assert name == "double_click"
        native.calls.append((name, json.loads(arguments)))
        return SimpleNamespace(action=None, is_error=False, degraded=False)

    native.call_tool = call
    action = ClickAction(
        type="click", count=2, element_ref=read.observation.elements[0].element_ref
    )
    outcome = await session.execute_one(
        admit_execute(
            request(action).model_copy(update={"observation": publish(read)}),
            settings=ComputerUseSettings(enabled=True),
        ),
        authority=lambda: None,
    )
    assert outcome.status == "unknown"
    calls = [p for n, p in native.calls if n == "double_click"]
    assert len(calls) == 1 and calls[0]["physical_gesture"] is True
    assert calls[0]["element_token"] == "tok-hidden"
    assert calls[0]["delivery_mode"] == "background"
    assert not any(n == "click" for n, _ in native.calls)


async def test_attribute_read_error_preserves_dispatched_completion():
    session, native, _, read, request = await setup()
    session._sdk.__version__ = FUNCTIONAL_SDK_VERSION

    async def call(name, arguments):
        assert name == "read_element_attribute"
        raise RuntimeError("native private details")

    native.call_tool = call
    ref = read.observation.elements[0].element_ref
    action = parse_computer_action(
        {
            "type": "click",
            "element_ref": ref,
            "postcondition": {
                "type": "attribute_equals",
                "element_ref": ref,
                "attribute": "enabled",
                "value": "true",
            },
        }
    )
    outcome = await session.execute_one(admit_execute(request(action)), authority=lambda: None)
    assert outcome.status == "completed" and outcome.postcondition == "not_checked"
    assert "private" not in repr(outcome)
    assert [n for n, _ in native.calls].count("click") == 1
