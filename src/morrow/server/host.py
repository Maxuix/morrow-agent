"""Core Host: one runtime thread/event loop owning all durable writes.

Per the frozen Stage 8 runtime contract C4, the thread-bound SQLite session is
never handed to ASGI workers. HTTP handlers submit mutation commands to a
bounded serialized bus — queue-full is explicit backpressure, never a silent
drop — and read projections run on the same Core loop. An explicit
``RunSupervisor`` owns at most one in-process driver per WorkflowRun; stopping
the host cancels drivers without recording any user cancellation.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import inspect
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("morrow.server")


class CommandBackpressureError(RuntimeError):
    """The bounded command bus is full; the client must retry explicitly."""


class CoreHostUnavailableError(RuntimeError):
    """The Core Host is closed or still starting."""


class CoreHostShutdownError(RuntimeError):
    """Force-stopped before context cleanup finished; resources may leak."""


class RunSupervisor:
    """Owns at most one in-process driver task per WorkflowRun.

    Driver tasks run on the Core loop. Cancellation stops driving only; the
    durable run state is untouched, so shutdown is never recorded as a user
    cancellation and a later process recovers from durable facts.
    """

    def __init__(self) -> None:
        self._drivers: dict[str, asyncio.Task] = {}
        self.execution_lock = asyncio.Lock()

    def is_driving(self, workflow_run_id: str) -> bool:
        task = self._drivers.get(workflow_run_id)
        return task is not None and not task.done()

    def ensure_driver(self, workflow_run_id: str, factory: Callable[[], Awaitable[Any]]) -> bool:
        """Start the run's driver unless one is already live; Core-loop only."""

        if self.is_driving(workflow_run_id):
            return False
        task = asyncio.ensure_future(self._guarded(workflow_run_id, factory))
        self._drivers[workflow_run_id] = task
        task.add_done_callback(
            lambda done: (
                self._drivers.pop(workflow_run_id, None)
                if self._drivers.get(workflow_run_id) is done
                else None
            )
        )
        return True

    async def wait_driver(self, workflow_run_id: str) -> None:
        task = self._drivers.get(workflow_run_id)
        if task is not None:
            await asyncio.shield(task)

    async def cancel_driver(self, workflow_run_id: str) -> None:
        task = self._drivers.get(workflow_run_id)
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def shutdown(self) -> None:
        tasks = [task for task in self._drivers.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _guarded(self, workflow_run_id: str, factory: Callable[[], Awaitable[Any]]) -> None:
        try:
            async with self.execution_lock:
                await factory()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # The durable store remains the authority: a driver exit leaves the
            # run recoverable, and the failure surface stays free of tracebacks.
            logger.warning("workflow driver for %s exited: %s", workflow_run_id, type(exc).__name__)
        finally:
            self._drivers.pop(workflow_run_id, None)


class ApprovalWaiters:
    """Live approval waiters registered by the server approval port.

    Touched only on the Core loop (driver coroutines and the command consumer),
    so no locking is required.
    """

    def __init__(self) -> None:
        self._waiters: dict[str, asyncio.Future] = {}
        self._claimed: set[str] = set()

    def waiting(self, approval_id: str) -> bool:
        future = self._waiters.get(approval_id)
        return future is not None and not future.done()

    def begin(self, approval_id: str) -> asyncio.Future:
        existing = self._waiters.get(approval_id)
        if existing is not None and not existing.done():
            return existing
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters[approval_id] = future
        return future

    def claim(self, approval_id: str) -> bool:
        """Reserve the sole live delivery before its durable decision commits."""

        future = self._waiters.get(approval_id)
        if future is None or future.done() or approval_id in self._claimed:
            return False
        self._claimed.add(approval_id)
        return True

    def deliver_claimed(self, approval_id: str, *, approved: bool) -> bool:
        future = self._waiters.get(approval_id)
        if future is None or future.done() or approval_id not in self._claimed:
            return False
        future.set_result(approved)
        return True

    def release(self, approval_id: str) -> None:
        self._claimed.discard(approval_id)

    def forget(self, approval_id: str) -> None:
        self._waiters.pop(approval_id, None)
        self._claimed.discard(approval_id)

    def cancel_all(self) -> None:
        for future in self._waiters.values():
            if not future.done():
                future.cancel()
        self._waiters.clear()
        self._claimed.clear()


class CoreHost:
    """Owns the Core runtime thread, its event loop and the serialized bus."""

    def __init__(
        self,
        build: Callable[[], Any],
        *,
        command_queue_size: int = 128,
    ) -> None:
        if command_queue_size < 1:
            raise ValueError("command queue size must be positive")
        self._build = build
        self._queue_size = command_queue_size
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._consumer: asyncio.Task | None = None
        self._context: Any = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._build_error: BaseException | None = None
        self._closing = False
        self._shutdown_closed = False
        self._startup_lock = threading.Lock()
        self.request_context = contextvars.ContextVar("morrow_workspace_context", default=None)

    @property
    def context(self) -> Any:
        if self._context is None:
            raise CoreHostUnavailableError("core host is not running")
        return self.request_context.get() or self._context

    @property
    def queued_commands(self) -> int:
        """Pending bus depth; an observability hook for tests and diagnostics."""

        return self._queue.qsize() if self._queue is not None else 0

    def start(self, *, timeout: float = 30.0) -> None:
        if self._thread is not None:
            raise RuntimeError("core host is already started")
        self._closing = False
        self._build_error = None
        self._shutdown_closed = False
        ready = threading.Event()
        self._ready = ready
        self._thread = threading.Thread(
            target=self._thread_main,
            args=(ready,),
            name="morrow-core-host",
            daemon=True,
        )
        self._thread.start()
        if not ready.wait(timeout=timeout):
            self._abort_start(timeout)
            raise CoreHostUnavailableError("core host did not start")
        if self._build_error is not None:
            error = self._build_error
            self._abort_start(timeout)
            raise error

    def stop(self, *, timeout: float = 30.0) -> None:
        loop, thread = self._loop, self._thread
        if loop is None or thread is None:
            return
        if loop.is_closed() or not thread.is_alive():
            self._reset()
            return
        self._closing = True
        try:
            future = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
        except RuntimeError:
            self._reset()
            return
        try:
            future.result(timeout=timeout)
        except TimeoutError:
            self._abort_shutdown(future, loop, thread, min(1.0, max(timeout, 0.5)))
            return
        except BaseException:
            if not loop.is_closed():
                loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=timeout)
            self._reset()
            raise
        if not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=timeout)
        self._reset()

    def _abort_shutdown(
        self,
        future: concurrent.futures.Future,
        loop: asyncio.AbstractEventLoop,
        thread: threading.Thread,
        grace: float,
    ) -> None:
        """_shutdown is wedged mid-flight: give it one short chance to finish
        context.close() after cancellation, then force the loop down."""
        self._wait_maintenance_bounded(grace)
        future.cancel()
        # The outer future is already cancelled, so result() cannot wait for
        # the teardown: cancellation reaches the loop asynchronously and the
        # close() in _shutdown's finally runs a tick later. Poll the recorded
        # outcome instead, bounded by the grace window.
        deadline = time.monotonic() + grace
        while not self._shutdown_closed and time.monotonic() < deadline:
            time.sleep(0.01)
        incomplete = not self._shutdown_closed
        if not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=grace)
        self._reset()
        if incomplete:
            raise CoreHostShutdownError(
                "core host cleanup did not finish; workspace resources may leak"
            )

    def _wait_maintenance_bounded(self, grace: float) -> None:
        """Give an uncancellable maintenance worker a bounded chance to finish
        so its guard (lock release) runs before the loop is force-stopped."""
        context = self._context
        registry = getattr(context, "workspaces", None) if context is not None else None
        task = getattr(registry, "maintenance_task", None)
        if task is None:
            return
        deadline = time.monotonic() + grace
        while not task.done() and time.monotonic() < deadline:
            time.sleep(0.01)

    def _abort_start(self, join_timeout: float) -> None:
        """Give up on a start that never became ready; keep the host reusable."""
        with self._startup_lock:
            # Invalidate this build before a retry can publish another context.
            self._ready = threading.Event()
            loop, thread = self._loop, self._thread
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        if thread is not None:
            thread.join(timeout=min(1.0, max(join_timeout, 0.5)))
        self._reset()

    def _reset(self) -> None:
        self._thread = None
        self._loop = None
        self._ready = threading.Event()
        self._context = None
        self._queue = None
        self._consumer = None

    async def execute_command(self, handler: Callable[[], Any]) -> Any:
        """Run one mutation on the serialized bounded command bus."""

        future = self._submit_to_bus(handler)
        return await asyncio.wrap_future(future)

    async def execute_query(self, handler: Callable[[], Any], *, blocking: bool = False) -> Any:
        """Run one read projection on the Core loop, outside the command bus.

        ``blocking=True`` marks a handler whose cost is synchronous file I/O; it
        runs on the Core loop's default executor instead of the loop itself, so
        slow disks cannot stall the command bus, heartbeats or other workspaces.
        A blocking handler must not touch Core-owned state: the thread-bound
        SQLite session and loop-affine structures reject foreign threads, and
        nothing serializes a worker against concurrent command-bus mutations.
        """

        if blocking:
            if self._loop is None or self._closing:
                raise CoreHostUnavailableError("core host is not running")
            scheduled = asyncio.run_coroutine_threadsafe(
                self._execute_blocking_query(handler), self._loop
            )
            return await asyncio.wrap_future(scheduled)

        result: concurrent.futures.Future = concurrent.futures.Future()

        def run() -> None:
            if not result.set_running_or_notify_cancel():
                return
            try:
                result.set_result(handler())
            except BaseException as exc:
                result.set_exception(exc)

        self._call_soon(run)
        return await asyncio.wrap_future(result)

    async def _execute_blocking_query(self, handler: Callable[[], Any]) -> Any:
        return await asyncio.get_running_loop().run_in_executor(None, handler)

    async def execute_preparation(self, handler: Callable[[], Awaitable[Any]]) -> Any:
        """Await read-only preparation on Core without blocking Pause/approval commands.

        The caller must submit any resulting mutation separately through the bus.
        No SQLite transaction may span this awaitable preparation.
        """
        if self._loop is None or self._closing:
            raise CoreHostUnavailableError("core host is not running")
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(handler(), self._loop))

    def _submit_to_bus(self, handler: Callable[[], Any]) -> concurrent.futures.Future:
        result: concurrent.futures.Future = concurrent.futures.Future()
        call_context = contextvars.copy_context()

        def enqueue() -> None:
            if self._closing:
                if result.set_running_or_notify_cancel():
                    result.set_exception(CoreHostUnavailableError("core host is shutting down"))
                return
            assert self._queue is not None
            try:
                self._queue.put_nowait((handler, result, call_context))
            except asyncio.QueueFull:
                if result.set_running_or_notify_cancel():
                    result.set_exception(
                        CommandBackpressureError("command bus is full; retry after a drain")
                    )

        self._call_soon(enqueue)
        return result

    def _call_soon(self, callback: Callable[[], None]) -> None:
        loop = self._loop
        if loop is None or self._closing:
            raise CoreHostUnavailableError("core host is not running")
        try:
            loop.call_soon_threadsafe(callback)
        except RuntimeError as exc:
            raise CoreHostUnavailableError("core host is not running") from exc

    def _thread_main(self, ready: threading.Event) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            context = self._build()
        except BaseException as exc:
            with self._startup_lock:
                if self._ready is ready:
                    self._build_error = exc
            ready.set()
            loop.close()
            return
        with self._startup_lock:
            abandoned = self._ready is not ready
            if not abandoned:
                self._loop = loop
                self._queue = asyncio.Queue(maxsize=self._queue_size)
                self._context = context
                self._consumer = loop.create_task(self._consume())
                ready.set()
        if abandoned:
            # The build owns its resources until publication, including after
            # timeout. Close on the constructing thread; never touch a retry.
            try:
                context.close()
            except Exception as exc:
                logger.warning("abandoned core context cleanup failed: %s", type(exc).__name__)
            finally:
                loop.close()
            return
        try:
            loop.run_forever()
        finally:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            # A timeout can race publication: the ready wait may expire just
            # before this thread publishes, so abort also owns this close path.
            if self._ready is not ready:
                try:
                    context.close()
                except Exception as exc:
                    logger.warning("abandoned core context cleanup failed: %s", type(exc).__name__)
            loop.close()

    async def _consume(self) -> None:
        assert self._queue is not None
        while True:
            item = await self._queue.get()
            if item is None:
                return
            handler, result, call_context = item
            # Atomically claim execution against cancellation on the caller loop.
            # Once running, cancelling the waiter cannot cancel the business result.
            if not result.set_running_or_notify_cancel():
                continue
            try:
                registry = getattr(self._context, "workspaces", None)
                if registry is not None:
                    registry.check_admission()
                value = call_context.run(handler)
                if inspect.isawaitable(value):
                    value = await call_context.run(asyncio.ensure_future, value)
            except BaseException as exc:
                result.set_exception(exc)
            else:
                result.set_result(value)

    async def _shutdown(self) -> None:
        self._closing = True
        context = self._context
        assert self._queue is not None
        try:
            # Drain accepted bus commands before cancelling their newly-created drivers.
            await self._queue.put(None)
            if self._consumer is not None:
                await self._consumer
            if context is not None:
                if getattr(context, "workspaces", None) is not None:
                    await context.workspaces.shutdown()
                elif getattr(context, "chat", None) is not None:
                    await context.chat.shutdown()
                await context.supervisor.shutdown()
                context.approval_waiters.cancel_all()
        finally:
            # Even a wedged or cancelled teardown must release the context
            # resources (workspace locks, SQLite): close() is synchronous, so
            # it still runs when the awaits above never finished.
            if context is not None:
                context.close()
                self._shutdown_closed = True
