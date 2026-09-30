"""Optional desktop driver adapter.

Importing this package does not import or construct a native driver.
"""

from __future__ import annotations

import importlib.util

from morrow.core.computer_use import ComputerUsePreflight, preflight_computer_use
from morrow.core.runtime_policy import ComputerUseSettings

DRIVER_CONSTRUCTION_COUNT = 0


def sdk_spec_present() -> bool:
    return importlib.util.find_spec("cua_driver") is not None


def preflight(settings: ComputerUseSettings | None = None) -> ComputerUsePreflight:
    resolved = settings or ComputerUseSettings()
    present = sdk_spec_present() if resolved.enabled else False
    return preflight_computer_use(resolved, spec_present=present)
