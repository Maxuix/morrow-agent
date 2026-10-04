"""Typed desktop adapter. These tests use a fake SDK and never construct a native driver."""

from __future__ import annotations

import ast
import base64
import importlib.abc
import json
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from morrow.adapters.computer_use import diagnose_host, preflight
from morrow.adapters.computer_use.candidates import LocalWindowIdentity
from morrow.adapters.computer_use.diagnostics import HostProbe
from morrow.adapters.computer_use.process_identity import ProcessBirth
from morrow.adapters.computer_use.registry import TrustedDesktopRegistry
from morrow.adapters.computer_use.sdk_loader import collect_host_probe, construct_driver
from morrow.adapters.computer_use.session import TypedComputerSession
from morrow.core.computer_admission import admit_discover, admit_execute, admit_observe
from morrow.core.computer_use import (
    TRUSTED_COMPUTER_USE_AUTHORITY,
    ClickAction,
    ComputerUseAppIdentity,
    ComputerUseContractError,
    ComputerUseDelivery,
    ComputerUseImageShare,
    ComputerUseOperation,
    ComputerUseScope,
    ComputerUseWindowBoundary,
    ComputerUseWindowIdentity,
    DiscoverRequest,
    ExecuteRequest,
    ObserveWindowRequest,
    OpenRunSessionRequest,
    PressKeyAction,
    TypeTextAction,
    decode_computer_use_scope,
    map_image_point,
)
from morrow.core.runtime_policy import ComputerUseMode, ComputerUseSettings
from morrow.testing import FixedClock, FixedIdSource

NOW = datetime(2026, 1, 1, tzinfo=UTC)
SOURCE = "src/morrow/adapters/computer_use"


def _process_birth(pid):
    assert pid == 4242
    return ProcessBirth(1, 0)


def _scope(**overrides) -> ComputerUseScope:
    values = {
        "generation": 1,
        "workspace_id": "ws_1",
        "task_run_id": "task_1",
        "agent_run_id": "arun_1",
        "apps": (ComputerUseAppIdentity(bundle_id="com.example.Notes"),),
        "window_boundary": ComputerUseWindowBoundary.WINDOW,
        "operations": (ComputerUseOperation.OBSERVE, ComputerUseOperation.ACTION),
        "delivery": ComputerUseDelivery.FOREGROUND,
        "image_share": ComputerUseImageShare.CONTROLLED_WINDOW,
    }
    values.update(overrides)
    return decode_computer_use_scope({"schema_version": 1, **values})


SELECTED_WINDOW = "cwin_notes"


def _selected_scope(**overrides) -> ComputerUseScope:
    windows = overrides.pop(
        "windows",
        (
            ComputerUseWindowIdentity(
                app=ComputerUseAppIdentity(bundle_id="com.example.Notes"),
                window_identity=SELECTED_WINDOW,
            ),
        ),
    )
    return _scope(schema_version=2, windows=windows, **overrides)


def _bindings(window_identity=SELECTED_WINDOW, *, birth=None):
    return {
        window_identity: LocalWindowIdentity(
            "com.example.Notes", 4242, birth or ProcessBirth(1, 0), 9001
        )
    }


def _hybrid_settings(**overrides) -> ComputerUseSettings:
    values = {"enabled": True, "mode": ComputerUseMode.HYBRID}
    values.update(overrides)
    return ComputerUseSettings(**values)


def _bound_session(native, *, reader=_process_birth, ids=None, clock=None, birth=None):
    ids = ids or FixedIdSource()
    return TypedComputerSession(
        _sdk(),
        native,
        TrustedDesktopRegistry(ids),
        ids,
        clock or FixedClock(NOW),
        process_reader=reader,
        window_bindings=_bindings(birth=birth),
    )


def _enabled_probe(**overrides) -> HostProbe:
    values = {
        "sdk_present": True,
        "sdk_version": "0.30.4",
        "system": "darwin",
        "os_version": (14, 5, 0),
        "interactive_session": True,
        "accessibility": True,
        "screen_recording": True,
    }
    values.update(overrides)
    return HostProbe(**values)


