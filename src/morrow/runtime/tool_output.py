"""Task-scoped tool output listener seam (master plan P4.3).

The tool cycle publishes realtime stdout/stderr fragments to whoever observes
the current execution without threading UI objects through every handler
signature. The listener is a bounded, best-effort sink: it never blocks the
drain loop and never owns durable state.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar

_listener: ContextVar[Callable[[str, str], None] | None] = ContextVar(
    "tool_output_listener", default=None
)


def current_output_listener() -> Callable[[str, str], None] | None:
    return _listener.get()


@contextmanager
def tool_output_scope(listener: Callable[[str, str], None] | None):
    token = _listener.set(listener)
    try:
        yield
    finally:
        _listener.reset(token)
