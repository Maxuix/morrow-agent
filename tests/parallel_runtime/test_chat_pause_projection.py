"""Lane A / P04 cross-lane accommodation: chat owner pause/resume projection.

Lane D renders pause/resume purely from the server ExecutionView projection
(P09: the client follows the projection). These tests pin the chat owner
projection contract on the real journal: pause advertised while a chat turn
runs, pausing while the intent is accepted, paused + resume once the turn
settled — and C0 behavior when the capability is disabled.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.execution import (
    STATE_PAUSED,
    STATE_PAUSING,
    STATE_RUNNING,
    ExecutionProjection,
)
from morrow.application.execution_pause import (
    ChatTurnPauseControl,
    ExecutionPauseService,
)
from morrow.core.execution_pause import TurnPauseSignal
from morrow.testing import FixedClock, FixedIdSource

WS = "ws_one"
SID = "ses_chat"


class _StubInteractions:
    """Minimal interactions seam: one active run, no user stops."""

    def __init__(self, active: str | None = None) -> None:
        self.active = active
        self.user_stops: set[str] = set()

    def active_run(self, session_id: str) -> str | None:
        return self.active


@pytest.fixture
def env(tmp_path):
    store = OperationalStore(tmp_path / "state", clock=FixedClock())
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle, clock=FixedClock().now)
    # initialize() creates the current pause tables directly.
    manager = SimpleNamespace(interactions=_StubInteractions())
    yield journal, manager, handle
    handle.close()


def _projection(journal, manager, *, enabled=True) -> ExecutionProjection:
    return ExecutionProjection(
        manager=manager,
        journal=journal,
        workspace_id=WS,
        chat_pause_enabled=enabled,
    )


def _service(journal) -> ExecutionPauseService:
    return ExecutionPauseService(
        journal, workspace_id=WS, id_source=FixedIdSource(), clock=FixedClock().now
    )


def test_chat_running_advertises_pause_but_default_keeps_c0(env):
    journal, manager, _ = env
    manager.interactions.active = "arun_chat"
    enabled = _projection(journal, manager, enabled=True)
    view = enabled._chat_execution(SID)
    assert view["owner"] == "chat" and view["state"] == STATE_RUNNING
    assert view["allowed_actions"] == ["pause", "stop"]
    # Composition that has not wired the chat pause endpoint keeps the C0 surface.
    disabled = _projection(journal, manager, enabled=False)
    assert disabled._chat_execution(SID)["allowed_actions"] == ["stop"]


def test_chat_pause_accept_replay_and_projection_lifecycle(env):
    journal, manager, _ = env
    service = _service(journal)
    manager.interactions.active = "arun_chat"
    projection = _projection(journal, manager)

    point = service.accept_chat_pause(SID, command_id="cmd_chat_pause_1")
    assert point.fact.control_generation == 1
    assert point.fact.owner == "chat_turn"
    replayed = service.accept_chat_pause(SID, command_id="cmd_chat_pause_1")
    assert replayed.pause_point_id == point.pause_point_id
    second = service.accept_chat_pause(SID, command_id="cmd_chat_pause_2")
    assert second.fact.control_generation == 2

    assert projection._chat_execution(SID)["state"] == STATE_PAUSING
    assert projection._chat_execution(SID)["allowed_actions"] == []

    settled = service.settle_chat_pause(SID)
    assert settled.fact.lifecycle == "suspended"
    # The turn ended: paused with resume; the surface keeps accepting input so
    # the user's continue/correction can be accepted (D07).
    manager.interactions.active = None
    paused_view = projection._chat_execution(SID)
    assert paused_view["state"] == STATE_PAUSED
    assert paused_view["allowed_actions"] == ["resume"]
    assert paused_view["accepts_chat_input"] is True

    resumed = service.resume_chat_pause(SID, command_id="cmd_chat_resume_1")
    assert resumed.fact.lifecycle == "resumed"
    assert projection._chat_execution(SID) is None
    # A fresh pause cycle starts a new generation on the same session.
    again = service.accept_chat_pause(SID, command_id="cmd_chat_pause_3")
    assert again.fact.control_generation == 3
    # Resuming an already-resumed cycle is an idempotent no-op.
    noop = service.resume_chat_pause(SID, command_id="cmd_chat_resume_2")
    assert noop.fact.lifecycle == "resumed"


@pytest.mark.asyncio
async def test_chat_turn_pause_control_ignores_stale_hints_and_wakes(env):
    journal, _, _ = env
    service = _service(journal)
    control = ChatTurnPauseControl(journal, WS)

    waiter = asyncio.ensure_future(control.wait_signal(SID))
    # A stale wake hint with no durable cycle is consumed, never trusted.
    control.interrupt(SID, TurnPauseSignal(control_generation=9, reason="user_interrupt"))
    await asyncio.sleep(0.02)
    assert not waiter.done()
    point = service.accept_chat_pause(SID, command_id="cmd_chat_pause_wake")
    control.interrupt(SID, TurnPauseSignal(control_generation=1, reason="user_interrupt"))
    await asyncio.wait_for(waiter, timeout=2)
    signal = control.pending_signal(SID)
    assert signal is not None and signal.control_generation == point.fact.control_generation
    control.clear(SID)
    # The durable authority stands until the drive settles the cycle.
    settled = service.settle_chat_pause(SID)
    assert settled.fact.lifecycle == "suspended"
    assert control.pending_signal(SID) is None


def test_chat_pause_projection_without_open_pause_cycle(tmp_path):
    """With the current schema but no pause cycle, chat stays C0-shaped."""
    store = OperationalStore(tmp_path / "state", clock=FixedClock())
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle, clock=FixedClock().now)
    manager = SimpleNamespace(interactions=_StubInteractions(active="arun_chat"))
    projection = _projection(journal, manager)
    view = projection._chat_execution(SID)
    assert view["allowed_actions"] == ["pause", "stop"]
    service = _service(journal)
    assert service.chat_pause_point(SID) is None
    handle.close()