def test_diagnose_host_stays_unavailable_for_every_blocker():
    enabled = ComputerUseSettings(enabled=True)
    assert diagnose_host(None, _enabled_probe()).reason == "disabled"
    cases = {
        "sdk_missing": {"sdk_present": False},
        "abi_mismatch": {"native_load_failed": True},
        "native_version_mismatch": {"sdk_version": "0.30.3"},
        "unsupported_os": {"system": "linux", "os_version": None},
        "no_interactive_session": {"interactive_session": False},
        "tcc_missing": {"accessibility": False},
        "driver_not_activated": {},
        "native_unverified": {"driver_activated": True},
    }
    for reason, overrides in cases.items():
        result = diagnose_host(enabled, _enabled_probe(**overrides))
        assert result.status == "unavailable"
        assert result.reason == reason
    images = diagnose_host(enabled, _enabled_probe(screen_recording=False), images_required=True)
    assert images.reason == "tcc_missing"
    assert preflight(ComputerUseSettings()).reason == "disabled"


def test_collect_host_probe_does_not_construct_or_echo_loader_errors():
    def refuse():
        raise AssertionError("loader")

    missing = collect_host_probe(
        spec_present=False,
        loader=refuse,
        system="darwin",
        os_version=(15, 0, 0),
        interactive=True,
    )
    assert diagnose_host(ComputerUseSettings(enabled=True), missing).reason == "sdk_missing"

    def broken():
        raise OSError("secret-path")

    failed = collect_host_probe(
        spec_present=True,
        loader=broken,
        system="darwin",
        os_version=(14, 0, 0),
        interactive=True,
    )
    assert failed.native_load_failed is True
    assert "secret-path" not in repr(failed)

    def unexpected_permission():
        raise AssertionError("tcc")

    old = collect_host_probe(
        spec_present=True,
        loader=lambda: SimpleNamespace(
            __version__="0.30.3",
            current_mac_os_permission_status=unexpected_permission,
        ),
        system="darwin",
        os_version=(14, 0, 0),
        interactive=True,
    )
    assert old.sdk_version == "0.30.3"
    assert old.accessibility is None


@pytest.mark.parametrize("version", ("0.30.4",))
def test_known_sdk_diagnostics_read_permissions_but_never_enable_native(version):
    calls = []

    def permissions():
        calls.append("read")
        return SimpleNamespace(accessibility=True, screen_recording=True)

    probe = collect_host_probe(
        spec_present=True,
        loader=lambda: SimpleNamespace(
            __version__=version, current_mac_os_permission_status=permissions
        ),
        system="darwin",
        os_version=(14, 0, 0),
        interactive=True,
        driver_activated=True,
    )
    assert calls == ["read"]
    assert probe.sdk_version == version
    result = diagnose_host(ComputerUseSettings(enabled=True), probe)
    assert result.status == "unavailable"
    assert result.reason == "native_unverified"


def test_construct_driver_uses_the_same_process_runtime():
    import morrow.adapters.computer_use as adapter

    created: list[object] = []

    class Options:
        def __init__(self, *, claude_code_compatibility: bool, authorization: object) -> None:
            self.claude_code_compatibility = claude_code_compatibility
            self.authorization = authorization

    class Driver:
        @classmethod
        def create_configured(cls, options: object) -> object:
            created.append(options)
            return {"runtime": True}

        @classmethod
        def create_private_worker(cls, options: object) -> object:
            raise AssertionError(options)

    before = adapter.DRIVER_CONSTRUCTION_COUNT
    runtime = construct_driver(
        SimpleNamespace(
            ConfiguredDriverOptions=Options,
            RuntimeAuthorizationOptions=lambda **values: SimpleNamespace(**values),
            SessionPermissionMode=SimpleNamespace(STANDARD="standard"),
            CuaDriver=Driver,
        )
    )
    assert runtime == {"runtime": True}
    assert adapter.DRIVER_CONSTRUCTION_COUNT == before + 1
    assert created[0].claude_code_compatibility is False
    assert created[0].authorization.allowed_modes == ["standard"]
    assert created[0].authorization.compatibility_mode == "standard"
    assert created[0].authorization.unrestricted_acknowledged is False
    assert created[0].authorization.max_session_ttl_seconds == 600
    assert created[0].authorization.max_idle_ttl_seconds == 600


