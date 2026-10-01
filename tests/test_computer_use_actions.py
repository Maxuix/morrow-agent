"""Single admitted actions with fake SDK and deterministic events/clocks."""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from morrow.adapters.computer_use.action_inputs import NativeTextInput, invoke_fixed_action
from morrow.adapters.computer_use.registry import TrustedDesktopRegistry
from morrow.adapters.computer_use.session import TypedComputerSession
from morrow.core.computer_use import (
    TRUSTED_COMPUTER_USE_AUTHORITY,
    ClickAction,
    ComputerUseContractError,
    ComputerUseDelivery,
    DiscoverRequest,
    ExecuteRequest,
    HotkeyAction,
    ObserveWindowRequest,
    OpenRunSessionRequest,
    PressKeyAction,
    ScrollAction,
    TypeTextAction,
)
from morrow.core.runtime_policy import ComputerUseSettings
from morrow.testing import FixedClock, FixedIdSource
from test_computer_use_driver import NOW, _Enum, _Native, _process_birth, _scope, _sdk


class Native(_Native):
    def __init__(self):
        super().__init__()
        self.frame_change = False
        self.after_windows = lambda: None

    async def list_windows(self, payload):
        result = await super().list_windows(payload)
        if self.frame_change:
            result.windows[0].bounds.x += 1
        self.after_windows()
        return result

    async def get_window_state(self, payload):
        result = await super().get_window_state(payload)
        result.elements[0].role = "AXTextField"
        result.elements[0].label = "Text"
        return result

    async def call_tool(self, name, arguments_json):
        assert name in {"type_text", "press_key", "hotkey", "scroll"}
        payload = json.loads(arguments_json)
        self.calls.append((name, payload))
        return SimpleNamespace(
            action=SimpleNamespace(
                effect=_Enum("CONFIRMED"),
                delivery=SimpleNamespace(mode=_Enum(payload["delivery_mode"].upper())),
                error=None,
            ),
            degraded=False,
            is_error=False,
            raw_json="raw secret diagnostic",
            text="raw secret",
        )


async def setup(delivery=ComputerUseDelivery.FOREGROUND, *, safety_probe=lambda subject: True):
    native, clock = Native(), FixedClock(NOW)
    scope = _scope(delivery=delivery)
    session = TypedComputerSession(
        _sdk(),
        native,
        TrustedDesktopRegistry(FixedIdSource()),
        FixedIdSource(),
        clock,
        process_reader=_process_birth,
        element_safety_probe=safety_probe,
    )
    run = await session.open_run_session(
        OpenRunSessionRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
            agent_run_id="arun_1",
            scope=scope,
        )
    )
    targets = await session.discover(
        DiscoverRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
            scope=scope,
            run_session_id=run.run_session_id,
        )
    )
    target = targets.targets[0]
    read = await session.observe(
        ObserveWindowRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
            scope=scope,
            target=target,
            delivery=delivery,
            include_image=False,
        )
    )

    def request(action):
        return ExecuteRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
            scope=scope,
            target=target,
            observation=read.observation,
            action=action,
            delivery=delivery,
        )

    return session, native, clock, read, request


def effects(native):
    return [
        (name, args)
        for name, args in native.calls
        if name in {"click", "type_text", "press_key", "hotkey", "scroll"}
    ]


@pytest.mark.parametrize("delivery", list(ComputerUseDelivery))
@pytest.mark.parametrize("kind", ["type_text", "press_key", "hotkey", "scroll"])
async def test_fixed_protocol_actions_bind_exact_token_window_and_delivery(delivery, kind):
    session, native, _, read, request = await setup(delivery)
    ref = read.observation.elements[0].element_ref
    action = {
        "type_text": lambda: TypeTextAction(type="type_text", element_ref=ref, text="你好🙂 hello"),
        "press_key": lambda: PressKeyAction(type="press_key", element_ref=ref, key="delete"),
        "hotkey": lambda: HotkeyAction(type="hotkey", element_ref=ref, keys=("meta", "shift", "s")),
        "scroll": lambda: ScrollAction(type="scroll", element_ref=ref, direction="down", amount=50),
    }[kind]()
    outcome = await session.execute_one(
        request(action), settings=ComputerUseSettings(enabled=True), authority=lambda: None
    )
    assert outcome.status == "completed" and outcome.delivery is delivery
    name, payload = effects(native)[0]
    assert name == kind
    assert payload["pid"] == 4242 and payload["window_id"] == 9001
    assert payload["element_token"] == "tok-hidden"
    assert payload["delivery_mode"] == delivery.value
    assert payload["session"].startswith("crun_")
    assert "scope" not in payload and "screenshot_out_file" not in payload
    if kind == "press_key":
        assert payload["key"] == "forward_delete"
    if kind == "hotkey":
        assert payload["keys"] == ["shift", "cmd", "s"]
    if kind == "scroll":
        assert "x" not in payload and "y" not in payload and payload["by"] == "line"
    assert "raw secret" not in outcome.model_dump_json()
    with pytest.raises(ComputerUseContractError, match="stale_observation"):
        await session.execute_one(
            request(action), settings=ComputerUseSettings(enabled=True), authority=lambda: None
        )
    assert len(effects(native)) == 1


