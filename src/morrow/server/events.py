"""Additive lifecycle emission into the existing workspace event machinery.

The event stream is a projection aid, never a second truth: every emission is
a bounded ``ApplicationEvent`` appended through the same monotonic-cursor
journal the CLI polls, followed by a cursor-only notification so WebSocket
clients know to pull. Query/polling remains the complete fallback path.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from datetime import datetime

from morrow.core.application import ApplicationEvent


class EventHub:
    """Fan-out of ``latest_cursor`` hints to subscribers on foreign event loops.

    ``publish`` runs on the Core thread; subscriptions carry their owning loop
    so notification crosses loop boundaries through ``call_soon_threadsafe``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = {}
        self._next_token = 0

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> tuple[int, asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._next_token += 1
            token = self._next_token
            self._subscribers[token] = (loop, queue)
        return token, queue

    def unsubscribe(self, token: int) -> None:
        with self._lock:
            self._subscribers.pop(token, None)

    def publish(self, latest_cursor: int) -> None:
        with self._lock:
            subscribers = tuple(self._subscribers.values())
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, latest_cursor)
            except RuntimeError:
                # Subscriber loop already closed; the next pull resyncs.
                pass


class WorkflowEventEmitter:
    """Appends bounded additive lifecycle events and hints subscribers."""

    def __init__(
        self,
        journal,
        *,
        workspace_id: str,
        id_source,
        clock: Callable[[], datetime],
        hub: EventHub,
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.clock = clock
        self.hub = hub

    def emit(
        self,
        event_type: str,
        aggregate_kind: str,
        aggregate_id: str,
        payload: dict,
    ) -> ApplicationEvent:
        event = self.journal.put_application_event(
            self.workspace_id,
            ApplicationEvent(
                event_id=self.id_source.new_id("evt"),
                workspace_id=self.workspace_id,
                event_type=event_type,
                aggregate_kind=aggregate_kind,
                aggregate_id=aggregate_id,
                payload=payload,
                created_at=self.clock(),
            ),
        )
        self.hub.publish(event.cursor)
        return event