def test_sdk_protocol_bridge_has_only_fixed_action_and_attribute_calls():
    root = __import__("pathlib").Path(SOURCE)
    fixed_names = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr != "call_tool":
                    continue
                assert path.name == "action_inputs.py"
                assert isinstance(node.args[0], ast.Constant)
                fixed_names.append((path.name, node.args[0].value))
    assert sorted(fixed_names) == [
        ("action_inputs.py", "double_click"),
        ("action_inputs.py", "hotkey"),
        ("action_inputs.py", "press_key"),
        ("action_inputs.py", "read_element_attribute"),
        ("action_inputs.py", "scroll"),
        ("action_inputs.py", "type_text"),
    ]


def test_importing_the_adapter_does_not_load_the_native_module():
    class RejectCua(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path, target=None):
            if fullname == "cua_driver" or fullname.startswith("cua_driver."):
                raise RuntimeError("native driver import")
            return None

    import morrow.adapters.computer_use as adapter

    finder = RejectCua()
    constructions = adapter.DRIVER_CONSTRUCTION_COUNT
    sys.meta_path.insert(0, finder)
    try:
        import morrow.adapters.computer_use.session as session

        assert adapter.preflight().reason == "disabled"
        assert session.TypedComputerSession is not None
        assert adapter.DRIVER_CONSTRUCTION_COUNT == constructions
    finally:
        sys.meta_path.remove(finder)


class _Input:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


class _Enum:
    def __init__(self, name: str) -> None:
        self.name = name


class _Target:
    @staticmethod
    def WINDOW(pid: int, window_id: int) -> SimpleNamespace:
        return SimpleNamespace(pid=pid, window_id=window_id)


class _Position:
    @staticmethod
    def ELEMENT(token: str) -> SimpleNamespace:
        return SimpleNamespace(element_token=token)

    @staticmethod
    def COORDINATES(x: float, y: float) -> SimpleNamespace:
        return SimpleNamespace(x=x, y=y)


def _sdk() -> SimpleNamespace:
    return SimpleNamespace(
        ListAppsInput=_Input,
        ListWindowsInput=_Input,
        GetWindowStateInput=_Input,
        StartSessionInput=_Input,
        EndSessionInput=_Input,
        ClickInput=_Input,
        TypeTextInput=_Input,
        ScrollInput=_Input,
        PressKeyInput=_Input,
        HotkeyInput=_Input,
        ActionTarget=SimpleNamespace(WINDOW=_Target.WINDOW),
        ClickPosition=SimpleNamespace(ELEMENT=_Position.ELEMENT, COORDINATES=_Position.COORDINATES),
        InputDeliveryMode=SimpleNamespace(
            FOREGROUND=_Enum("FOREGROUND"), BACKGROUND=_Enum("BACKGROUND")
        ),
        ClickButton=SimpleNamespace(LEFT=_Enum("LEFT"), RIGHT=_Enum("RIGHT")),
        ScrollDirection=SimpleNamespace(UP=_Enum("UP"), DOWN=_Enum("DOWN")),
        CaptureScope=SimpleNamespace(WINDOW=_Enum("WINDOW")),
    )


