"""Transient native identities for local pickers; never a model target registry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from morrow.adapters.computer_use.process_identity import ProcessBirth
from morrow.core.computer_use import (
    MAX_DISCOVERED_TARGETS,
    MAX_OBSERVATION_AGE_SECONDS,
    ComputerUseAppIdentity,
    ComputerUseContractError,
    ComputerUseWindowIdentity,
    LocalComputerUseCandidate,
    LocalComputerUseCandidates,
)
from morrow.core.domain import COMPUTER_CANDIDATE_ID_PREFIX, COMPUTER_WINDOW_ID_PREFIX


@dataclass(frozen=True, slots=True, repr=False)
class LocalWindowIdentity:
    bundle_id: str
    pid: int
    process_birth: ProcessBirth
    window_id: int

    def __repr__(self):
        return "LocalWindowIdentity()"


class LocalCandidateRegistry:
    def __init__(self, ids, clock):
        self._ids, self._clock = ids, clock
        self._windows = {}
        self._selected = {}
        self._expires_at = None

    def __repr__(self):
        return f"LocalCandidateRegistry(count={len(self._windows)})"

    def clear(self):
        """Revoke both picker entries and confirmed, unconsumed bindings."""
        self.clear_candidates()
        self._selected.clear()

    def clear_candidates(self):
        """Refresh the picker without revoking a previously confirmed selection."""
        self._windows.clear()
        self._expires_at = None

    def publish(self, windows):
        self.clear_candidates()
        self._expires_at = self._clock.now() + timedelta(seconds=MAX_OBSERVATION_AGE_SECONDS)
        candidates = []
        for identity, label in windows:
            candidate_id = self._ids.new_id(COMPUTER_CANDIDATE_ID_PREFIX)
            self._windows[candidate_id] = identity
            candidates.append(
                LocalComputerUseCandidate(
                    candidate_id=candidate_id,
                    app=ComputerUseAppIdentity(bundle_id=identity.bundle_id),
                    display_label=label,
                )
            )
        return LocalComputerUseCandidates(candidates=tuple(candidates), expires_at=self._expires_at)

    def resolve(self, candidate_id):
        if self._expires_at is None or self._clock.now() >= self._expires_at:
            # Expired picker entries cannot revoke a previously confirmed selection.
            # Bindings are consumed once and revalidated against process birth at run entry.
            self.clear_candidates()
            raise ComputerUseContractError("stale_observation")
        identity = self._windows.get(candidate_id)
        if identity is None:
            raise ComputerUseContractError("unknown_target")
        return identity

    def select(self, candidate_ids):
        if (
            not isinstance(candidate_ids, tuple)
            or not 1 <= len(candidate_ids) <= MAX_DISCOVERED_TARGETS
            or any(not isinstance(item, str) for item in candidate_ids)
            or len(set(candidate_ids)) != len(candidate_ids)
        ):
            raise ComputerUseContractError("rejected_action")
        identities = [self.resolve(candidate_id) for candidate_id in candidate_ids]
        if len(self._selected) + len(identities) > MAX_DISCOVERED_TARGETS:
            raise ComputerUseContractError("target_budget")
        selected = []
        for identity in identities:
            window_id = self._ids.new_id(COMPUTER_WINDOW_ID_PREFIX)
            self._selected[window_id] = identity
            selected.append(
                ComputerUseWindowIdentity(
                    app=ComputerUseAppIdentity(bundle_id=identity.bundle_id),
                    window_identity=window_id,
                )
            )
        return tuple(sorted(selected, key=lambda item: item.window_identity))

    def take_bindings(self, windows, process_reader):
        bindings = {}
        for window in windows:
            identity = self._selected.get(window.window_identity)
            if identity is None or window.app.bundle_id != identity.bundle_id:
                raise ComputerUseContractError("unknown_target")
            if process_reader(identity.pid) != identity.process_birth:
                raise ComputerUseContractError("stale_observation")
            bindings[window.window_identity] = identity
        for window_id in bindings:
            self._selected.pop(window_id)
        return bindings

    def discard_bindings(self, windows):
        """Release only the selections explicitly abandoned by the local picker."""
        for window in windows:
            identity = self._selected.get(window.window_identity)
            if identity is not None and window.app.bundle_id == identity.bundle_id:
                self._selected.pop(window.window_identity)
