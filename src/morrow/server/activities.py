"""Bounded per-session activity stream state (master plan P2.1/P2.2).

Reducer semantics: upsert by ``activity_id``, reject stale revisions, and never
let a terminal item regress to a non-terminal state. Metadata and transient
content are bounded by the master-plan constants; eviction prefers completed
items' content and keeps states and references. This is display projection
only: it never owns durable facts and never aborts execution.
"""

from __future__ import annotations

import json
import time
import weakref
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from morrow.core.activity import ACTIVITY_DELTA_MAX_BYTES, TERMINAL_ACTIVITY_STATES

# Master plan section 4 budgets (proposal 4.6); mirrored in gui/src/api/activity.ts.
ACTIVITY_METADATA_LIMIT = 256
ACTIVITY_PREVIEW_TOTAL_BYTES = 512 * 1024
ACTIVITY_PREVIEW_ITEM_BYTES = 32 * 1024
ACTIVITY_RING_BYTES = 1024 * 1024
ACTIVITY_RING_FRAMES = 1024
ACTIVITY_HOST_TOTAL_BYTES = 32 * 1024 * 1024
ACTIVITY_COMPLETED_CONTENT_TTL_SECONDS = 600.0
ACTIVITY_DELIVERY_BYTES = 64 * 1024


@dataclass
class ActivityEntry:
    """One activity's metadata plus bounded transient content."""

    item: dict
    content: str = ""
    content_bytes: int = 0
    completed_monotonic: float | None = None

    @property
    def terminal(self) -> bool:
        return self.item["state"] in TERMINAL_ACTIVITY_STATES