class _Native:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.traps = 0

    def call_tool(self, *_args: object) -> None:
        self.traps += 1
        raise AssertionError("generic tool")

    async def start_session(self, payload: object) -> object:
        self.calls.append(("start_session", payload))
        return SimpleNamespace(active=True, revived=True, state=None)

    async def end_session(self, payload: object) -> object:
        self.calls.append(("end_session", payload))
        return SimpleNamespace()

    def close(self) -> None:
        self.calls.append(("close", None))

    async def list_apps(self, payload: object) -> object:
        self.calls.append(("list_apps", payload))
        return SimpleNamespace(
            apps=[
                SimpleNamespace(
                    pid=4242,
                    bundle_id="com.example.Notes",
                    running=True,
                    launch_path="/var/secret/app",
                    name="Notes",
                ),
                SimpleNamespace(pid=7, bundle_id="com.example.Other", running=True, name="Other"),
            ]
        )

    async def list_windows(self, payload: object) -> object:
        self.calls.append(("list_windows", payload))
        return SimpleNamespace(
            windows=[
                SimpleNamespace(
                    window_id=9001,
                    pid=4242,
                    title="Notes password",
                    is_on_screen=True,
                    minimized=False,
                    bounds=SimpleNamespace(x=0, y=0, width=40, height=20),
                )
            ]
        )

    async def get_window_state(self, payload: object) -> object:
        self.calls.append(("get_window_state", payload))
        encoded = base64.b64encode(b"png-bytes").decode("ascii")
        return SimpleNamespace(
            pid=4242,
            window_id=9001,
            snapshot_id="snap-secret",
            elements_complete=True,
            degraded=True,
            truncated=True,
            total_element_count=5,
            returned_element_count=2,
            screenshot_width=20,
            screenshot_height=10,
            screenshot_scale=2.0,
            screenshot_frame_valid=True,
            screenshot_file_path="/tmp/secret-shot.png",
            images=[SimpleNamespace(mime_type="image/png", data_base64=encoded)],
            elements=[
                SimpleNamespace(
                    role="AXButton",
                    depth=1,
                    label="Save",
                    element_token="tok-hidden",
                    frame=SimpleNamespace(x=0, y=0, w=10, h=4),
                ),
                SimpleNamespace(
                    role="AXSecureTextField",
                    depth=2,
                    label="secret",
                    element_token="tok-secure",
                    frame=None,
                ),
            ],
            window_bounds=SimpleNamespace(x=0, y=0, width=40, height=20),
        )

    async def click(self, payload: object) -> object:
        self.calls.append(("click", payload))
        return SimpleNamespace(
            effect=_Enum("CONFIRMED"),
            delivery=SimpleNamespace(mode=_Enum("BACKGROUND")),
            error=None,
            verified=True,
        )

    async def type_text(self, payload: object) -> object:
        self.calls.append(("type_text", payload))
        return SimpleNamespace(
            action=SimpleNamespace(
                effect=_Enum("CONFIRMED"),
                delivery=SimpleNamespace(mode=_Enum("FOREGROUND")),
                error=None,
            ),
            text="typed password",
            raw_json='{"password":"x"}',
            is_error=False,
            degraded=False,
        )

    async def scroll(self, payload: object) -> object:
        self.calls.append(("scroll", payload))
        return SimpleNamespace(action=None, is_error=False, degraded=False, text="", raw_json="")


async def test_typed_session_hides_native_identity_and_keeps_the_real_frame():
    native = _Native()
    registry = TrustedDesktopRegistry(FixedIdSource())
    session = TypedComputerSession(
        _sdk(),
        native,
        registry,
        FixedIdSource(),
        FixedClock(NOW),
        process_reader=_process_birth,
        window_bindings=_bindings(),
    )
    opened = await session.open_run_session(
        OpenRunSessionRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
            agent_run_id="arun_1",
            scope=_selected_scope(),
        )
    )
    assert opened.run_session_id.startswith("crun_")
    assert "windows=0" in repr(registry)
    discovered = await session.discover(
        admit_discover(
            DiscoverRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=_selected_scope(),
                run_session_id=opened.run_session_id,
            )
        )
    )
    again = await session.discover(
        admit_discover(
            DiscoverRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=_selected_scope(),
                run_session_id=opened.run_session_id,
            )
        )
    )
    assert len(discovered.targets) == 1
    target = discovered.targets[0]
    assert target.window_identity == again.targets[0].window_identity
    assert target.display_label == "Notes password"
    dumped = target.model_dump_json()
    assert "4242" not in dumped
    assert "9001" not in dumped
    assert "password" in dumped
    assert "secret" not in dumped
    assert "4242" not in repr(registry)
    assert "tok-hidden" not in repr(registry)

    admitted = admit_observe(
        ObserveWindowRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
            scope=_selected_scope(),
            target=target,
            delivery=ComputerUseDelivery.FOREGROUND,
            include_image=True,
        ),
        settings=ComputerUseSettings(
            enabled=True,
            mode=ComputerUseMode.HYBRID,
            image_long_edge_px=1280,
            max_call_seconds=5,
        ),
    )
    observed = await session.observe(admitted)
    calls = len(native.calls)
    with pytest.raises(TypeError, match="settings"):
        await session.observe(admitted, settings=ComputerUseSettings())
    assert len(native.calls) == calls
    state_input = next(payload for name, payload in native.calls if name == "get_window_state")
    assert state_input.screenshot_out_file is None
    assert state_input.max_elements == 400
    assert state_input.max_depth == 8
    assert state_input.timeout_ms == 5000
    assert state_input.max_image_dimension == 1280
    assert state_input.pid == 4242
    assert observed.observation.complete is False
    assert observed.observation.truncated is True
    assert observed.observation.omitted_count >= 3
    assert observed.observation.frame.scale_x == 1.0
    assert map_image_point(observed.observation.frame, 10, 4) == (10.0, 4.0)
    assert observed.observation.frame.scale_y == 1.0
    assert observed.observation.frame.crop_width == 20
    secure = next(item for item in observed.observation.elements if "secure" in item.role)
    assert "sensitive" not in secure.model_dump()
    assert secure.label == "secret"
    assert observed.capture is not None
    assert observed.capture.content == b"png-bytes"
    rendered = f"{observed.observation.model_dump_json()} {observed!r} {observed.capture!r}"
    assert "png-bytes" not in rendered
    assert base64.b64encode(b"png-bytes").decode("ascii") not in rendered
    assert "secret-shot" not in rendered
    assert "tok-hidden" not in rendered
    assert "snap-secret" not in rendered
    assert native.traps == 0


