"""Guarded SDK query contract and real typed-session admission with a fake SDK."""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from morrow.adapters.computer_use.action_inputs import ElementSafetySubject, NativeTextInput
from morrow.adapters.computer_use.security import NativeElementSafetyProbe, parse_element_security
from morrow.core.computer_use import ComputerUseContractError, TypeTextAction
from morrow.core.runtime_policy import ComputerUseSettings
from test_computer_use_actions import Native, effects, setup


def response(**changes):
    value = {
        "schema_version": 2,
        "input_guard_version": 1,
        "classification": "non_sensitive",
        "binding_verified": True,
        "reason": None,
    }
    value.update(changes)
    return json.dumps(value)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": 1},
        {"schema_version": True},
        {"input_guard_version": True},
        {"input_guard_version": 2},
        {"binding_verified": 1},
        {"binding_verified": False},
        {"classification": "safe"},
        {"reason": "private native error"},
        {"token": "private-token"},
    ],
)
def test_old_or_unproven_guard_contract_never_grants_input(changes):
    with pytest.raises(ComputerUseContractError, match="element_security_invalid"):
        parse_element_security(response(**changes))


def test_duplicate_security_fields_are_rejected():
    content = response()[:-1] + ',"classification":"sensitive"}'
    with pytest.raises(ComputerUseContractError, match="element_security_invalid"):
        parse_element_security(content)


async def test_query_uses_only_exact_subject_binding_and_current_session():
    calls = []

    class SDK:
        async def call_tool(self, name, content):
            calls.append((name, json.loads(content)))
            return SimpleNamespace(is_error=False, structured_json=response())

    current = "current-session"
    probe = NativeElementSafetyProbe(SDK(), lambda: current)
    subject = ElementSafetySubject(10, 20, "current-private-token", "axtextfield", (100, 200))
    assert await probe(subject) is True
    assert calls == [
        (
            "get_element_security",
            {
                "pid": 10,
                "window_id": 20,
                "element_token": "current-private-token",
                "session": current,
            },
        )
    ]
    assert "private-token" not in repr(probe) and "private-token" not in repr(subject)
    assert await probe(ElementSafetySubject(10, 20, None, "axtextfield", (100, 200))) is False
    assert len(calls) == 1


@pytest.mark.parametrize("is_error", [True, None, 1, "false"])
async def test_native_error_or_malformed_error_flag_cannot_grant_input(is_error):
    class SDK:
        async def call_tool(self, name, content):
            return SimpleNamespace(is_error=is_error, structured_json=response())

    probe = NativeElementSafetyProbe(SDK(), lambda: "current-session")
    assert await probe(ElementSafetySubject(10, 20, "private-token", "axtextfield", None)) is False


@pytest.mark.parametrize("value", [False, 1, "true"])
def test_native_guard_flag_cannot_be_disabled_or_coerced(value):
    with pytest.raises(ValidationError):
        NativeTextInput(
            pid=10,
            window_id=20,
            session="current-session",
            delivery_mode="background",
            element_token="private-token",
            text="controlled text",
            require_non_sensitive=value,
        )


async def test_typed_session_queries_guard_contract_and_requests_native_enforcement(monkeypatch):
    original = Native.call_tool
    queries = []

    async def call(native, name, content):
        if name == "get_element_security":
            queries.append(json.loads(content))
            return SimpleNamespace(is_error=False, structured_json=response())
        return await original(native, name, content)

    monkeypatch.setattr(Native, "call_tool", call)
    session, native, _, read, request = await setup(safety_probe=None, native_security=True)
    outcome = await session.execute_one(
        request(
            TypeTextAction(
                type="type_text", text="hello", element_ref=read.observation.elements[0].element_ref
            )
        ),
        settings=ComputerUseSettings(enabled=True),
        authority=lambda: None,
    )
    assert outcome.status == "completed" and len(queries) == 3
    assert all(query["element_token"] == "tok-hidden" for query in queries)
    assert len(effects(native)) == 1 and effects(native)[0][1]["require_non_sensitive"] is True


@pytest.mark.parametrize("lost_contract", [False, True])
async def test_final_native_security_change_refuses_before_mutating_call(
    monkeypatch, lost_contract
):
    original = Native.call_tool
    queries = 0

    async def call(native, name, content):
        nonlocal queries
        if name == "get_element_security":
            queries += 1
            content = response()
            if queries == 3:
                content = (
                    response(schema_version=1)
                    if lost_contract
                    else response(classification="sensitive")
                )
            return SimpleNamespace(is_error=False, structured_json=content)
        return await original(native, name, content)

    monkeypatch.setattr(Native, "call_tool", call)
    session, native, _, read, request = await setup(safety_probe=None, native_security=True)
    with pytest.raises(ComputerUseContractError, match="element_safety_unconfirmed"):
        await session.execute_one(
            request(
                TypeTextAction(
                    type="type_text",
                    text="hello",
                    element_ref=read.observation.elements[0].element_ref,
                )
            ),
            settings=ComputerUseSettings(enabled=True),
            authority=lambda: None,
        )
    assert queries == 3 and effects(native) == []
