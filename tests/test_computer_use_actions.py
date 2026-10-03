"""Single admitted actions with fake SDK and deterministic events/clocks."""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from morrow.adapters.computer_use.action_inputs import NativeTextInput, invoke_fixed_action
from morrow.core.computer_admission import admit_discover, admit_execute, admit_observe
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
from morrow.testing import FixedClock
from test_computer_use_driver import NOW, _bound_session, _Enum, _Native, _selected_scope


class Native(_Native):
    def __init__(self):
        super().__init__()
        self.frame_change = False
        self.after_windows = lambda: None

    async def click(self, payload):
        result = await super().click(payload)
        result.delivery.mode = _Enum(payload.delivery_mode.name)
        return result

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


async def setup(delivery=ComputerUseDelivery.FOREGROUND):
    native, clock = Native(), FixedClock(NOW)
    scope = _selected_scope(delivery=delivery)
    session = _bound_session(native, clock=clock)
    run = await session.open_run_session(
        OpenRunSessionRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
            agent_run_id="arun_1",
            scope=scope,
        )
    )
    targets = await session.discover(
        admit_discover(
            DiscoverRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=scope,
                run_session_id=run.run_session_id,
            )
        )
    )
    target = targets.targets[0]
    read = await session.observe(
        admit_observe(
            ObserveWindowRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=scope,
                target=target,
                delivery=delivery,
                include_image=False,
            ),
            settings=ComputerUseSettings(),
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
    if kind == "scroll":
        observed = read.observation.model_copy(
            update={
                "elements": tuple(
                    e.model_copy(update={"role": "axscrollarea"}) if e.element_ref == ref else e
                    for e in read.observation.elements
                )
            }
        )
        old_request = request

        def request(a):
            return old_request(a).model_copy(update={"observation": observed})

    action = {
        "type_text": lambda: TypeTextAction(type="type_text", element_ref=ref, text="你好🙂 hello"),
        "press_key": lambda: PressKeyAction(type="press_key", element_ref=ref, key="delete"),
        "hotkey": lambda: HotkeyAction(type="hotkey", element_ref=ref, keys=("meta", "shift", "s")),
        "scroll": lambda: ScrollAction(type="scroll", element_ref=ref, direction="down", amount=50),
    }[kind]()
    outcome = await session.execute_one(
        admit_execute(request(action), settings=ComputerUseSettings(enabled=True)),
        authority=lambda: None,
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
    with pytest.raises(ComputerUseContractError, match="unknown_element"):
        await session.execute_one(
            admit_execute(request(action), settings=ComputerUseSettings(enabled=True)),
            authority=lambda: None,
        )
    assert len(effects(native)) == 1


@pytest.mark.parametrize("kind", ["type_text", "press_key", "hotkey"])
async def test_secure_keyboard_targets_use_the_same_sdk_path(kind):
    session, native, _, read, request = await setup()
    ref = read.observation.elements[1].element_ref
    action = {
        "type_text": lambda: TypeTextAction(type=kind, element_ref=ref, text="my password value"),
        "press_key": lambda: PressKeyAction(type=kind, element_ref=ref, key="enter"),
        "hotkey": lambda: HotkeyAction(type=kind, element_ref=ref, keys=("meta", "a")),
    }[kind]()
    assert read.observation.elements[1].role == "axsecuretextfield"
    assert read.observation.elements[1].label == "secret"
    outcome = await session.execute_one(
        admit_execute(request(action), settings=ComputerUseSettings(enabled=True)),
        authority=lambda: None,
    )
    assert outcome.status == "completed"
    assert len(effects(native)) == 1
    assert effects(native)[0][1]["element_token"] == "tok-secure"
    assert "require_non_sensitive" not in effects(native)[0][1]


async def test_geometry_change_rejects_action_and_consumes_the_old_observation():
    session, native, _, read, request = await setup()
    action = ClickAction(type="click", element_ref=read.observation.elements[0].element_ref)
    native.frame_change = True
    with pytest.raises(ComputerUseContractError, match="stale_observation"):
        await session.execute_one(
            admit_execute(request(action), settings=ComputerUseSettings(enabled=True)),
            authority=lambda: None,
        )
    native.frame_change = False
    with pytest.raises(ComputerUseContractError, match="unknown_element"):
        await session.execute_one(
            admit_execute(request(action), settings=ComputerUseSettings(enabled=True)),
            authority=lambda: None,
        )
    assert effects(native) == []


@pytest.mark.parametrize("age", [-1, 30, 31])
async def test_expired_or_future_observation_is_rejected_before_any_sdk_read(age):
    session, native, clock, read, request = await setup()
    clock.value += timedelta(seconds=age)
    before = len(native.calls)
    with pytest.raises(ComputerUseContractError, match="stale_observation"):
        await session.execute_one(
            admit_execute(
                request(
                    ClickAction(type="click", element_ref=read.observation.elements[0].element_ref)
                ),
                settings=ComputerUseSettings(enabled=True),
            ),
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
            admit_execute(
                request(
                    ClickAction(type="click", element_ref=read.observation.elements[0].element_ref)
                ),
                settings=ComputerUseSettings(enabled=True),
            ),
            authority=authority,
        )
    assert calls == 3 and effects(native) == []


async def test_deadline_expiring_during_live_preflight_prevents_dispatch():
    session, native, clock, read, request = await setup()
    native.after_windows = lambda: setattr(clock, "value", NOW + timedelta(seconds=30))
    with pytest.raises(ComputerUseContractError, match="stale_observation"):
        await session.execute_one(
            admit_execute(
                request(
                    ClickAction(type="click", element_ref=read.observation.elements[0].element_ref)
                ),
                settings=ComputerUseSettings(enabled=True),
            ),
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
        admit_execute(request(action), settings=ComputerUseSettings(enabled=True)),
        authority=lambda: None,
    )
    assert outcome.status == "unknown" and outcome.error_code == "driver_error"
    assert "raw diagnostic" not in repr(outcome)
    with pytest.raises(ComputerUseContractError, match="unknown_element"):
        await session.execute_one(
            admit_execute(request(action), settings=ComputerUseSettings(enabled=True)),
            authority=lambda: None,
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
@pytest.mark.parametrize("delivery", list(ComputerUseDelivery))
async def test_click_keeps_exact_button_count_and_bound_token(button, count, delivery):
    session, native, _, read, request = await setup(delivery)
    action = ClickAction(
        type="click",
        button=button,
        count=count,
        element_ref=read.observation.elements[0].element_ref,
    )
    from morrow.core.computer_use import ObservationImageRef

    if count == 2 and delivery is ComputerUseDelivery.BACKGROUND:
        with pytest.raises(ComputerUseContractError, match="unsupported_double_click_delivery"):
            admit_execute(request(action), settings=ComputerUseSettings(enabled=True))
        assert effects(native) == []
        return

    observation = read.observation
    if count == 2 or button == "right":
        with pytest.raises(ComputerUseContractError, match="image_not_published"):
            admit_execute(request(action), settings=ComputerUseSettings(enabled=True))
        observation = observation.model_copy(
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
    outcome = await session.execute_one(
        admit_execute(
            request(action).model_copy(update={"observation": observation}),
            settings=ComputerUseSettings(enabled=True),
        ),
        authority=lambda: None,
    )
    assert outcome.status == "completed"
    _, payload = effects(native)[0]
    assert payload.count == count and payload.button.name == button.upper()
    assert payload.target.pid == 4242 and payload.target.window_id == 9001
    if count == 1 and button == "left":
        assert payload.position.element_token == "tok-hidden"
    else:
        assert not hasattr(payload.position, "element_token")
        assert (payload.position.x, payload.position.y) == (2, 1)


async def test_coordinates_are_delivered_image_pixels_without_second_retina_conversion():
    from morrow.core.computer_use import ObservationImageRef

    session, native, _, read, request = await setup(ComputerUseDelivery.BACKGROUND)
    action = ClickAction(type="click", x=10, y=4)
    with pytest.raises(ComputerUseContractError, match="image_not_published"):
        await session.execute_one(
            admit_execute(request(action), settings=ComputerUseSettings(enabled=True)),
            authority=lambda: None,
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
        admit_execute(actual, settings=ComputerUseSettings(enabled=True)), authority=lambda: None
    )
    assert outcome.status == "completed"
    _, payload = effects(native)[0]
    assert (payload.position.x, payload.position.y) == (10, 4)


async def test_rediscovery_invalidates_previous_action_snapshot():
    session, native, _, read, request = await setup()
    req = request(ClickAction(type="click", element_ref=read.observation.elements[0].element_ref))
    await session.discover(
        admit_discover(
            DiscoverRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=req.scope,
                run_session_id=session._require_session(),
            )
        )
    )
    with pytest.raises(ComputerUseContractError, match="unknown_element"):
        await session.execute_one(
            admit_execute(req, settings=ComputerUseSettings(enabled=True)), authority=lambda: None
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
        admit_execute(
            request(
                ClickAction(type="click", element_ref=read.observation.elements[0].element_ref)
            ),
            settings=ComputerUseSettings(enabled=True),
        ),
        authority=lambda: None,
    )
    assert outcome.status == expected and outcome.error_code == "action_interrupted"
    assert "untrusted" not in outcome.model_dump_json()
    assert len(effects(native)) == 1


async def test_scroll_rejects_a_text_field_without_guessing_its_center():
    _, _, _, read, request = await setup()
    action = ScrollAction(
        type="scroll",
        element_ref=read.observation.elements[0].element_ref,
        direction="down",
        amount=3,
    )
    with pytest.raises(ComputerUseContractError, match="unsupported_scroll_target"):
        admit_execute(request(action), settings=ComputerUseSettings(enabled=True))


@pytest.mark.parametrize(
    "center,error", [(None, "element_geometry_unavailable"), ((100, 100), "out_of_bounds")]
)
async def test_physical_gestures_without_exact_geometry_never_enter_sdk(center, error):
    from morrow.core.computer_use import ObservationImageRef

    session, native, _, read, request = await setup()
    ref = read.observation.elements[0].element_ref
    session._registry.element(ref).center = center
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
    with pytest.raises(ComputerUseContractError, match=error):
        await session.execute_one(
            admit_execute(
                request(ClickAction(type="click", element_ref=ref, count=2)).model_copy(
                    update={"observation": observation}
                ),
                settings=ComputerUseSettings(enabled=True),
            ),
            authority=lambda: None,
        )
    assert effects(native) == []


@pytest.mark.parametrize("target", ["element", "coordinate"])
async def test_background_double_click_is_refused_before_native_delivery(target):
    from morrow.core.computer_use import ObservationImageRef

    _, native, _, read, request = await setup(ComputerUseDelivery.BACKGROUND)
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
    action = ClickAction(
        type="click",
        count=2,
        **(
            {"element_ref": read.observation.elements[0].element_ref}
            if target == "element"
            else {"x": 1, "y": 1}
        ),
    )
    with pytest.raises(ComputerUseContractError, match="unsupported_double_click_delivery"):
        admit_execute(
            request(action).model_copy(update={"observation": observation}),
            settings=ComputerUseSettings(enabled=True),
        )
    assert effects(native) == []
