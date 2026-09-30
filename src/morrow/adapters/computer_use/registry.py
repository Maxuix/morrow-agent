"""Private map from opaque desktop ids to native process, window, and element facts."""

from __future__ import annotations

from dataclasses import dataclass

from morrow.adapters.computer_use.process_identity import ProcessBirth
from morrow.core.computer_use import (
    ComputerUseAppIdentity,
    ComputerUseContractError,
    TargetRef,
)
from morrow.core.domain import (
    COMPUTER_ELEMENT_ID_PREFIX,
    COMPUTER_PROCESS_ID_PREFIX,
    COMPUTER_TARGET_ID_PREFIX,
    COMPUTER_WINDOW_ID_PREFIX,
)
from morrow.core.ports import IdSource


@dataclass(slots=True)
class _ProcessRecord:
    process_identity: str
    pid: int
    bundle_id: str
    birth: ProcessBirth

    def __repr__(self) -> str:
        return f"_ProcessRecord(process_identity={self.process_identity!r})"


@dataclass(slots=True)
class _WindowRecord:
    target_ref: str
    process_identity: str
    window_identity: str
    pid: int
    window_id: int
    bundle_id: str
    agent_run_id: str
    generation: int
    process_birth: ProcessBirth

    def __repr__(self) -> str:
        return f"_WindowRecord(window_identity={self.window_identity!r})"


@dataclass(slots=True)
class _ElementRecord:
    element_ref: str
    token: str | None
    window_identity: str
    center: tuple[float, float] | None

    def __repr__(self) -> str:
        return f"_ElementRecord(element_ref={self.element_ref!r})"


class TrustedDesktopRegistry:
    """Native identifiers stay here. ``repr`` exposes only counts."""

    def __init__(self, id_source: IdSource) -> None:
        self._ids = id_source
        self._processes: dict[tuple[str, int, str, int, ProcessBirth], _ProcessRecord] = {}
        self._windows: dict[tuple[str, int, str, int, int, ProcessBirth], _WindowRecord] = {}
        self._windows_by_identity: dict[str, _WindowRecord] = {}
        self._elements: dict[str, _ElementRecord] = {}
        self._snapshots: dict[str, str] = {}

    def __repr__(self) -> str:
        return (
            f"TrustedDesktopRegistry(processes={len(self._processes)}, "
            f"windows={len(self._windows)}, elements={len(self._elements)})"
        )

    def clear(self) -> None:
        """Discard live identities and tokens at the run lifecycle boundary."""
        self._processes.clear()
        self._windows.clear()
        self._windows_by_identity.clear()
        self._elements.clear()
        self._snapshots.clear()

    def remember_window(
        self,
        *,
        agent_run_id: str,
        generation: int,
        bundle_id: str,
        pid: int,
        window_id: int,
        display_label: str | None,
        process_birth: ProcessBirth,
    ) -> TargetRef:
        process = self._process(agent_run_id, generation, bundle_id, pid, process_birth)
        key = (agent_run_id, generation, bundle_id, pid, window_id, process_birth)
        existing = self._windows.get(key)
        if existing is None:
            existing = _WindowRecord(
                target_ref=self._ids.new_id(COMPUTER_TARGET_ID_PREFIX),
                process_identity=process.process_identity,
                window_identity=self._ids.new_id(COMPUTER_WINDOW_ID_PREFIX),
                pid=pid,
                window_id=window_id,
                bundle_id=bundle_id,
                agent_run_id=agent_run_id,
                generation=generation,
                process_birth=process_birth,
            )
            self._windows[key] = existing
            self._windows_by_identity[existing.window_identity] = existing
        return TargetRef(
            target_ref=existing.target_ref,
            agent_run_id=agent_run_id,
            generation=generation,
            app=ComputerUseAppIdentity(bundle_id=bundle_id),
            process_identity=existing.process_identity,
            window_identity=existing.window_identity,
            display_label=display_label,
        )

    def remember_element(
        self,
        *,
        window_identity: str,
        token: str | None,
        center: tuple[float, float] | None,
    ) -> str:
        element_ref = self._ids.new_id(COMPUTER_ELEMENT_ID_PREFIX)
        self._elements[element_ref] = _ElementRecord(
            element_ref=element_ref,
            token=token,
            window_identity=window_identity,
            center=center,
        )
        return element_ref

    def remember_snapshot(self, observation_id: str, snapshot_id: str | None) -> None:
        if snapshot_id:
            self._snapshots[observation_id] = snapshot_id

    def window(self, window_identity: str) -> _WindowRecord:
        found = self._windows_by_identity.get(window_identity)
        if found is None:
            raise ComputerUseContractError("unknown_target")
        return found

    def element(self, element_ref: str) -> _ElementRecord:
        found = self._elements.get(element_ref)
        if found is None:
            raise ComputerUseContractError("unknown_element")
        return found

    def _process(
        self, agent_run_id: str, generation: int, bundle_id: str, pid: int, birth: ProcessBirth
    ) -> _ProcessRecord:
        key = (agent_run_id, generation, bundle_id, pid, birth)
        found = self._processes.get(key)
        if found is None:
            found = _ProcessRecord(
                process_identity=self._ids.new_id(COMPUTER_PROCESS_ID_PREFIX),
                pid=pid,
                bundle_id=bundle_id,
                birth=birth,
            )
            self._processes[key] = found
        return found
