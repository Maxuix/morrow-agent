"""Explicit service bundle owned by the Core Host's runtime thread."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ServerContext:
    """Everything API handlers reach, always on the Core loop."""

    workspace_id: str
    application: Any
    journal: Any
    api: Any
    management: Any
    runtime: Any
    emitter: Any
    hub: Any
    supervisor: Any
    approval_waiters: Any
    skill_queries: Any = None
    tool_catalog: tuple[dict, ...] = ()
    products: Any = None
    context_management: Any = None
    close: Callable[[], None] = lambda: None