async def test_click_results_preserve_delivery_and_reject_unsafe_text_target():
    native = _Native()
    ids = FixedIdSource()
    registry = TrustedDesktopRegistry(ids)
    session = TypedComputerSession(
        _sdk(),
        native,
        registry,
        ids,
        FixedClock(NOW),
        process_reader=_process_birth,
        window_bindings=_bindings(),
    )
    opened = await session.open_run_session(
        OpenRunSessionRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
            agent_run_id="arun_1",
            scope=_selected_scope(),
        )
    )
    target = (
        await session.discover(
            admit_discover(
                DiscoverRequest(
                    authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                    scope=_selected_scope(),
                    run_session_id=opened.run_session_id,
                )
            )
        )
    ).targets[0]
    observed = await session.observe(
        admit_observe(
            ObserveWindowRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=_selected_scope(),
                target=target,
                delivery=ComputerUseDelivery.FOREGROUND,
                include_image=False,
            ),
            settings=ComputerUseSettings(),
        )
    )
    button = next(item for item in observed.observation.elements if item.role == "axbutton")

    def admitted(action, observation):
        return admit_execute(
            ExecuteRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=_selected_scope(),
                target=target,
                observation=observation,
                action=action,
                delivery=ComputerUseDelivery.FOREGROUND,
            )
        )

    click = await session.execute_one(
        admitted(ClickAction(type="click", element_ref=button.element_ref), observed.observation),
        authority=lambda: None,
    )
    payload = next(item for name, item in native.calls if name == "click")
    assert payload.position.element_token == "tok-hidden"
    assert payload.delivery_mode.name == "FOREGROUND"
    assert click.status == "unknown"
    assert click.error_code == "unexpected_delivery"
    assert click.delivery is ComputerUseDelivery.BACKGROUND
    assert "verified" not in click.model_dump_json()

    async def fresh():
        return await session.observe(
            admit_observe(
                ObserveWindowRequest(
                    authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                    scope=_selected_scope(),
                    target=target,
                    delivery=ComputerUseDelivery.FOREGROUND,
                    include_image=False,
                ),
                settings=ComputerUseSettings(),
            )
        )

    observed = await fresh()
    button = observed.observation.elements[0]
    before_refusal = len(native.calls)
    with pytest.raises(ComputerUseContractError, match="not_editable"):
        admitted(
            TypeTextAction(type="type_text", text="hello", element_ref=button.element_ref),
            observed.observation,
        )
    assert len(native.calls) == before_refusal

    class ActionInterrupted(Exception):
        def __init__(self):
            self.completion = _Enum("UNKNOWN")
            self.reason = "password field focused"

    async def interrupted(payload):
        native.calls.append(("click", payload))
        raise ActionInterrupted()

    native.click = interrupted
    stopped = await session.execute_one(
        admitted(ClickAction(type="click", element_ref=button.element_ref), observed.observation),
        authority=lambda: None,
    )
    assert stopped.status == "unknown" and stopped.error_code == "action_interrupted"
    assert "password" not in stopped.model_dump_json()

    async def refused(payload):
        native.calls.append(("click", payload))
        return SimpleNamespace(
            effect=_Enum("REFUSED"), delivery=None, error=SimpleNamespace(code="Not Allowed")
        )

    native.click = refused
    observed = await fresh()
    denial = await session.execute_one(
        admitted(
            ClickAction(type="click", element_ref=observed.observation.elements[0].element_ref),
            observed.observation,
        ),
        authority=lambda: None,
    )
    assert denial.status == "not_started" and denial.error_code == "refused"
    assert native.traps == 0


