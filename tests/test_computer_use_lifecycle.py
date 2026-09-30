"""Lifecycle boundaries with a scripted SDK; no native access or timing sleeps."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from morrow.adapters.computer_use.owner import ComputerDriverOwner
from morrow.core.computer_use import (
    TRUSTED_COMPUTER_USE_AUTHORITY,
    CloseRunSessionRequest,
    ComputerUseContractError,
    DiscoverRequest,
    OpenRunSessionRequest,
)
from morrow.testing import FixedClock, FixedIdSource
from test_computer_use_driver import NOW, _Native, _scope, _sdk


def _open(generation=1):
    return OpenRunSessionRequest(
        authority=TRUSTED_COMPUTER_USE_AUTHORITY,
        agent_run_id="arun_1",
        scope=_scope(generation=generation),
    )


def _close(run):
    return CloseRunSessionRequest(
        authority=TRUSTED_COMPUTER_USE_AUTHORITY, run_session_id=run.run_session_id
    )


def _owner():
    calls = []
    sessions = []

    def record(name):
        calls.append((name, asyncio.get_running_loop(), threading.get_ident()))

    class Native(_Native):
        async def start_session(self, payload):
            record("start")
            return await super().start_session(payload)

        async def end_session(self, payload):
            record("end")

        def close(self):
            record("close")

    async def shutdown():
        record("shutdown")

    def driver_factory(sdk):
        record("create")
        return SimpleNamespace(shutdown=shutdown)

    def session_factory(driver, name):
        record("session")
        native = Native()
        sessions.append(native)
        return native

    owner = ComputerDriverOwner(
        _sdk(),
        FixedIdSource(),
        FixedClock(NOW),
        session_factory=session_factory,
        driver_factory=driver_factory,
    )
    return owner, calls, sessions


async def test_resources_have_one_owner_and_closed_sessions_cannot_be_reused():
    owner, calls, _ = _owner()
    first = await owner.open_run_session(_open())
    session = owner.session_for(first)
    discovery = DiscoverRequest(
        authority=TRUSTED_COMPUTER_USE_AUTHORITY,
        scope=_scope(),
        run_session_id=first.run_session_id,
    )
    target = (await session.discover(discovery)).targets[0]
    await owner.close_run_session(_close(first))
    with pytest.raises(ComputerUseContractError, match="unknown_target"):
        session._registry.window(target.window_identity)
    with pytest.raises(ComputerUseContractError, match="driver_not_activated"):
        await session.discover(discovery)
    with pytest.raises(ComputerUseContractError, match="session_not_reusable"):
        await session.open_run_session(_open(2))
    with pytest.raises(ComputerUseContractError, match="stale_observation"):
        await owner.open_run_session(_open())
    second = await owner.open_run_session(_open(2))
    assert second.run_session_id != first.run_session_id
    await owner.close_run_session(_close(second))
    await owner.shutdown()
    await owner.shutdown()
    assert len({(id(loop), thread) for _, loop, thread in calls}) == 1
    assert [name for name, _, _ in calls].count("shutdown") == 1


async def test_foreign_thread_and_loop_rejected_before_sdk_calls():
    owner, calls, natives = _owner()
    run = await owner.open_run_session(_open())
    session = owner.session_for(run)
    before = len(calls), len(natives[0].calls)

    async def foreign():
        with pytest.raises(ComputerUseContractError, match="owner_mismatch"):
            await owner.shutdown()
        with pytest.raises(ComputerUseContractError, match="owner_mismatch"):
            await session.discover(
                DiscoverRequest(
                    authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                    scope=_scope(),
                    run_session_id=run.run_session_id,
                )
            )

    await asyncio.to_thread(lambda: asyncio.run(foreign()))
    assert before == (len(calls), len(natives[0].calls))
    await owner.close_run_session(_close(run))
    await owner.shutdown()


async def test_subject_and_busy_rejections_do_not_create_sessions():
    owner, calls, _ = _owner()
    with pytest.raises(ComputerUseContractError, match="subject_mismatch"):
        await owner.open_run_session(_open().model_copy(update={"agent_run_id": "arun_other"}))
    run = await owner.open_run_session(_open())
    before = len(calls)
    with pytest.raises(ComputerUseContractError, match="desktop_busy"):
        await owner.open_run_session(_open(2))
    assert len(calls) == before
    await owner.close_run_session(_close(run))
    await owner.shutdown()


async def test_failed_start_quarantines_owner_and_redacts_native_exception():
    owner, calls, _ = _owner()

    class Broken(_Native):
        async def start_session(self, payload):
            raise RuntimeError("secret native traceback")

        def close(self):
            pass

    owner._session_factory = lambda driver, name: Broken()
    with pytest.raises(ComputerUseContractError, match="^driver_error$"):
        await owner.open_run_session(_open())
    with pytest.raises(ComputerUseContractError, match="driver_not_activated"):
        await owner.open_run_session(_open(2))
    await owner.shutdown()
    assert [name for name, _, _ in calls].count("create") == 1


async def test_shutdown_closes_active_session_before_native_shutdown():
    owner, calls, _ = _owner()
    run = await owner.open_run_session(_open())
    session = owner.session_for(run)
    await owner.shutdown()
    assert [name for name, _, _ in calls][-3:] == ["end", "close", "shutdown"]
    with pytest.raises(ComputerUseContractError, match="driver_not_activated"):
        session._require_session()


async def test_shutdown_failure_can_be_settled_without_constructing_another_driver():
    owner, calls, _ = _owner()
    settled = owner._driver.shutdown

    async def broken():
        raise RuntimeError("private native details")

    owner._driver.shutdown = broken
    with pytest.raises(ComputerUseContractError, match="^driver_error$"):
        await owner.shutdown()
    with pytest.raises(ComputerUseContractError, match="driver_not_activated"):
        await owner.open_run_session(_open())
    owner._driver.shutdown = settled
    await owner.shutdown()
    assert [name for name, _, _ in calls].count("create") == 1


async def test_start_transition_rejects_concurrent_open_and_shutdown_without_sleep():
    owner, calls, _ = _owner()
    entered, release = asyncio.Event(), asyncio.Event()

    class Waiting(_Native):
        async def start_session(self, payload):
            entered.set()
            await release.wait()
            return await super().start_session(payload)

        async def end_session(self, payload):
            pass

        def close(self):
            pass

    owner._session_factory = lambda driver, name: Waiting()
    opening = asyncio.create_task(owner.open_run_session(_open()))
    await entered.wait()
    with pytest.raises(ComputerUseContractError, match="desktop_busy"):
        await owner.open_run_session(_open(2))
    with pytest.raises(ComputerUseContractError, match="desktop_busy"):
        await owner.shutdown()
    release.set()
    await opening
    await owner.shutdown()


async def test_core_host_bus_owns_driver_lifecycle_and_foreign_direct_use_is_rejected():
    from morrow.server.host import ApprovalWaiters, CoreHost, RunSupervisor

    host = CoreHost(
        lambda: SimpleNamespace(
            supervisor=RunSupervisor(),
            approval_waiters=ApprovalWaiters(),
            close=lambda: None,
        )
    )
    host.start()
    try:
        owner, calls, _ = await host.execute_command(_owner)
        run = await host.execute_command(lambda: owner.open_run_session(_open()))
        before = len(calls)
        with pytest.raises(ComputerUseContractError, match="owner_mismatch"):
            owner.session_for(run)
        assert len(calls) == before
        await host.execute_command(lambda: owner.close_run_session(_close(run)))
        await host.execute_command(owner.shutdown)
        assert {thread for _, _, thread in calls} == {host._thread.ident}
    finally:
        host.stop()