@pytest.mark.parametrize("kind", ["type_text", "press_key", "hotkey"])
async def test_sensitive_keyboard_targets_never_reach_sdk(kind):
    session, native, _, read, request = await setup()
    ref = read.observation.elements[1].element_ref
    action = {
        "type_text": lambda: TypeTextAction(type=kind, element_ref=ref, text="hello"),
        "press_key": lambda: PressKeyAction(type=kind, element_ref=ref, key="enter"),
        "hotkey": lambda: HotkeyAction(type=kind, element_ref=ref, keys=("meta", "a")),
    }[kind]()
    with pytest.raises(ComputerUseContractError, match="sensitive_target"):
        await session.execute_one(
            request(action), settings=ComputerUseSettings(enabled=True), authority=lambda: None
        )
    assert effects(native) == []


async def test_geometry_change_rejects_action_and_consumes_the_old_observation():
    session, native, _, read, request = await setup()
    action = ClickAction(type="click", element_ref=read.observation.elements[0].element_ref)
    native.frame_change = True
    with pytest.raises(ComputerUseContractError, match="stale_observation"):
        await session.execute_one(
            request(action), settings=ComputerUseSettings(enabled=True), authority=lambda: None
        )
    native.frame_change = False
    with pytest.raises(ComputerUseContractError, match="stale_observation"):
        await session.execute_one(
            request(action), settings=ComputerUseSettings(enabled=True), authority=lambda: None
        )
    assert effects(native) == []


@pytest.mark.parametrize("age", [-1, 30, 31])
async def test_expired_or_future_observation_is_rejected_before_any_sdk_read(age):
    session, native, clock, read, request = await setup()
    clock.value += timedelta(seconds=age)
    before = len(native.calls)
    with pytest.raises(ComputerUseContractError, match="stale_observation"):
        await session.execute_one(
            request(
                ClickAction(type="click", element_ref=read.observation.elements[0].element_ref)
            ),
            settings=ComputerUseSettings(enabled=True),
            authority=lambda: None,
        )
    assert len(native.calls) == before


async def test_revocation_in_retained_task_prevents_sdk_entry():
    session, native, _, read, request = await setup()
    calls = 0

    def authority():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise ComputerUseContractError("grant_inactive")

    with pytest.raises(ComputerUseContractError, match="grant_inactive"):
        await session.execute_one(
            request(
                ClickAction(type="click", element_ref=read.observation.elements[0].element_ref)
            ),
            settings=ComputerUseSettings(enabled=True),
            authority=authority,
        )
    assert calls == 3 and effects(native) == []


async def test_deadline_expiring_during_live_preflight_prevents_dispatch():
    session, native, clock, read, request = await setup()
    native.after_windows = lambda: setattr(clock, "value", NOW + timedelta(seconds=30))
    with pytest.raises(ComputerUseContractError, match="stale_observation"):
        await session.execute_one(
            request(
                ClickAction(type="click", element_ref=read.observation.elements[0].element_ref)
            ),
            settings=ComputerUseSettings(enabled=True),
            authority=lambda: None,
        )
    assert effects(native) == []


async def test_opaque_failure_after_dispatch_is_unknown_and_cannot_repeat():
    session, native, _, read, request = await setup()

    async def broken(payload):
        native.calls.append(("click", payload))
        raise RuntimeError("raw diagnostic")

    native.click = broken
    action = ClickAction(type="click", element_ref=read.observation.elements[0].element_ref)
    outcome = await session.execute_one(
        request(action), settings=ComputerUseSettings(enabled=True), authority=lambda: None
    )
    assert outcome.status == "unknown" and outcome.error_code == "driver_error"
    assert "raw diagnostic" not in repr(outcome)
    with pytest.raises(ComputerUseContractError, match="stale_observation"):
        await session.execute_one(
            request(action), settings=ComputerUseSettings(enabled=True), authority=lambda: None
        )
    assert len(effects(native)) == 1


async def test_native_inputs_refuse_open_ended_arguments_and_do_not_echo_text_or_tokens():
    with pytest.raises(ValidationError):
        NativeTextInput(
            pid=1,
            window_id=2,
            session="crun_1",
            delivery_mode="foreground",
            element_token="token",
            text="hello",
            output_path="/tmp/out",
        )
    payload = NativeTextInput(
        pid=1,
        window_id=2,
        session="crun_1",
        delivery_mode="foreground",
        element_token="private-token",
        text="private-text",
    )
    assert "private" not in repr(payload)
    with pytest.raises(ComputerUseContractError, match="rejected_action"):
        await invoke_fixed_action(None, {"name": "arbitrary_script"})