def test_interactive_probe_uses_console_owner_instead_of_spoofable_environment(monkeypatch):
    from morrow.adapters.computer_use import sdk_loader

    monkeypatch.setattr(sdk_loader.sys, "platform", "darwin")
    monkeypatch.setattr(sdk_loader.os, "getuid", lambda: 501)
    monkeypatch.delenv("SECURITYSESSIONID", raising=False)
    monkeypatch.setattr(sdk_loader.os, "stat", lambda path: SimpleNamespace(st_uid=501))
    assert sdk_loader.current_interactive_session()
    monkeypatch.setenv("SECURITYSESSIONID", "spoofed")
    monkeypatch.setattr(sdk_loader.os, "stat", lambda path: SimpleNamespace(st_uid=502))
    assert not sdk_loader.current_interactive_session()


async def test_unresolved_native_window_does_not_invent_image_mapping():
    class UnresolvedWindow(_Native):
        async def get_window_state(self, payload):
            return SimpleNamespace(
                elements=[],
                images=[],
                degraded=True,
                elements_complete=False,
                window_bounds=None,
                screenshot_width=None,
                screenshot_height=None,
            )

    ids = FixedIdSource()
    session = TypedComputerSession(
        _sdk(),
        UnresolvedWindow(),
        TrustedDesktopRegistry(ids),
        ids,
        FixedClock(NOW),
        process_reader=_process_birth,
        window_bindings=_bindings(),
    )
    run = await session.open_run_session(
        OpenRunSessionRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY, agent_run_id="arun_1", scope=_selected_scope()
        )
    )
    found = await session.discover(
        admit_discover(
            DiscoverRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=_selected_scope(),
                run_session_id=run.run_session_id,
            )
        )
    )
    read = await session.observe(
        admit_observe(
            ObserveWindowRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=_selected_scope(),
                target=found.targets[0],
                delivery=ComputerUseDelivery.FOREGROUND,
                include_image=True,
            ),
            settings=ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID),
        )
    )
    assert read.capture is None
    assert read.image_error == "image_missing"
    assert read.observation.degraded
    assert read.observation.frame.width == 40
    assert read.observation.frame.scale_x is None
    with pytest.raises(ComputerUseContractError, match="unknown_scale"):
        map_image_point(read.observation.frame, 0, 0)


@pytest.mark.parametrize("dimensions", [(2560, 1440), (1440, 2560), (7680, 4320)])
async def test_large_semantic_window_can_observe_and_click_an_element(dimensions):
    from morrow.services.computer_use import ComputerUseRunService

    width, height = dimensions

    class SemanticWindow(_Native):
        async def list_windows(self, payload):
            result = await super().list_windows(payload)
            result.windows[0].bounds = SimpleNamespace(x=0, y=0, width=width, height=height)
            return result

        async def get_window_state(self, payload):
            assert payload.include_screenshot is False
            result = await super().get_window_state(payload)
            result.images = []
            result.screenshot_width = result.screenshot_height = result.screenshot_scale = None
            result.screenshot_frame_valid = False
            result.degraded = result.truncated = False
            result.total_element_count = result.returned_element_count = len(result.elements)
            return result

        async def click(self, payload):
            result = await super().click(payload)
            result.delivery.mode = SimpleNamespace(name="FOREGROUND")
            return result

    native = SemanticWindow()
    clock = FixedClock(NOW)
    session = _bound_session(native, clock=clock)
    scope = _selected_scope()
    opened = await session.open_run_session(
        OpenRunSessionRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
            agent_run_id=scope.agent_run_id,
            scope=scope,
        )
    )
    service = ComputerUseRunService(
        session, opened, scope, ComputerUseSettings(enabled=True), clock
    )

    def authority():
        pass

    try:
        found = await service.discover(authority=authority)
        read = await service.observe(found.targets[0].target_ref, authority=authority)
        observation = read.observation
        assert observation.complete and observation.elements[0].label == "Save"
        assert read.capture is None and observation.image is None
        assert (observation.frame.width, observation.frame.height) == dimensions
        with pytest.raises(ComputerUseContractError, match="unknown_scale"):
            map_image_point(observation.frame, 1, 1)
        coordinate = await service.execute_one(
            observation.observation_id, ClickAction(type="click", x=1, y=1), authority=authority
        )
        assert coordinate.status == "not_started" and coordinate.error_code == "unknown_scale"
        assert not any(name == "click" for name, _ in native.calls)

        outcome = await service.execute_one(
            observation.observation_id,
            ClickAction(type="click", element_ref=observation.elements[0].element_ref),
            authority=authority,
        )
        assert outcome.status == "completed" and outcome.error_code is None
        assert outcome.delivery is scope.delivery
        assert [name for name, _ in native.calls].count("click") == 1
    finally:
        service.stop()
        await session.settle()