@dataclass(eq=False)
class ActivityStreamState:
    """Self-consistent activity stream: own epoch, sequence, ring and entries.

    Identity-hashed (``eq=False``) so live states can register in the host
    budget's WeakSet.
    """

    epoch: str = field(default_factory=lambda: uuid4().hex)
    sequence: int = 0
    frames: deque = field(default_factory=deque)
    ring_bytes: int = 0
    peak_bytes: int = 0
    entries: OrderedDict = field(default_factory=OrderedDict)
    preview_bytes: int = 0
    drops: dict = field(default_factory=dict)

    def count_drop(self, reason: str) -> int:
        self.drops[reason] = self.drops.get(reason, 0) + 1
        return self.drops[reason]

    def emit(self, kind: str, payload: dict, workspace_id: str, session_id: str) -> dict:
        self.sequence += 1
        frame = {
            "protocol_version": 1,
            "workspace_id": workspace_id,
            "session_id": session_id,
            "stream_epoch": self.epoch,
            "sequence": self.sequence,
            "type": kind,
            "agent_run_id": None,
            "message_id": None,
            "payload": payload,
        }
        size = len(json.dumps(frame, ensure_ascii=False).encode())
        self.frames.append((frame, size))
        self.ring_bytes += size
        while len(self.frames) > ACTIVITY_RING_FRAMES or self.ring_bytes > ACTIVITY_RING_BYTES:
            _, removed = self.frames.popleft()
            self.ring_bytes -= removed
            self.count_drop("ring_evicted")
        self.peak_bytes = max(self.peak_bytes, self.ring_bytes)
        return frame

    def upsert(self, item: dict) -> bool:
        """Insert or update one activity item; returns False when rejected."""
        activity_id = item["activity_id"]
        existing = self.entries.get(activity_id)
        if existing is not None:
            if item["revision"] < existing.item["revision"]:
                self.count_drop("stale_revision")
                return False
            if existing.terminal and item["state"] not in TERMINAL_ACTIVITY_STATES:
                # A late running/preparing report must not overwrite a terminal
                # outcome (A09: stale snapshots never regress terminals).
                self.count_drop("terminal_regression")
                return False
            if item["revision"] == existing.item["revision"] and item == existing.item:
                return False
        if existing is not None:
            item = dict(item)
            # Metadata producers do not own the preview or its durable ref.
            if existing.item.get("content_ref") and not item.get("content_ref"):
                item["content_ref"] = existing.item["content_ref"]
            item["truncated"] = item.get("truncated", False) or existing.item.get(
                "truncated", False
            )
        entry = existing if existing is not None else ActivityEntry(item=item)
        entry.item = item
        if entry.terminal and entry.completed_monotonic is None:
            entry.completed_monotonic = time.monotonic()
        self.entries[activity_id] = entry
        self.entries.move_to_end(activity_id)
        while len(self.entries) > ACTIVITY_METADATA_LIMIT:
            _, evicted = self.entries.popitem(last=False)
            self.preview_bytes -= evicted.content_bytes
            self.count_drop("metadata_evicted")
        return True

    def append_delta(self, activity_id: str, delta: str) -> bool:
        """Accumulate bounded projected text; unknown ids drop the delta."""
        entry = self.entries.get(activity_id)
        if entry is None:
            self.count_drop("delta_unknown_activity")
            return False
        if not delta:
            return False
        encoded = delta.encode()
        if len(encoded) > ACTIVITY_DELTA_MAX_BYTES:
            self.count_drop("delta_oversize")
            return False
        if entry.content_bytes + len(encoded) > ACTIVITY_PREVIEW_ITEM_BYTES:
            entry.item["truncated"] = True
            self.count_drop("preview_item_cap")
            return True
        if self.preview_bytes + len(encoded) > ACTIVITY_PREVIEW_TOTAL_BYTES:
            self._evict_completed_content()
            if self.preview_bytes + len(encoded) > ACTIVITY_PREVIEW_TOTAL_BYTES:
                entry.item["truncated"] = True
                self.count_drop("preview_total_cap")
                return True
        entry.content += delta
        entry.content_bytes += len(encoded)
        self.preview_bytes += len(encoded)
        # Wait-state fact (P3.4/UI 3.3): the last realtime activity instant.
        entry.item["last_activity_at"] = datetime.now(UTC).isoformat()
        return True

    def _evict_completed_content(self) -> None:
        for entry in self.entries.values():
            if entry.terminal and entry.content_bytes:
                self.preview_bytes -= entry.content_bytes
                entry.content = ""
                entry.content_bytes = 0
                entry.item["availability"] = "evicted"

    def sweep_expired(self) -> None:
        """Drop completed items' transient content after the content TTL."""
        now = time.monotonic()
        for entry in self.entries.values():
            if (
                entry.terminal
                and entry.content_bytes
                and entry.completed_monotonic is not None
                and now - entry.completed_monotonic > ACTIVITY_COMPLETED_CONTENT_TTL_SECONDS
            ):
                self.preview_bytes -= entry.content_bytes
                entry.content = ""
                entry.content_bytes = 0
                entry.item["availability"] = "evicted"

    def snapshot_items(self) -> list[dict]:
        return [entry.item for entry in self.entries.values()]


# Host-wide budget accounting: every live ActivityStreamState registers here so
# the 32 MiB cap counts multi-workspace roots, leaves, rings and backlogs.
_HOST_STATES: weakref.WeakSet | None = None


def _host_states() -> weakref.WeakSet:
    global _HOST_STATES
    if _HOST_STATES is None:
        _HOST_STATES = weakref.WeakSet()
    return _HOST_STATES


def register_host_state(state: ActivityStreamState) -> None:
    _host_states().add(state)


def host_activity_bytes() -> int:
    return sum(state.ring_bytes + state.preview_bytes for state in _host_states())


def enforce_host_budget() -> None:
    """Prefer dropping completed previews host-wide, then oldest metadata."""
    if host_activity_bytes() <= ACTIVITY_HOST_TOTAL_BYTES:
        return
    states = sorted(_host_states(), key=lambda state: state.sequence)
    for state in states:
        state._evict_completed_content()
        if host_activity_bytes() <= ACTIVITY_HOST_TOTAL_BYTES:
            return
    for state in states:
        while state.entries and host_activity_bytes() > ACTIVITY_HOST_TOTAL_BYTES:
            _, evicted = state.entries.popitem(last=False)
            state.preview_bytes -= evicted.content_bytes
            state.count_drop("host_budget_evicted")
