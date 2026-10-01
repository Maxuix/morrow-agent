"""Process birth identity is verified with injected kernel replies and fake SDK calls."""

from __future__ import annotations

import ctypes

import pytest

from morrow.adapters.computer_use import process_identity
from morrow.adapters.computer_use.process_identity import ProcessBirth
from morrow.adapters.computer_use.registry import TrustedDesktopRegistry
from morrow.adapters.computer_use.session import TypedComputerSession
from morrow.core.computer_use import (
    TRUSTED_COMPUTER_USE_AUTHORITY,
    ClickAction,
    ComputerUseContractError,
    ComputerUseDelivery,
    DiscoverRequest,
    ObserveWindowRequest,
    OpenRunSessionRequest,
    PreparedComputerAction,
)
from morrow.testing import FixedClock, FixedIdSource
from test_computer_use_driver import NOW, _Native, _scope, _sdk


@pytest.mark.parametrize("reply", ["short", "wrong_pid", "zero_start", "invalid_usec", "exception"])
def test_kernel_identity_failure_is_closed_and_sanitized(monkeypatch, reply):
    monkeypatch.setattr(process_identity.sys, "platform", "darwin")

    def query(pid, info):
        if reply == "exception":
            raise OSError("secret native details")
        info.pid = pid + 1 if reply == "wrong_pid" else pid
        info.start_seconds = 0 if reply == "zero_start" else 123
        info.start_microseconds = 1_000_000 if reply == "invalid_usec" else 456
        return ctypes.sizeof(info) - 1 if reply == "short" else ctypes.sizeof(info)

    monkeypatch.setattr(process_identity, "_query_process", query)
    with pytest.raises(ComputerUseContractError, match="target_identity_unavailable") as error:
        process_identity.read_process_birth(42)
    assert "secret" not in str(error.value)


def test_kernel_identity_layout_and_success(monkeypatch):
    monkeypatch.setattr(process_identity.sys, "platform", "darwin")
    assert ctypes.sizeof(process_identity._ProcBsdInfo) == 136
    assert process_identity._ProcBsdInfo.start_seconds.offset == 120

    def query(pid, info):
        info.pid, info.start_seconds, info.start_microseconds = pid, 123, 456
        return ctypes.sizeof(info)

    monkeypatch.setattr(process_identity, "_query_process", query)
    stamp = process_identity.read_process_birth(42)
    assert stamp == ProcessBirth(123, 456)
    assert repr(stamp) == "ProcessBirth()"


@pytest.mark.parametrize("pid", [True, 0, -1, 2**31, "42"])
def test_invalid_pid_never_calls_kernel(monkeypatch, pid):
    monkeypatch.setattr(process_identity.sys, "platform", "darwin")
    monkeypatch.setattr(
        process_identity, "_query_process", lambda *args: pytest.fail("unexpected kernel query")
    )
    with pytest.raises(ComputerUseContractError, match="target_identity_unavailable"):
        process_identity.read_process_birth(pid)


async def _opened(native, reader):
    ids = FixedIdSource()
    session = TypedComputerSession(
        _sdk(), native, TrustedDesktopRegistry(ids), ids, FixedClock(NOW), process_reader=reader
    )
    run = await session.open_run_session(
        OpenRunSessionRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY, agent_run_id="arun_1", scope=_scope()
        )
    )
    request = DiscoverRequest(
        authority=TRUSTED_COMPUTER_USE_AUTHORITY, scope=_scope(), run_session_id=run.run_session_id
    )
    found = await session.discover(request)
    return session, request, found.targets[0]


def _observe(target):
    return ObserveWindowRequest(
        authority=TRUSTED_COMPUTER_USE_AUTHORITY,
        scope=_scope(),
        target=target,
        delivery=ComputerUseDelivery.FOREGROUND,
        include_image=True,
    )


async def test_pid_reuse_mints_new_refs_and_old_refs_do_not_reach_sdk():
    birth = ProcessBirth(123, 0)
    native = _Native()
    session, request, target = await _opened(native, lambda pid: birth)
    observed = await session.observe(_observe(target))
    birth = ProcessBirth(124, 0)
    before = len(native.calls)
    with pytest.raises(ComputerUseContractError, match="stale_observation"):
        await session.observe(_observe(target))
    with pytest.raises(ComputerUseContractError, match="stale_observation"):
        await session.execute(
            PreparedComputerAction(
                action=ClickAction(
                    type="click", element_ref=observed.observation.elements[0].element_ref
                ),
                window_point=None,
            ),
            window_identity=target.window_identity,
            delivery=ComputerUseDelivery.FOREGROUND,
            agent_run_id="arun_1",
            generation=1,
        )
    assert len(native.calls) == before
    replacement = (await session.discover(request)).targets[0]
    assert replacement.process_identity != target.process_identity
    assert replacement.target_ref != target.target_ref
    assert replacement.window_identity != target.window_identity


async def test_process_replaced_during_observe_never_returns_capture():
    birth = ProcessBirth(123, 0)

    class ReplacedWindow(_Native):
        async def get_window_state(self, payload):
            nonlocal birth
            result = await super().get_window_state(payload)
            birth = ProcessBirth(124, 0)
            return result

    native = ReplacedWindow()
    session, _, target = await _opened(native, lambda pid: birth)
    with pytest.raises(ComputerUseContractError, match="stale_observation"):
        await session.observe(_observe(target))
    assert [name for name, _ in native.calls].count("get_window_state") == 1
    assert not any(name == "click" for name, _ in native.calls)