@pytest.mark.parametrize("degraded", [False, True])
async def test_unknown_tree_completeness_does_not_invent_truncation(degraded):
    class IncompleteWindow(_Native):
        async def get_window_state(self, payload):
            result = await super().get_window_state(payload)
            result.elements_complete = False
            result.degraded = degraded
            result.truncated = False
            result.total_element_count = len(result.elements)
            result.returned_element_count = len(result.elements)
            return result

    ids = FixedIdSource()
    session = TypedComputerSession(
        _sdk(),
        IncompleteWindow(),
        TrustedDesktopRegistry(ids),
        ids,
        FixedClock(NOW),
        process_reader=_process_birth,
        window_bindings=_bindings(),
    )
    run = await session.open_run_session(
        OpenRunSessionRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY, agent_run_id="arun_1", scope=_selected_scope()
        )
    )
    found = await session.discover(
        admit_discover(
            DiscoverRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=_selected_scope(),
                run_session_id=run.run_session_id,
            )
        )
    )
    observed = await session.observe(
        admit_observe(
            ObserveWindowRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=_selected_scope(),
                target=found.targets[0],
                delivery=ComputerUseDelivery.FOREGROUND,
                include_image=True,
            ),
            settings=ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID),
        )
    )
    assert observed.observation.degraded is degraded
    assert observed.observation.degraded_reason == ("unknown" if degraded else None)
    assert observed.observation.complete is False
    assert observed.observation.truncated is False
    assert observed.observation.omitted_count == 0
    assert len(observed.observation.elements) == 2


@pytest.mark.parametrize("kind", ["multiple", "oversized"])
async def test_capture_limits_are_checked_before_base64_decode(monkeypatch, kind):
    from morrow.adapters.computer_use.session import _capture
    from morrow.core.computer_use import MAX_IMAGE_BYTES

    state = await _Native().get_window_state(None)
    if kind == "multiple":
        state.images.append(state.images[0])
    else:
        state.images[0].data_base64 = "A" * ((((MAX_IMAGE_BYTES + 2) // 3) * 4) + 4)
    monkeypatch.setattr(base64, "b64decode", lambda *args, **kwargs: pytest.fail("decoder called"))
    capture, reason = _capture(state)
    assert capture is None
    assert reason == "image_bounds"


@pytest.mark.parametrize("element_count", [201, 400])
async def test_larger_native_walk_never_expands_model_projection_or_shares_omissions(element_count):
    class LargeWindow(_Native):
        async def get_window_state(self, payload):
            result = await super().get_window_state(payload)
            result.elements = [
                SimpleNamespace(role="AXButton", depth=1, element_token=f"token-{index}")
                for index in range(element_count)
            ]
            result.elements_complete = True
            result.truncated = result.degraded = False
            result.total_element_count = result.returned_element_count = element_count
            return result

    ids = FixedIdSource()
    session = TypedComputerSession(
        _sdk(),
        LargeWindow(),
        TrustedDesktopRegistry(ids),
        ids,
        FixedClock(NOW),
        process_reader=_process_birth,
        window_bindings=_bindings(),
    )
    run = await session.open_run_session(
        OpenRunSessionRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY, agent_run_id="arun_1", scope=_selected_scope()
        )
    )
    found = await session.discover(
        admit_discover(
            DiscoverRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=_selected_scope(),
                run_session_id=run.run_session_id,
            )
        )
    )
    observed = await session.observe(
        admit_observe(
            ObserveWindowRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=_selected_scope(),
                target=found.targets[0],
                delivery=ComputerUseDelivery.FOREGROUND,
                include_image=True,
            ),
            settings=ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID),
        )
    )
    assert len(observed.observation.elements) == 200
    assert observed.observation.omitted_count == element_count - 200
    assert observed.observation.truncated is True
    assert observed.observation.complete is False
    assert observed.image_error is None


