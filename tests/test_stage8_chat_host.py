"""Real Core owner cancellation boundaries; barriers avoid timing assumptions."""

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from morrow.server.host import (
    ApprovalWaiters,
    CoreHost,
    CoreHostUnavailableError,
    RunSupervisor,
)


@pytest.fixture
def host():
    owner = CoreHost(
        lambda: SimpleNamespace(
            supervisor=RunSupervisor(), approval_waiters=ApprovalWaiters(), close=lambda: None
        )
    )
    owner.start()
    yield owner
    owner.stop()


async def test_blocking_query_runs_off_core_loop_and_loop_stays_responsive(host):
    entered = asyncio.Event()
    release = threading.Event()
    caller_loop = asyncio.get_running_loop()
    cell = {}

    def blocking_handler():
        cell["thread"] = threading.current_thread()
        caller_loop.call_soon_threadsafe(entered.set)
        assert release.wait(2)
        return "file-bytes"

    waiter = asyncio.create_task(host.execute_query(blocking_handler, blocking=True))
    await entered.wait()
    try:
        # The Core loop must answer an ordinary query while the blocking
        # handler is still in flight on an executor worker.
        assert await asyncio.wait_for(host.execute_query(lambda: "alive"), 2) == "alive"
    finally:
        release.set()
    assert await waiter == "file-bytes"
    assert cell["thread"] is not host._thread


async def test_blocking_query_requires_running_host():
    host = CoreHost(
        lambda: SimpleNamespace(
            supervisor=RunSupervisor(), approval_waiters=ApprovalWaiters(), close=lambda: None
        )
    )
    with pytest.raises(CoreHostUnavailableError, match="not running"):
        await host.execute_query(lambda: "x", blocking=True)


@pytest.mark.parametrize("fails", [False, True])
async def test_cancel_waiter_after_handler_started_preserves_consumer(host, fails):
    started = asyncio.Event()
    caller_loop = asyncio.get_running_loop()
    cell = {}

    async def handler():
        cell["release"] = asyncio.Event()
        caller_loop.call_soon_threadsafe(started.set)
        await cell["release"].wait()
        cell["committed"] = True
        if fails:
            raise ValueError("safe failure")
        return "receipt"

    waiter = asyncio.create_task(host.execute_command(handler))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    await host.execute_query(lambda: cell["release"].set())
    # Bounded timeout is a failure guard, not a success/timing assertion.
    assert await asyncio.wait_for(host.execute_command(lambda: "alive"), 2) == "alive"
    assert cell["committed"]


async def test_cancel_before_admission_skips_handler(host):
    started = asyncio.Event()
    caller_loop = asyncio.get_running_loop()
    cell = {}

    async def blocker():
        cell["release"] = asyncio.Event()
        caller_loop.call_soon_threadsafe(started.set)
        await cell["release"].wait()

    first = asyncio.create_task(host.execute_command(blocker))
    await started.wait()
    pending = host._submit_to_bus(lambda: cell.update(unexpected=True))
    assert pending.cancel()
    await host.execute_query(lambda: cell["release"].set())
    await first
    assert await host.execute_command(lambda: "alive") == "alive"
    assert "unexpected" not in cell


@pytest.mark.parametrize("fails", [False, True])
async def test_cancel_query_waiter_during_delivery(host, fails):
    import threading

    entered = asyncio.Event()
    release = threading.Event()
    caller_loop = asyncio.get_running_loop()

    def query():
        caller_loop.call_soon_threadsafe(entered.set)
        assert release.wait(2)
        if fails:
            raise ValueError("safe failure")
        return "view"

    waiter = asyncio.create_task(host.execute_query(query))
    await entered.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    assert await host.execute_query(lambda: "alive") == "alive"


async def test_asgi_task_cancel_after_committed_session_remains_queryable(tmp_path, monkeypatch):
    from morrow.server.commands import ServerCommands
    from test_stage8_core_api import ServerFixture

    fixture = ServerFixture(tmp_path)
    entered = asyncio.Event()
    caller_loop = asyncio.get_running_loop()
    cell = {}
    original = ServerCommands.session_create

    async def held(self, request):
        outcome = original(self, request)
        cell["id"] = outcome.result["session"]["session_id"]
        cell["release"] = asyncio.Event()
        caller_loop.call_soon_threadsafe(entered.set)
        await cell["release"].wait()
        return outcome

    monkeypatch.setattr(ServerCommands, "session_create", held)
    try:
        request = asyncio.create_task(fixture.client.post("/v1/sessions", {}))
        await entered.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        await fixture.on_core(lambda: cell["release"].set())
        response = await fixture.client.get("/v1/sessions/" + cell["id"])
        assert response.status == 200
        assert await fixture.host.execute_command(lambda: "alive") == "alive"
    finally:
        fixture.close()


