"""Local discovery through the shared SDK owner; no native device or model request."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from morrow.adapters.computer_use.owner import ComputerDriverOwner
from morrow.adapters.computer_use.process_identity import ProcessBirth
from morrow.application.computer_use import ComputerUseLifecycle
from morrow.core.computer_use import (
    TRUSTED_COMPUTER_USE_AUTHORITY,
    ComputerUseContractError,
    ComputerUsePreflight,
)
from morrow.core.runtime_policy import ComputerUseSettings
from morrow.testing import FixedClock, FixedIdSource
from test_computer_use_driver import NOW, _Native, _sdk
from test_computer_use_lifecycle import _close, _Lease, _open

AUTH = TRUSTED_COMPUTER_USE_AUTHORITY
SETTINGS = ComputerUseSettings(enabled=True)


def owner_for(driver, *, process_reader=lambda pid: ProcessBirth(1, 0)):
    lease, clock, sessions = _Lease(), FixedClock(NOW), []

    def session_factory(_driver, name, settings):
        sessions.append(name)
        return _Native()

    owner = ComputerDriverOwner(
        _sdk(),
        FixedIdSource(),
        clock,
        driver_factory=lambda _: driver,
        session_factory=session_factory,
        lease=lease,
        process_reader=process_reader,
    )
    return owner, lease, clock, sessions


class Driver(_Native):
    async def list_windows(self, payload):
        result = await super().list_windows(payload)
        result.windows[0].title = "Controlled Notes"
        return result

    async def shutdown(self):
        self.calls.append(("shutdown", None))


async def test_installed_apps_do_not_consume_running_window_candidate_budget():
    class InstalledApps(Driver):
        async def list_apps(self, payload):
            result = await super().list_apps(payload)
            result.apps.extend(
                SimpleNamespace(pid=0, running=False, bundle_id=f"com.installed.app{index}")
                for index in range(150)
            )
            result.apps.append(SimpleNamespace(pid=9000, bundle_id="com.unknown.running"))
            return result

    driver = InstalledApps()
    owner, lease, _, _ = owner_for(driver)
    try:
        found = await owner.discover_local_candidates(SETTINGS, authority=AUTH)
        assert len(found.candidates) == 1
        assert found.candidates[0].app.bundle_id == "com.example.Notes"
        assert len([name for name, _ in driver.calls if name == "list_windows"]) == 2
        assert not lease.held
    finally:
        await owner.shutdown()


async def test_running_app_budget_still_refuses_without_window_queries():
    class TooManyRunning(Driver):
        async def list_apps(self, payload):
            result = await super().list_apps(payload)
            result.apps = [result.apps[0]] * 101
            return result

    driver = TooManyRunning()
    owner, lease, _, _ = owner_for(driver)
    try:
        with pytest.raises(ComputerUseContractError, match="target_budget"):
            await owner.discover_local_candidates(SETTINGS, authority=AUTH)
        assert not any(name == "list_windows" for name, _ in driver.calls)
        assert not lease.held
    finally:
        await owner.shutdown()


async def test_local_discovery_hides_native_identity_and_shares_owner_with_runs():
    driver = Driver()
    owner, lease, clock, sessions = owner_for(driver)
    try:
        result = await owner.discover_local_candidates(SETTINGS, authority=AUTH)
        assert len(result.candidates) == 1
        candidate = result.candidates[0]
        assert candidate.app.bundle_id == "com.example.Notes"
        assert candidate.display_label == "Controlled Notes"
        assert result.expires_at == NOW + timedelta(seconds=30)
        identity = owner._candidates.resolve(candidate.candidate_id)
        assert identity.pid == 4242 and identity.window_id == 9001
        assert repr(identity) == "LocalWindowIdentity()"
        serialized = result.model_dump_json()
        assert "4242" not in serialized and "9001" not in serialized
        assert "/var/secret" not in serialized
        assert sessions == [] and not lease.held
        assert set(name for name, _ in driver.calls) == {"list_apps", "list_windows"}
        fresh = await owner.discover_local_candidates(SETTINGS, authority=AUTH)
        with pytest.raises(ComputerUseContractError, match="unknown_target"):
            owner._candidates.resolve(candidate.candidate_id)
        clock.value = fresh.expires_at
        with pytest.raises(ComputerUseContractError, match="stale_observation"):
            owner._candidates.resolve(fresh.candidates[0].candidate_id)
        run = await owner.open_run_session(_open(owner))
        with pytest.raises(ComputerUseContractError, match="desktop_busy"):
            await owner.discover_local_candidates(SETTINGS, authority=AUTH)
        assert len(sessions) == 1 and lease.held
        await owner.close_run_session(_close(run))
    finally:
        await owner.shutdown()
    assert not lease.held
    assert [name for name, _ in driver.calls].count("shutdown") == 1


@pytest.mark.parametrize(
    "fault", ["pid_changed", "window_owner", "geometry", "secret_label", "budget"]
)
async def test_local_candidates_validate_identity_limits_and_labels(fault):
    class FaultDriver(Driver):
        async def list_windows(self, payload):
            result = await super().list_windows(payload)
            window = result.windows[0]
            if fault == "window_owner":
                window.pid = 999
            elif fault == "geometry":
                window.bounds.width = float("nan")
            elif fault == "secret_label":
                window.title = "sk-" + "X" * 80
            elif fault == "budget":
                result.windows = [window] * 101
            return result

    reads = 0

    def process_reader(pid):
        nonlocal reads
        reads += 1
        return ProcessBirth(2 if fault == "pid_changed" and reads > 1 else 1, 0)

    owner, lease, _, _ = owner_for(FaultDriver(), process_reader=process_reader)
    try:
        if fault in {"pid_changed", "budget"}:
            with pytest.raises(ComputerUseContractError, match="stale_observation|target_budget"):
                await owner.discover_local_candidates(SETTINGS, authority=AUTH)
            assert repr(owner._candidates) == "LocalCandidateRegistry(count=0)"
        else:
            result = await owner.discover_local_candidates(SETTINGS, authority=AUTH)
            if fault == "secret_label":
                assert result.candidates[0].display_label == "sk-" + "X" * 80
                assert "sk-" in result.model_dump_json()
            else:
                assert result.candidates == ()
        assert not lease.held
    finally:
        await owner.shutdown()


async def test_cancelled_candidate_read_retains_lease_until_the_driver_settles():
    entered, release = asyncio.Event(), asyncio.Event()

    class BlockingDriver(Driver):
        async def list_apps(self, payload):
            entered.set()
            await release.wait()
            return await super().list_apps(payload)

    driver = BlockingDriver()
    owner, lease, _, sessions = owner_for(driver)
    discovery = asyncio.create_task(owner.discover_local_candidates(SETTINGS, authority=AUTH))
    await entered.wait()
    discovery.cancel()
    with pytest.raises(asyncio.CancelledError):
        await discovery
    assert owner.quarantined and lease.held and sessions == []
    with pytest.raises(ComputerUseContractError, match="driver_not_activated"):
        await owner.open_run_session(_open(owner))
    closing = asyncio.create_task(owner.shutdown())
    assert not closing.done()
    assert not any(name == "shutdown" for name, _ in driver.calls)
    release.set()
    await closing
    assert not lease.held
    assert repr(owner._candidates) == "LocalCandidateRegistry(count=0)"
    assert [name for name, _ in driver.calls].count("shutdown") == 1


@pytest.mark.parametrize("blocked_call", ["list_apps", "list_windows"])
@pytest.mark.parametrize("stop", ["cancel", "shutdown"])
async def test_stopped_candidate_enumeration_finishes_only_current_call(
    monkeypatch, blocked_call, stop
):
    entered, release, settled, stopping = (asyncio.Event() for _ in range(4))
    window_reads = []

    class BlockingDriver(Driver):
        async def list_apps(self, payload):
            result = await super().list_apps(payload)
            result.apps = [
                SimpleNamespace(pid=4242, running=True, bundle_id="com.example.Notes"),
                SimpleNamespace(pid=4343, running=True, bundle_id="com.example.Calendar"),
            ]
            if blocked_call == "list_apps":
                entered.set()
                await release.wait()
                settled.set()
            return result

        async def list_windows(self, payload):
            window_reads.append(payload.pid)
            if blocked_call == "list_windows" and len(window_reads) == 1:
                entered.set()
                await release.wait()
                settled.set()
            result = await super().list_windows(payload)
            result.windows[0].pid = payload.pid
            return result

        async def shutdown(self):
            assert settled.is_set()
            await super().shutdown()

    driver = BlockingDriver()
    owner, lease, _, sessions = owner_for(driver)
    stop_admission = owner.stop_admission

    def stopped():
        stop_admission()
        stopping.set()

    monkeypatch.setattr(owner, "stop_admission", stopped)
    discovery = asyncio.create_task(owner.discover_local_candidates(SETTINGS, authority=AUTH))
    closing = None
    try:
        await entered.wait()
        if stop == "cancel":
            discovery.cancel()
            with pytest.raises(asyncio.CancelledError):
                await discovery
            assert owner.quarantined
        closing = asyncio.create_task(owner.shutdown())
        await stopping.wait()
        assert lease.held and not settled.is_set() and not closing.done()
        assert not any(name == "shutdown" for name, _ in driver.calls)
        with pytest.raises(ComputerUseContractError, match="driver_not_activated"):
            await owner.discover_local_candidates(SETTINGS, authority=AUTH)
        release.set()
        await closing
        if stop == "shutdown":
            with pytest.raises(ComputerUseContractError, match="driver_not_activated"):
                await discovery
        assert window_reads == ([] if blocked_call == "list_apps" else [4242])
        assert not lease.held and sessions == []
        assert repr(owner._candidates) == "LocalCandidateRegistry(count=0)"
        assert [name for name, _ in driver.calls].count("shutdown") == 1
    finally:
        release.set()
        await asyncio.gather(discovery, *([closing] if closing else []), return_exceptions=True)
        await owner.shutdown()


async def test_local_read_keeps_native_gate_and_trusted_authority_before_owner_creation():
    created = []
    lifecycle = ComputerUseLifecycle(
        lambda: created.append(True),
        lambda: ComputerUsePreflight(status="unavailable", reason="native_unverified"),
    )
    with pytest.raises(ComputerUseContractError, match="disabled"):
        await lifecycle.discover_local_candidates(ComputerUseSettings(), authority=AUTH)
    with pytest.raises(ComputerUseContractError, match="untrusted_authority"):
        await lifecycle.discover_local_candidates(SETTINGS, authority="model")
    with pytest.raises(ComputerUseContractError, match="native_unverified"):
        await lifecycle.discover_local_candidates(SETTINGS, authority=AUTH)
    assert created == []


async def test_local_read_and_agent_run_use_the_same_lifecycle_owner():
    driver = Driver()
    owner, lease, _, sessions = owner_for(driver)
    created = []

    def factory():
        created.append(True)
        return owner

    lifecycle = ComputerUseLifecycle(
        factory,
        lambda: ComputerUsePreflight(status="unavailable", reason="native_unverified"),
        native_verified=True,  # Fake SDK only; production remains unavailable.
    )
    try:
        result = await lifecycle.discover_local_candidates(SETTINGS, authority=AUTH)
        assert result.candidates and created == [True] and sessions == []
        run = await lifecycle.open_run_session(_open(owner))
        assert created == [True] and len(sessions) == 1 and lease.held
        await lifecycle.close_run_session(_close(run))
    finally:
        await lifecycle.shutdown()


async def test_local_call_timeout_settles_before_shutdown_and_releasing_lease(monkeypatch):
    from morrow.adapters.computer_use import owner as owner_module
    from morrow.adapters.computer_use.calls import NativeCalls

    entered, release = asyncio.Event(), asyncio.Event()

    class BlockingDriver(Driver):
        async def list_apps(self, payload):
            entered.set()
            await release.wait()
            return await super().list_apps(payload)

    driver = BlockingDriver()
    owner, lease, _, _ = owner_for(driver)

    class ControlledTimeout(NativeCalls):
        def __init__(self, quarantine, *, timeout):
            assert timeout == 7
            super().__init__(quarantine, timeout=timeout)
            self._waiter = self.timeout

        async def timeout(self, waiting, seconds):
            await entered.wait()
            raise TimeoutError

    monkeypatch.setattr(owner_module, "NativeCalls", ControlledTimeout)
    with pytest.raises(ComputerUseContractError, match="driver_timeout"):
        await owner.discover_local_candidates(
            ComputerUseSettings(enabled=True, max_call_seconds=7), authority=AUTH
        )
    assert owner.quarantined and lease.held and owner._candidate_calls.pending
    closing = asyncio.create_task(owner.shutdown())
    assert not closing.done()
    release.set()
    await closing
    assert not lease.held and not owner._candidate_calls.pending
    assert repr(owner._candidates) == "LocalCandidateRegistry(count=0)"
    assert [name for name, _ in driver.calls].count("shutdown") == 1