async def test_process_replaced_during_discovery_returns_no_targets():
    births = iter([ProcessBirth(123, 0), ProcessBirth(124, 0)])
    native = _Native()
    with pytest.raises(ComputerUseContractError, match="stale_observation"):
        await _opened(native, lambda pid: next(births))
    assert not any(name == "get_window_state" for name, _ in native.calls)


@pytest.mark.parametrize("pid,window_id", [(7, 9001), (True, 9001), (4242, 0), (4242, 2**32)])
async def test_discovery_rejects_wrong_owner_and_invalid_window_identity(pid, window_id):
    class InvalidWindow(_Native):
        async def list_windows(self, payload):
            result = await super().list_windows(payload)
            result.windows[0].pid = pid
            result.windows[0].window_id = window_id
            return result

    native = InvalidWindow()
    ids = FixedIdSource()
    session = TypedComputerSession(
        _sdk(),
        native,
        TrustedDesktopRegistry(ids),
        ids,
        FixedClock(NOW),
        process_reader=lambda pid: ProcessBirth(123, 0),
    )
    run = await session.open_run_session(
        OpenRunSessionRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY, agent_run_id="arun_1", scope=_scope()
        )
    )
    found = await session.discover(
        DiscoverRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
            scope=_scope(),
            run_session_id=run.run_session_id,
        )
    )
    assert found.targets == ()
    assert not any(name == "get_window_state" for name, _ in native.calls)


@pytest.mark.parametrize("change", ["app", "closed", "owner", "geometry"])
async def test_live_target_changes_refuse_before_window_state(change):
    class ChangedWindow(_Native):
        changed = False

        async def list_apps(self, payload):
            result = await super().list_apps(payload)
            if self.changed and change == "app":
                result.apps[0].bundle_id = "com.other.App"
            return result

        async def list_windows(self, payload):
            result = await super().list_windows(payload)
            if self.changed:
                if change == "closed":
                    result.windows = []
                elif change == "owner":
                    result.windows[0].pid = 7
                elif change == "geometry":
                    result.windows[0].bounds.width = float("nan")
            return result

    native = ChangedWindow()
    session, _, target = await _opened(native, lambda pid: ProcessBirth(123, 0))
    native.changed = True
    with pytest.raises(ComputerUseContractError):
        await session.observe(_observe(target))
    assert not any(name == "get_window_state" for name, _ in native.calls)


async def test_geometry_changed_while_observing_rejects_the_capture():
    class MovingWindow(_Native):
        moved = False

        async def get_window_state(self, payload):
            result = await super().get_window_state(payload)
            self.moved = True
            return result

        async def list_windows(self, payload):
            result = await super().list_windows(payload)
            if self.moved:
                result.windows[0].bounds.x += 1
            return result

    native = MovingWindow()
    session, _, target = await _opened(native, lambda pid: ProcessBirth(123, 0))
    with pytest.raises(ComputerUseContractError, match="stale_observation"):
        await session.observe(_observe(target))
    assert [name for name, _ in native.calls].count("get_window_state") == 1
    assert not any(name == "click" for name, _ in native.calls)


async def test_sdk_capture_bounds_must_match_live_geometry():
    class WrongFrame(_Native):
        async def get_window_state(self, payload):
            result = await super().get_window_state(payload)
            result.window_bounds.width += 1
            return result

    native = WrongFrame()
    session, _, target = await _opened(native, lambda pid: ProcessBirth(123, 0))
    with pytest.raises(ComputerUseContractError, match="stale_observation"):
        await session.observe(_observe(target))


async def test_changed_frozen_scope_does_not_reach_sdk():
    native = _Native()
    session, request, target = await _opened(native, lambda pid: ProcessBirth(123, 0))
    before = len(native.calls)
    changed = request.scope.model_copy(update={"workspace_id": "ws_other"})
    with pytest.raises(ComputerUseContractError, match="subject_mismatch"):
        await session.discover(request.model_copy(update={"scope": changed}))
    with pytest.raises(ComputerUseContractError, match="subject_mismatch"):
        await session.observe(_observe(target).model_copy(update={"scope": changed}))
    assert len(native.calls) == before


async def test_new_read_retires_previous_window_element_tokens_even_on_failure():
    native = _Native()
    session, _, target = await _opened(native, lambda pid: ProcessBirth(123, 0))
    first = await session.observe(_observe(target))
    old_ref = first.observation.elements[0].element_ref
    assert session._registry.element(old_ref) is not None
    second = await session.observe(_observe(target))
    with pytest.raises(ComputerUseContractError, match="unknown_element"):
        session._registry.element(old_ref)
    new_ref = second.observation.elements[0].element_ref
    assert new_ref != old_ref

    async def broken(payload):
        raise RuntimeError("raw native diagnostic must not escape")

    native.get_window_state = broken
    with pytest.raises(ComputerUseContractError, match="driver_error"):
        await session.observe(_observe(target))
    with pytest.raises(ComputerUseContractError, match="unknown_element"):
        session._registry.element(new_ref)
