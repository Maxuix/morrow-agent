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
from test_computer_use_driver import NOW, _Native, _process_birth, _scope, _sdk


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


class _Lease:
    def __init__(self):
        self.held = False

    def acquire(self):
        if self.held:
            raise ComputerUseContractError("desktop_busy")
        self.held = True

    def release(self):
        self.held = False


def _owner(lease=None):
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
        lease=lease or _Lease(),
        process_reader=_process_birth,
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
    stopping = asyncio.create_task(owner.shutdown())
    release.set()
    await opening
    await stopping


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


async def test_cancelled_observation_holds_lease_until_native_finishes_and_close_settles():
    lease = _Lease()
    owner, calls, natives = _owner(lease)
    run = await owner.open_run_session(_open())
    session = owner.session_for(run)
    entered, release = asyncio.Event(), asyncio.Event()
    native_done = asyncio.Event()

    async def waiting(payload):
        entered.set()
        try:
            await release.wait()
            return SimpleNamespace(apps=[])
        finally:
            native_done.set()

    natives[0].list_apps = waiting
    waiter = asyncio.create_task(
        session.discover(
            DiscoverRequest(
                authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                scope=_scope(),
                run_session_id=run.run_session_id,
            )
        )
    )
    await entered.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert session.pending and owner.quarantined and lease.held
    assert not native_done.is_set()
    with pytest.raises(ComputerUseContractError, match="driver_not_activated"):
        owner.session_for(run)
    closing = asyncio.create_task(owner.close_run_session(_close(run)))
    assert lease.held
    release.set()
    await closing
    assert native_done.is_set()
    assert not lease.held
    await owner.shutdown()


async def test_cancelled_session_start_is_not_replayed_or_disposed_before_settling():
    lease = _Lease()
    owner, calls, _ = _owner(lease)
    entered, release, disposed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Waiting(_Native):
        async def start_session(self, payload):
            entered.set()
            await release.wait()
            return SimpleNamespace()

        async def end_session(self, payload):
            pass

        def close(self):
            disposed.set()

    owner._session_factory = lambda driver, name: Waiting()
    opening = asyncio.create_task(owner.open_run_session(_open()))
    await entered.wait()
    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening
    assert lease.held and not disposed.is_set()
    stopping = asyncio.create_task(owner.shutdown())
    release.set()
    await stopping
    assert disposed.is_set() and not lease.held
    assert [name for name, _, _ in calls].count("create") == 1


async def test_cancelled_shutdown_keeps_the_same_native_shutdown_live():
    lease = _Lease()
    owner, _, _ = _owner(lease)
    await owner.open_run_session(_open())
    entered, release = asyncio.Event(), asyncio.Event()
    invocations = []

    async def waiting():
        invocations.append(1)
        entered.set()
        await release.wait()

    owner._driver.shutdown = waiting
    stopping = asyncio.create_task(owner.shutdown())
    await entered.wait()
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    assert lease.held and owner.shutdown_pending
    release.set()
    await owner.shutdown()
    assert invocations == [1]
    assert not lease.held and not owner.shutdown_pending


async def test_core_host_retains_owner_loop_on_desktop_shutdown_timeout():
    from morrow.server.host import (
        ApprovalWaiters,
        CoreHost,
        CoreHostShutdownError,
        RunSupervisor,
    )

    closed = threading.Event()
    host = CoreHost(
        lambda: SimpleNamespace(
            supervisor=RunSupervisor(),
            approval_waiters=ApprovalWaiters(),
            close=closed.set,
            computer_use=None,
        )
    )
    host.start()
    release = threading.Event()
    entered = threading.Event()
    try:
        owner, _, _ = await host.execute_command(_owner)
        await host.execute_command(lambda: owner.open_run_session(_open()))

        async def waiting():
            entered.set()
            await asyncio.to_thread(release.wait)

        await host.execute_query(lambda: setattr(owner._driver, "shutdown", waiting))
        await host.execute_query(lambda: setattr(host.context, "computer_use", owner))
        with pytest.raises(CoreHostShutdownError, match="owner loop and lease retained"):
            await asyncio.to_thread(lambda: host.stop(timeout=0))
        assert await asyncio.to_thread(entered.wait, 2)
        assert host._thread.is_alive() and not closed.is_set()
        assert owner._lease.held
        release.set()
        await asyncio.to_thread(host.stop)
        assert closed.is_set() and not owner._lease.held
    finally:
        release.set()
        if host._thread is not None:
            await asyncio.to_thread(host.stop)


