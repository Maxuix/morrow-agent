"""Session-owned runtimes and drivers sharing the workspace execution gate."""

from __future__ import annotations

import asyncio
from collections import OrderedDict

from morrow.core.application import ApplicationError, ApplicationErrorCode

MAX_SESSION_RUNTIMES = 8
MAX_RUNTIME_HISTORY_BYTES = 16 * 1024 * 1024


class SessionRuntimeManager:
    """One live log/committer per Session, all on the Core owner loop."""

    def __init__(self, *, workspace_id, api, factory, execution_lock, history_bytes=None):
        self.workspace_id = workspace_id
        self.api = api
        self.factory = factory
        self.history_bytes = history_bytes
        self.execution_lock = execution_lock
        self.recovery_lock = execution_lock
        self.runtimes = OrderedDict()
        self.drivers = {}

    def require_session(self, session_id):
        session = self.api.get_session(session_id)
        if session is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Session is missing")
        return session

    def runtime(self, session_id, model=None):
        self.require_session(session_id)
        if (
            self.history_bytes is not None
            and self.history_bytes(session_id) > MAX_RUNTIME_HISTORY_BYTES
        ):
            raise ApplicationError(
                ApplicationErrorCode.BUSY, "Session history exceeds runtime capacity"
            )
        if session_id in self.runtimes:
            self.runtimes.move_to_end(session_id)
            return self.runtimes[session_id]
        while len(self.runtimes) >= MAX_SESSION_RUNTIMES:
            idle = next((key for key in self.runtimes if key not in self.drivers), None)
            if idle is None:
                raise ApplicationError(ApplicationErrorCode.BUSY, "Session runtime cache is busy")
            # No store close: all committers share the Core-owned handle.
            del self.runtimes[idle]
        runtime = self.factory(session_id, model)
        self.runtimes[session_id] = runtime
        return runtime

    def ensure_driver(self, session_id, factory, *, per_run_gate=False):
        self.require_session(session_id)
        if session_id in self.drivers:
            return False
        task = asyncio.create_task(self._drive(session_id, factory, per_run_gate=per_run_gate))
        self.drivers[session_id] = task
        task.add_done_callback(
            lambda done: (
                self.drivers.pop(session_id, None) if self.drivers.get(session_id) is done else None
            )
        )
        return True

    async def _drive(self, session_id, factory, *, per_run_gate=False):
        try:
            if per_run_gate:
                await factory()
            else:
                async with self.execution_lock:
                    await factory()
        finally:
            self.drivers.pop(session_id, None)

    async def shutdown(self):
        if hasattr(self, "planning"):
            await self.planning.shutdown()
        if hasattr(self, "attachments"):
            await self.attachments.shutdown()
        tasks = tuple(self.drivers.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.drivers.clear()
        self.runtimes.clear()
