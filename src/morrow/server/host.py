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
import inspect
import logging
import threading
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("morrow.server")


class CommandBackpressureError(RuntimeError):
    """The bounded command bus is full; the client must retry explicitly."""


class CoreHostUnavailableError(RuntimeError):
    """The Core Host is closed or still starting."""


class RunSupervisor:
    """Owns at most one in-process driver task per WorkflowRun.

    Driver tasks run on the Core loop. Cancellation stops driving only; the
    durable run state is untouched, so shutdown is never recorded as a user
    cancellation and a later process recovers from durable facts.
    """

    def __init__(self) -> None:
        self._drivers: dict[str, asyncio.Task] = {}

    def is_driving(self, workflow_run_id: str) -> bool:
        task = self._drivers.get(workflow_run_id)
        return task is not None and not task.done()

    def ensure_driver(self, workflow_run_id: str, factory: Callable[[], Awaitable[Any]]) -> bool:
        """Start the run's driver unless one is already live; Core-loop only."""

        if self.is_driving(workflow_run_id):
            return False
        self._drivers[workflow_run_id] = asyncio.ensure_future(
            self._guarded(workflow_run_id, factory)
        )
        return True

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
            await factory()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # The durable store remains the authority: a driver exit leaves the
            # run recoverable, and the failure surface stays free of tracebacks.
            logger.warning("workflow driver for %s exited: %s", workflow_run_id, type(exc).__name__)


class ApprovalWaiters:
    """Live approval waiters registered by the server approval port.

    Touched only on the Core loop (driver coroutines and the command consumer),
    so no locking is required.
    """

    def __init__(self) -> None:
        self._waiters: dict[str, asyncio.Future] = {}

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

    def deliver(self, approval_id: str, *, approved: bool) -> bool:
        future = self._waiters.get(approval_id)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True

    def forget(self, approval_id: str) -> None:
        self._waiters.pop(approval_id, None)

    def cancel_all(self) -> None:
        for future in self._waiters.values():
            if not future.done():
                future.cancel()
        self._waiters.clear()


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

    @property
    def context(self) -> Any:
        if self._context is None:
            raise CoreHostUnavailableError("core host is not running")
        return self._context

    @property
    def queued_commands(self) -> int:
        """Pending bus depth; an observability hook for tests and diagnostics."""

        return self._queue.qsize() if self._queue is not None else 0

    def start(self, *, timeout: float = 30.0) -> None:
        if self._thread is not None:
            raise RuntimeError("core host is already started")
        self._thread = threading.Thread(
            target=self._thread_main, name="morrow-core-host", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            raise CoreHostUnavailableError("core host did not start")
        if self._build_error is not None:
            self._thread.join(timeout=timeout)
            raise self._build_error

    def stop(self, *, timeout: float = 30.0) -> None:
        loop, thread = self._loop, self._thread
        if loop is None or thread is None:
            return
        self._closing = True
        future = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
        try:
            future.result(timeout=timeout)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=timeout)
        self._thread = None
        self._loop = None

    async def execute_command(self, handler: Callable[[], Any]) -> Any:
        """Run one mutation on the serialized bounded command bus."""

        future = self._submit_to_bus(handler)
        return await asyncio.wrap_future(future)

    async def execute_query(self, handler: Callable[[], Any]) -> Any:
        """Run one read projection on the Core loop, outside the command bus."""

        result: concurrent.futures.Future = concurrent.futures.Future()

        def run() -> None:
            try:
                result.set_result(handler())
            except BaseException as exc:
                result.set_exception(exc)

        self._call_soon(run)
        return await asyncio.wrap_future(result)

    def _submit_to_bus(self, handler: Callable[[], Any]) -> concurrent.futures.Future:
        result: concurrent.futures.Future = concurrent.futures.Future()

        def enqueue() -> None:
            if self._closing:
                result.set_exception(CoreHostUnavailableError("core host is shutting down"))
                return
            assert self._queue is not None
            try:
                self._queue.put_nowait((handler, result))
            except asyncio.QueueFull:
                result.set_exception(
                    CommandBackpressureError("command bus is full; retry after a drain")
                )

        self._call_soon(enqueue)
        return result

    def _call_soon(self, callback: Callable[[], None]) -> None:
        loop = self._loop
        if loop is None:
            raise CoreHostUnavailableError("core host is not running")
        try:
            loop.call_soon_threadsafe(callback)
        except RuntimeError as exc:
            raise CoreHostUnavailableError("core host is not running") from exc

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._queue = asyncio.Queue(maxsize=self._queue_size)
        try:
            self._context = self._build()
        except BaseException as exc:
            self._build_error = exc
            self._ready.set()
            loop.close()
            return
        self._consumer = loop.create_task(self._consume())
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    async def _consume(self) -> None:
        assert self._queue is not None
        while True:
            item = await self._queue.get()
            if item is None:
                return
            handler, result = item
            if result.cancelled():
                continue
            try:
                value = handler()
                if inspect.isawaitable(value):
                    value = await value
            except BaseException as exc:
                result.set_exception(exc)
            else:
                result.set_result(value)

    async def _shutdown(self) -> None:
        self._closing = True
        context = self._context
        if context is not None:
            await context.supervisor.shutdown()
            context.approval_waiters.cancel_all()
        assert self._queue is not None
        await self._queue.put(None)
        if self._consumer is not None:
            await self._consumer
        if context is not None:
            context.close()
