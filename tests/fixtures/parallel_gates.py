"""Shared deterministic gates for the parallel-repair fixtures (P01.3).

All synchronization binds a Core-loop ``asyncio.Event`` plus a ``threading.Event``
progress report — never wall-clock sleeps. These helpers generalize the
file-private ``ModelGate`` (test_workflow_pause_continue_repair.py) and
``BarrierBank`` (test_stage8_readonly_parallel.py) patterns so the four lanes
share one implementation. They gate scripted providers only: tools themselves
are never gated by name or "looks read-only" heuristics (spec 3.3.6).
"""

from __future__ import annotations

import asyncio
import threading


class AsyncGate:
    """One-shot gate: the provider enters on the Core loop, the test releases."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self._release: asyncio.Event | None = None

    def bind(self) -> None:
        """Create the release event on the Core loop (inside a Core callback).

        The gated model request may already be in flight before the test can
        bind the gate, so the first ``wait`` creates the event lazily.
        """
        if self._release is None:
            self._release = asyncio.Event()

    async def wait(self) -> None:
        self.bind()
        self.entered.set()
        assert self._release is not None
        await self._release.wait()

    def release(self) -> None:
        assert self._release is not None, "gate was never bound to the Core loop"
        self._release.set()


def gate_provider_stream(provider, gate: AsyncGate, *, hold_before_event: int = 0):
    """Wrap ``provider.stream`` so it parks on ``gate`` before yielding event N.

    ``hold_before_event=0`` parks before the first event of every call that has
    not been released yet (no first token). A higher value lets earlier events
    through first, counted across calls: a script whose first item performs a
    tool call and whose second item is gated reproduces "tool committed, next
    model request waiting". A script with fewer events than ``hold_before_event``
    never engages the gate; the test fails on its own expectation.
    """

    seen = 0
    original = provider.stream

    async def stream(*args, **kwargs):
        nonlocal seen
        async for event in original(*args, **kwargs):
            if seen >= hold_before_event:
                await gate.wait()
            seen += 1
            yield event

    provider.stream = stream
    return provider


class BarrierBank:
    """Every scripted parallel leaf enters its own gate; the test releases all."""

    def __init__(self, count: int) -> None:
        if count < 1:
            raise ValueError("a barrier needs at least one gate")
        self.gates = [AsyncGate() for _ in range(count)]

    @property
    def all_entered(self) -> bool:
        return all(gate.entered.is_set() for gate in self.gates)

    def release(self) -> None:
        for gate in self.gates:
            gate.release()

    def bind(self) -> None:
        for gate in self.gates:
            gate.bind()

    async def await_all_entered(self, *, spins: int = 4000) -> None:
        for _ in range(spins):
            if self.all_entered:
                return
            await asyncio.sleep(0)
        raise AssertionError("not every gated leaf reached its model request")


async def await_entered(gate: AsyncGate, *, spins: int = 4000) -> None:
    for _ in range(spins):
        if gate.entered.is_set():
            return
        await asyncio.sleep(0)
    raise AssertionError("the gated provider never reached its model request")