# Shutdown/start lifecycle under wedged teardown (batch 2) ---------------------


class _WedgedWorkspaces:
    """shutdown() never returns: exercises the force-stop path."""

    async def shutdown(self):
        await asyncio.Event().wait()


class _MaintenanceWorkspaces:
    """Mirrors workspaces.shutdown(): shields a maintenance task whose guard
    must still run when the loop is force-stopped."""

    def __init__(self):
        self.maintenance_task = None
        self.guard_released = threading.Event()

    async def _drive(self):
        try:
            await asyncio.Event().wait()
        finally:
            self.guard_released.set()

    def start_maintenance(self):
        self.maintenance_task = asyncio.create_task(self._drive())
        return self.maintenance_task

    async def shutdown(self):
        if self.maintenance_task is not None:
            await asyncio.shield(self.maintenance_task)


def _context(workspaces, closed):
    return SimpleNamespace(
        workspaces=workspaces,
        supervisor=RunSupervisor(),
        approval_waiters=ApprovalWaiters(),
        close=closed.set,
    )


async def test_stop_timeout_cancels_wedged_shutdown_but_still_closes_context():
    closed = threading.Event()
    host = CoreHost(lambda: _context(_WedgedWorkspaces(), closed))
    host.start()
    host.stop(timeout=0.05)
    assert closed.is_set()
    assert host._thread is None


async def test_stop_waits_bounded_for_maintenance_and_releases_its_guard():
    closed = threading.Event()
    workspaces = _MaintenanceWorkspaces()
    host = CoreHost(lambda: _context(workspaces, closed))
    host.start()
    await host.execute_query(workspaces.start_maintenance)
    host.stop(timeout=0.05)
    assert closed.is_set()
    assert workspaces.guard_released.is_set()
    assert host._thread is None


def test_start_timeout_leaves_host_reusable():
    block = threading.Event()
    started = threading.Event()
    holder = {}

    def slow_build():
        holder["thread"] = threading.current_thread()
        started.set()
        block.wait(timeout=5)  # failure guard; the abort path must not need it
        return _context(_WedgedWorkspaces(), threading.Event())

    host = CoreHost(slow_build)
    with pytest.raises(CoreHostUnavailableError, match="did not start"):
        host.start(timeout=0.05)
    assert started.is_set()
    assert host._thread is None
    block.set()
    for _ in range(200):
        if not holder["thread"].is_alive():
            break
        time.sleep(0.01)
    assert not holder["thread"].is_alive(), "abandoned start thread must exit"

    closed = threading.Event()
    host = CoreHost(lambda: _context(_WedgedWorkspaces(), closed))
    host.start()
    host.stop()
    assert closed.is_set()


@pytest.mark.parametrize("old_build_fails", [False, True])
def test_same_host_retry_cannot_be_overwritten_by_abandoned_build(old_build_fails):
    release = threading.Event()
    old_closed = threading.Event()
    new_closed = threading.Event()
    builds = []

    def build():
        builds.append(threading.current_thread())
        if len(builds) == 1:
            assert release.wait(5)
            if old_build_fails:
                raise RuntimeError("abandoned build failed")
            return _context(None, old_closed)
        return _context(None, new_closed)

    host = CoreHost(build)
    try:
        with pytest.raises(CoreHostUnavailableError):
            host.start(timeout=0.01)
        host.start()
        new_context, new_loop = host.context, host._loop
        release.set()
        builds[0].join(timeout=2)
        assert not builds[0].is_alive()
        assert host.context is new_context
        assert host._loop is new_loop
        assert host._build_error is None
        assert old_closed.is_set() is not old_build_fails
        assert not new_closed.is_set()
    finally:
        release.set()
        host.stop()
    assert new_closed.is_set()
    with pytest.raises(CoreHostUnavailableError):
        _ = host.context


def test_start_timeout_racing_context_publication_closes_context(monkeypatch):
    closed = threading.Event()
    host = CoreHost(lambda: _context(None, closed))
    original = threading.Event.wait

    def expired_wait(event, timeout=None):
        value = original(event, timeout)
        # Model a wait deadline expiring just before the builder publishes.
        return False if event is host._ready else value

    monkeypatch.setattr(threading.Event, "wait", expired_wait)
    with pytest.raises(CoreHostUnavailableError):
        host.start(timeout=1)
    assert closed.is_set()
    assert host._thread is None