@pytest.mark.parametrize("button,count", [("left", 1), ("right", 1), ("left", 2), ("right", 2)])
async def test_click_keeps_exact_button_count_and_bound_token(button, count):
    session, native, _, read, request = await setup(ComputerUseDelivery.BACKGROUND)
    action = ClickAction(
        type="click",
        button=button,
        count=count,
        element_ref=read.observation.elements[0].element_ref,
    )
    outcome = await session.execute_one(
        request(action), settings=ComputerUseSettings(enabled=True), authority=lambda: None
    )
    assert outcome.status == "completed"
    _, payload = effects(native)[0]
    assert payload.count == count and payload.button.name == button.upper()
    assert payload.target.pid == 4242 and payload.target.window_id == 9001
    assert payload.position.element_token == "tok-hidden"


async def test_coordinates_are_delivered_image_pixels_without_second_retina_conversion():
    from morrow.core.computer_use import ObservationImageRef

    session, native, _, read, request = await setup(ComputerUseDelivery.BACKGROUND)
    action = ClickAction(type="click", x=10, y=4)
    with pytest.raises(ComputerUseContractError, match="image_not_published"):
        await session.execute_one(
            request(action), settings=ComputerUseSettings(enabled=True), authority=lambda: None
        )
    observation = read.observation.model_copy(
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
    actual = request(action).model_copy(update={"observation": observation})
    outcome = await session.execute_one(
        actual, settings=ComputerUseSettings(enabled=True), authority=lambda: None
    )
    assert outcome.status == "completed"
    _, payload = effects(native)[0]
    assert (payload.position.x, payload.position.y) == (10, 4)


async def test_rediscovery_invalidates_previous_action_snapshot():
    session, native, _, read, request = await setup()
    req = request(ClickAction(type="click", element_ref=read.observation.elements[0].element_ref))
    await session.discover(
        DiscoverRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
            scope=req.scope,
            run_session_id=session._require_session(),
        )
    )
    with pytest.raises(ComputerUseContractError, match="stale_observation"):
        await session.execute_one(
            req, settings=ComputerUseSettings(enabled=True), authority=lambda: None
        )
    assert effects(native) == []


@pytest.mark.parametrize(
    "completion,expected",
    [("NOT_STARTED", "not_started"), ("COMPLETED", "completed"), ("UNKNOWN", "unknown")],
)
async def test_interruption_preserves_native_completion_without_retry(completion, expected):
    session, native, _, read, request = await setup()

    class ActionInterrupted(Exception):
        pass

    async def interrupted(payload):
        native.calls.append(("click", payload))
        error = ActionInterrupted("untrusted raw native error")
        error.completion = _Enum(completion)
        raise error

    native.click = interrupted
    outcome = await session.execute_one(
        request(ClickAction(type="click", element_ref=read.observation.elements[0].element_ref)),
        settings=ComputerUseSettings(enabled=True),
        authority=lambda: None,
    )
    assert outcome.status == expected and outcome.error_code == "action_interrupted"
    assert "untrusted" not in outcome.model_dump_json()
    assert len(effects(native)) == 1


async def test_missing_secure_subrole_proof_suppresses_text_label_and_refuses_input():
    session, native, _, read, request = await setup(safety_probe=None)
    element = read.observation.elements[0]
    assert element.sensitive and element.label is None
    with pytest.raises(ComputerUseContractError, match="sensitive_target"):
        await session.execute_one(
            request(
                TypeTextAction(type="type_text", text="hello", element_ref=element.element_ref)
            ),
            settings=ComputerUseSettings(enabled=True),
            authority=lambda: None,
        )
    assert effects(native) == []


async def test_live_secure_subrole_proof_is_rechecked_and_failure_is_closed():
    safe = True

    def probe(subject):
        assert subject.pid == 4242 and subject.window_id == 9001
        assert "4242" not in repr(subject) and "tok-hidden" not in repr(subject)
        return safe

    session, native, _, read, request = await setup(safety_probe=probe)
    assert not read.observation.elements[0].sensitive
    safe = False
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
    assert effects(native) == []


async def test_native_privacy_probe_error_is_never_echoed_or_treated_as_safe():
    def broken(subject):
        raise RuntimeError("native raw secure value")

    _, _, _, read, _ = await setup(safety_probe=broken)
    assert read.observation.elements[0].sensitive
    assert "native raw" not in read.observation.model_dump_json()


async def test_privacy_change_at_last_authority_check_prevents_input_dispatch():
    safe = True
    session, native, _, read, request = await setup(safety_probe=lambda subject: safe)
    calls = 0

    def authority():
        nonlocal safe, calls
        calls += 1
        if calls == 3:
            safe = False

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
            authority=authority,
        )
    assert effects(native) == []
