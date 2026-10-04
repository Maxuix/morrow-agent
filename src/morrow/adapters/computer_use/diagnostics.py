"""Host facts for desktop preflight. This module does not load a native driver."""

from __future__ import annotations

from dataclasses import dataclass

from morrow.adapters.computer_use.action_inputs import FUNCTIONAL_SDK_VERSION
from morrow.core.computer_use import ComputerUsePreflight
from morrow.core.runtime_policy import ComputerUseSettings

OFFICIAL_SDK_VERSION = "0.30.4"
DIAGNOSTIC_SDK_VERSIONS = (OFFICIAL_SDK_VERSION, FUNCTIONAL_SDK_VERSION)
MINIMUM_MACOS = (14, 0, 0)


@dataclass(frozen=True, slots=True)
class HostProbe:
    """Facts collected before any driver exists. Permission flags may be unknown."""

    sdk_present: bool = False
    sdk_version: str | None = None
    native_load_failed: bool = False
    system: str = "other"
    os_version: tuple[int, int, int] | None = None
    interactive_session: bool = False
    accessibility: bool | None = None
    screen_recording: bool | None = None
    driver_activated: bool = False


def diagnose_host(
    settings: ComputerUseSettings | None,
    probe: HostProbe,
    *,
    images_required: bool = False,
) -> ComputerUsePreflight:
    """Classify a host probe. Every result stays unavailable."""

    resolved = settings or ComputerUseSettings()
    if not resolved.enabled:
        return ComputerUsePreflight(status="unavailable", reason="disabled")
    if not probe.sdk_present:
        return ComputerUsePreflight(status="unavailable", reason="sdk_missing")
    if probe.native_load_failed:
        return ComputerUsePreflight(status="unavailable", reason="abi_mismatch")
    if probe.sdk_version not in DIAGNOSTIC_SDK_VERSIONS:
        return ComputerUsePreflight(status="unavailable", reason="native_version_mismatch")
    if not _supported_macos(probe):
        return ComputerUsePreflight(status="unavailable", reason="unsupported_os")
    if not probe.interactive_session:
        return ComputerUsePreflight(status="unavailable", reason="no_interactive_session")
    if probe.accessibility is not True:
        return ComputerUsePreflight(status="unavailable", reason="tcc_missing")
    if images_required and probe.screen_recording is not True:
        return ComputerUsePreflight(status="unavailable", reason="tcc_missing")
    if probe.driver_activated:
        return ComputerUsePreflight(status="unavailable", reason="native_unverified")
    return ComputerUsePreflight(status="unavailable", reason="driver_not_activated")


def _supported_macos(probe: HostProbe) -> bool:
    if probe.system != "darwin" or probe.os_version is None:
        return False
    return probe.os_version >= MINIMUM_MACOS