async def test_lazy_application_lifecycle_checks_gate_before_driver_construction():
    from morrow.application.computer_use import ComputerUseLifecycle
    from morrow.core.computer_use import ComputerUsePreflight

    constructed = []

    def factory():
        owner, _, _ = _owner()
        constructed.append(owner)
        return owner

    for reason in ["disabled", "sdk_missing", "tcc_missing", "native_unverified"]:
        lifecycle = ComputerUseLifecycle(
            factory,
            lambda reason=reason: ComputerUsePreflight(status="unavailable", reason=reason),
        )
        with pytest.raises(ComputerUseContractError, match=reason):
            await lifecycle.open_run_session(_open())
        assert not lifecycle.shutdown_pending
        await lifecycle.shutdown()
    assert constructed == []
    lifecycle = ComputerUseLifecycle(
        factory,
        lambda: ComputerUsePreflight(status="unavailable", reason="driver_not_activated"),
        native_verified=True,
    )
    run = await lifecycle.open_run_session(_open())
    await lifecycle.close_run_session(_close(run))
    await lifecycle.shutdown()
    assert len(constructed) == 1


def test_pinned_session_factory_uses_immutable_named_standard_surface():
    from morrow.adapters.computer_use.sdk_loader import construct_run_session

    seen = []
    sdk = SimpleNamespace(
        TrustedSessionOptions=lambda **kwargs: kwargs,
        SessionPermissionMode=SimpleNamespace(STANDARD="standard"),
        create_trusted_session=lambda driver, options: seen.append((driver, options)),
    )
    construct_run_session(sdk, "driver", "crun_test", lifetime_seconds=120)
    assert seen == [
        (
            "driver",
            {
                "public_session": "crun_test",
                "mode": "standard",
                "ttl_seconds": 120,
                "idle_ttl_seconds": 120,
                "capability_manifest_path": None,
                "bounded_manifest_path": None,
            },
        )
    ]


async def test_timeout_reports_unknown_and_does_not_cancel_native_action_or_unlock():
    from morrow.core.computer_use import PreparedComputerAction, PressKeyAction

    lease = _Lease()
    owner, _, natives = _owner(lease)
    run = await owner.open_run_session(_open())
    session = owner.session_for(run)
    targets = await session.discover(
        DiscoverRequest(
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
            scope=_scope(),
            run_session_id=run.run_session_id,
        )
    )
    entered, release = asyncio.Event(), asyncio.Event()
    finished = []

    async def waiting(payload):
        entered.set()
        await release.wait()
        finished.append(1)
        return SimpleNamespace()

    async def expire(shielded, timeout):
        await entered.wait()
        shielded.cancel()
        raise TimeoutError

    natives[0].press_key = waiting
    original_waiter = session._calls._waiter
    session._calls._waiter = expire
    outcome = await session.execute(
        PreparedComputerAction(
            action=PressKeyAction(type="press_key", key="enter"), window_point=None
        ),
        window_identity=targets.targets[0].window_identity,
        delivery=_scope().delivery,
        agent_run_id="arun_1",
        generation=1,
    )
    assert outcome.status == "unknown" and outcome.error_code == "driver_timeout"
    assert lease.held and session.pending and finished == []
    session._calls._waiter = original_waiter
    release.set()
    await owner.shutdown()
    assert finished == [1] and not lease.held