async def test_unverified_screenshot_frame_never_enables_coordinate_mapping():
    from morrow.adapters.computer_use.registry import WindowGeometry
    from morrow.adapters.computer_use.session import _capture, _frame

    state = await _Native().get_window_state(None)
    state.screenshot_frame_valid = False
    capture, reason = _capture(state)
    assert capture is None
    assert reason == "unknown_scale"
    frame = _frame(state, geometry=WindowGeometry(0, 0, 40, 20))
    with pytest.raises(ComputerUseContractError, match="unknown_scale"):
        map_image_point(frame, 10, 4)


@pytest.mark.parametrize("node_count", [1, 200])
@pytest.mark.parametrize("field", ["value", "value_description"])
@pytest.mark.parametrize("action_kind", ["type_text", "press_key"])
async def test_long_text_display_preserves_actionable_refs_and_shared_byte_budget(
    node_count, field, action_kind
):
    from morrow.core.computer_use import MAX_AX_TEXT_BYTES, TextAppearsPostcondition
    from morrow.services.computer_use import ComputerUseRunService
    from morrow.services.computer_verification import evaluate_postcondition

    class LongTextWindow(_Native):
        async def get_window_state(self, payload):
            state = await super().get_window_state(payload)
            state.degraded = state.truncated = False
            state.elements_complete = True
            state.total_element_count = state.returned_element_count = node_count
            state.elements = [
                SimpleNamespace(
                    role="AXTextField",
                    depth=1,
                    label=f"Field {index}",
                    element_token=f"long-token-{index}",
                    enabled=True,
                    **{field: "界" * 4096 + "tail-only"},
                )
                for index in range(node_count)
            ]
            return state

        async def call_tool(self, name, content):
            assert name == action_kind
            arguments = json.loads(content)
            assert arguments["element_token"] == f"long-token-{node_count - 1}"
            self.calls.append((name, arguments))
            return SimpleNamespace(
                action=SimpleNamespace(
                    effect=_Enum("CONFIRMED"),
                    delivery=SimpleNamespace(mode=_Enum("FOREGROUND")),
                    error=None,
                )
            )

    native = LongTextWindow()
    clock = FixedClock(NOW)
    scope = _selected_scope()
    session = _bound_session(native, clock=clock)
    opened = await session.open_run_session(
        OpenRunSessionRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY, agent_run_id="arun_1", scope=scope
        )
    )
    service = ComputerUseRunService(
        session, opened, scope, ComputerUseSettings(enabled=True), clock
    )
    try:
        discovered = await service.discover(authority=lambda: None)
        observed = await service.observe(discovered.targets[0].target_ref, authority=lambda: None)
        tree = observed.observation
        assert len(tree.elements) == node_count and tree.omitted_count == 0
        assert tree.truncated is True and tree.complete is False
        element = tree.elements[-1]
        assert element.label == f"Field {node_count - 1}" and element.enabled is True
        assert element.text_truncated is True
        assert getattr(element, field) == ("界" * 4096 if node_count == 1 else "")
        text = "".join(
            f"{item.role}{item.label or ''}{item.value or ''}{item.value_tail or ''}"
            f"{item.value_description or ''}"
            for item in tree.elements
        )
        assert len(text.encode()) <= MAX_AX_TEXT_BYTES
        assert evaluate_postcondition(
            tree, TextAppearsPostcondition(type="text_appears", text="tail-only")
        ) == ("passed" if field == "value" else "pending")
        action = (
            TypeTextAction(type="type_text", element_ref=element.element_ref, text="append")
            if action_kind == "type_text"
            else PressKeyAction(type="press_key", element_ref=element.element_ref, key="q")
        )
        outcome = await service.execute_one(tree.observation_id, action, authority=lambda: None)
        assert outcome.status == "completed"
        assert sum(name == action_kind for name, _ in native.calls) == 1
    finally:
        service.stop()
        await session.settle()
