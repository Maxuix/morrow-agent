"""Keep admitted SDK coroutines alive when their Python caller stops waiting."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from morrow.core.computer_use import ComputerUseContractError


class NativeActionInterrupted(Exception):
    """Only the stable native completion category survives normalization."""

    def __init__(self, completion: str) -> None:
        self.completion = completion
        super().__init__("action_interrupted")


class NativeCalls:
    def __init__(self, on_quarantine: Callable[[], None], *, timeout: float | None = 15) -> None:
        self._on_quarantine = on_quarantine
        self._timeout = timeout
        self._waiter = asyncio.wait_for
        self._tasks: set[asyncio.Task] = set()
        self.accepting = True
        self.quarantined = False

    @property
    def pending(self) -> bool:
        return any(not task.done() for task in self._tasks)

    def stop(self) -> None:
        self.accepting = False

    def quarantine(self) -> None:
        self.stop()
        self.quarantined = True
        self._on_quarantine()

    async def run(self, operation: Callable[[], Awaitable[Any]], *, cleanup: bool = False) -> Any:
        if not cleanup and not self.accepting:
            raise ComputerUseContractError("driver_not_activated")
        if self.pending:
            raise ComputerUseContractError("desktop_busy")
        try:
            task = asyncio.create_task(operation())
        except Exception:
            raise ComputerUseContractError("driver_error") from None
        self._tasks.add(task)
        task.add_done_callback(self._done)
        try:
            return await self._waiter(asyncio.shield(task), self._timeout)
        except asyncio.CancelledError:
            self.quarantine()
            raise
        except TimeoutError:
            self.quarantine()
            raise ComputerUseContractError("driver_timeout") from None
        except ComputerUseContractError:
            raise
        except Exception as exc:
            if type(exc).__name__ == "ActionInterrupted":
                completion = getattr(getattr(exc, "completion", None), "name", "UNKNOWN")
                if completion not in {"NOT_STARTED", "COMPLETED", "UNKNOWN"}:
                    completion = "UNKNOWN"
                raise NativeActionInterrupted(completion) from None
            raise ComputerUseContractError("driver_error") from None

    def _done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            # Retrieve errors even when the original caller no longer waits.
            task.exception()

    async def settle(self) -> None:
        self.stop()
        if self._tasks:
            tasks = tuple(self._tasks)
            await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))
            for task in tasks:
                if task.done():
                    self._tasks.discard(task)
