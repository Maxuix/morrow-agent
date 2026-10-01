"""Transient native identities for local pickers; never a model target registry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from morrow.adapters.computer_use.process_identity import ProcessBirth
from morrow.core.computer_use import (
    MAX_OBSERVATION_AGE_SECONDS,
    ComputerUseAppIdentity,
    ComputerUseContractError,
    LocalComputerUseCandidate,
    LocalComputerUseCandidates,
)
from morrow.core.domain import COMPUTER_CANDIDATE_ID_PREFIX


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
        self._expires_at = None

    def __repr__(self):
        return f"LocalCandidateRegistry(count={len(self._windows)})"

    def clear(self):
        self._windows.clear()
        self._expires_at = None

    def publish(self, windows):
        self.clear()
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
            self.clear()
            raise ComputerUseContractError("stale_observation")
        identity = self._windows.get(candidate_id)
        if identity is None:
            raise ComputerUseContractError("unknown_target")
        return identity
