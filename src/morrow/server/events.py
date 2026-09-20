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

from morrow.core.application import ApplicationError, ApplicationErrorCode, ApplicationEvent


class EventHub:
    """Coalesced cursor hints: one queued value and one scheduled wake per subscriber."""

    def __init__(self, *, max_subscribers=64) -> None:
        self._lock = threading.Lock()
        self._subscribers = {}
        self._next_token = 0
        self.max_subscribers = max_subscribers
        self.peak_scheduled = 0

    def subscribe(self, loop):
        queue = asyncio.Queue(maxsize=1)
        with self._lock:
            if len(self._subscribers) >= self.max_subscribers:
                raise ApplicationError(ApplicationErrorCode.BUSY, "Subscription limit reached")
            self._next_token += 1
            token = self._next_token
            self._subscribers[token] = [loop, queue, 0, False]
        return token, queue

    def unsubscribe(self, token):
        with self._lock:
            self._subscribers.pop(token, None)

    def publish(self, latest_cursor):
        with self._lock:
            for token, subscriber in tuple(self._subscribers.items()):
                subscriber[2] = max(subscriber[2], latest_cursor)
                if subscriber[3]:
                    continue
                subscriber[3] = True
                try:
                    subscriber[0].call_soon_threadsafe(self._deliver, token)
                except RuntimeError:
                    self._subscribers.pop(token, None)
            self.peak_scheduled = max(
                self.peak_scheduled, sum(s[3] for s in self._subscribers.values())
            )

    def _deliver(self, token):
        # Runs on the receiving loop. Queue replacement never crosses threads.
        with self._lock:
            subscriber = self._subscribers.get(token)
            if subscriber is None:
                return
            _, queue, latest, _ = subscriber
            subscriber[3] = False
        if queue.full():
            latest = max(latest, queue.get_nowait())
        queue.put_nowait(latest)


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
        # In-process observers of every emitted lifecycle event; the Chat reply
        # streams use this seam to refresh the owning sessions' projections.
        self.subscribers: list[Callable[[str, str, str, dict], None]] = []

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
        self.journal.after_commit(lambda: self.hub.publish(event.cursor))
        for subscriber in tuple(self.subscribers):
            subscriber(event_type, aggregate_kind, aggregate_id, payload)
        return event
