"""Shared strict window reads. Candidate scans and run discovery each read again."""

from __future__ import annotations

import math
from typing import Any

from morrow.adapters.computer_use.registry import WindowGeometry
from morrow.core.computer_use import ComputerUseContractError
from morrow.core.domain import refuse_secret_material


def running_app(app: object) -> bool:
    return getattr(app, "running", False) is True


def valid_pid(pid: object) -> bool:
    return isinstance(pid, int) and not isinstance(pid, bool) and 0 < pid <= 2**31 - 1


def valid_window_id(window_id: object) -> bool:
    return (
        isinstance(window_id, int)
        and not isinstance(window_id, bool)
        and 0 < window_id <= 2**32 - 1
    )


def strict_window(window: object, pid: int) -> bool:
    """A listed window may be skipped. A live confirm uses the same predicate."""

    owner_pid = getattr(window, "pid", None)
    if not valid_window_id(getattr(window, "window_id", None)):
        return False
    if isinstance(owner_pid, bool) or owner_pid != pid:
        return False
    if getattr(window, "minimized", False) is True:
        return False
    if getattr(window, "is_on_screen", True) is False:
        return False
    return True


def bounds_geometry(bounds: Any) -> WindowGeometry:
    values = tuple(getattr(bounds, name, None) for name in ("x", "y", "width", "height"))
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or abs(value) > 2**31 - 1
        for value in values
    ):
        raise ComputerUseContractError("unknown_scale")
    if values[2] <= 0 or values[3] <= 0:
        raise ComputerUseContractError("unknown_scale")
    return WindowGeometry(*values)


def window_geometry(window: Any) -> WindowGeometry:
    return bounds_geometry(getattr(window, "bounds", None))


def display_label(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > 120:
        return None
    try:
        refuse_secret_material(cleaned, label="computer use label")
    except ValueError:
        return None
    return cleaned
