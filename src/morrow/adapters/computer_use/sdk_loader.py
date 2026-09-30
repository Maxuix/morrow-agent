"""Load the optional desktop SDK by module name and construct a driver only on request."""

from __future__ import annotations

import importlib
import os
import platform
import sys
from collections.abc import Callable
from typing import Any

from morrow.adapters.computer_use.diagnostics import PINNED_SDK_VERSION, HostProbe

SDK_MODULE_NAME = "cua_driver"


def load_sdk() -> Any:
    """Import the optional SDK. Callers that only preflight must not use this."""

    return importlib.import_module(SDK_MODULE_NAME)


def construct_driver(sdk: Any | None = None) -> Any:
    """Create the same-process runtime. This is the only construction path."""

    import morrow.adapters.computer_use as package

    package.DRIVER_CONSTRUCTION_COUNT += 1
    module = load_sdk() if sdk is None else sdk
    options = module.DriverOptions(claude_code_compatibility=False)
    return module.CuaDriver.create(options)


def collect_host_probe(
    *,
    spec_present: bool | None = None,
    loader: Callable[[], Any] | None = None,
    driver_activated: bool = False,
    system: str | None = None,
    os_version: tuple[int, int, int] | None = None,
    interactive: bool | None = None,
) -> HostProbe:
    """Read package, OS, session, and TCC facts. Does not construct a driver."""

    present = _spec_present() if spec_present is None else spec_present
    resolved_system = current_system() if system is None else system
    resolved_version = current_os_version(resolved_system) if os_version is None else os_version
    resolved_interactive = current_interactive_session() if interactive is None else interactive
    if not present:
        return HostProbe(
            sdk_present=False,
            system=resolved_system,
            os_version=resolved_version,
            interactive_session=resolved_interactive,
            driver_activated=driver_activated,
        )
    module_loader = load_sdk if loader is None else loader
    try:
        module = module_loader()
    except (ImportError, ModuleNotFoundError, OSError):
        return HostProbe(
            sdk_present=True,
            native_load_failed=True,
            system=resolved_system,
            os_version=resolved_version,
            interactive_session=resolved_interactive,
            driver_activated=driver_activated,
        )
    version = getattr(module, "__version__", None)
    accessibility: bool | None = None
    screen_recording: bool | None = None
    if (
        version == PINNED_SDK_VERSION
        and resolved_system == "darwin"
        and resolved_interactive
        and resolved_version is not None
        and resolved_version >= (14, 0, 0)
    ):
        accessibility, screen_recording = _permission_flags(module)
    return HostProbe(
        sdk_present=True,
        sdk_version=version if isinstance(version, str) else None,
        system=resolved_system,
        os_version=resolved_version,
        interactive_session=resolved_interactive,
        accessibility=accessibility,
        screen_recording=screen_recording,
        driver_activated=driver_activated,
    )


def current_system() -> str:
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    return "other"


def current_os_version(system: str) -> tuple[int, int, int] | None:
    if system != "darwin":
        return None
    raw = platform.mac_ver()[0]
    parts: list[int] = []
    for piece in raw.split("."):
        if not piece.isdigit():
            break
        parts.append(int(piece))
    if not parts:
        return None
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def current_interactive_session() -> bool:
    if sys.platform != "darwin":
        return False
    return bool(os.environ.get("SECURITYSESSIONID"))


def _spec_present() -> bool:
    return importlib.util.find_spec(SDK_MODULE_NAME) is not None


def _permission_flags(module: Any) -> tuple[bool | None, bool | None]:
    try:
        status = module.current_mac_os_permission_status()
    except Exception:
        return None, None
    accessibility = getattr(status, "accessibility", None)
    screen_recording = getattr(status, "screen_recording", None)
    if not isinstance(accessibility, bool) or not isinstance(screen_recording, bool):
        return None, None
    return accessibility, screen_recording


def construct_run_session(sdk: Any, driver: Any, name: str, *, lifetime_seconds: int = 600) -> Any:
    """The pinned SDK's immutable session-bound surface, never model options."""
    if not 1 <= lifetime_seconds <= 600:
        from morrow.core.computer_use import ComputerUseContractError

        raise ComputerUseContractError("rejected_action")
    return sdk.create_trusted_session(
        driver,
        sdk.TrustedSessionOptions(
            public_session=name,
            mode=sdk.SessionPermissionMode.STANDARD,
            ttl_seconds=lifetime_seconds,
            idle_ttl_seconds=lifetime_seconds,
            capability_manifest_path=None,
            bounded_manifest_path=None,
        ),
    )
