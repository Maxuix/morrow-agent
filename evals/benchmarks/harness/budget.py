"""Shared token-budget ledger for all Morrow benchmark runs.

One JSON ledger file per campaign. The ledger records every admission
(reservation) and every finalized task usage so a driver can refuse to start
new work once the cumulative 100M-token budget would be exceeded.

An admission holds its reservation. Complete terminal usage replaces that
charge with actual tokens; partial or missing usage retains at least the
reservation and reports known actual tokens separately. File locking serializes
independent drivers. This is an admission ledger, not a request-level hard cap.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class TokenBudget:
    """Process-safe task admission ledger; not a per-request token limit."""

    SCHEMA_VERSION = 1

    def __init__(self, path: Path, *, budget_total: int, reservation: int) -> None:
        self.path = Path(path)
        self.budget_total = int(budget_total)
        self.reservation = int(reservation)
        if self.budget_total <= 0 or self.reservation <= 0:
            raise ValueError("budget_total and reservation must be positive")
        with self._locked() as state:
            if state["budget_total"] != self.budget_total:
                raise ValueError("budget_total differs from existing ledger")
            if state["reservation"] != self.reservation:
                raise ValueError("reservation differs from existing ledger")

    @classmethod
    def preview(cls, path: Path, *, budget_total: int, reservation: int) -> dict[str, Any]:
        """Inspect admission capacity without creating a ledger or lock file."""
        if budget_total <= 0 or reservation <= 0:
            raise ValueError("budget_total and reservation must be positive")
        if path.is_file():
            state = json.loads(path.read_text(encoding="utf-8"))
            if state.get("schema_version") != cls.SCHEMA_VERSION:
                raise ValueError(f"unsupported budget ledger version: {path}")
            if state.get("budget_total") != budget_total:
                raise ValueError("budget_total differs from existing ledger")
            if state.get("reservation") != reservation:
                raise ValueError("reservation differs from existing ledger")
            charged = cls._charged(state)
            return {
                "budget_total": budget_total,
                "used_tokens": charged,
                "remaining": budget_total - charged,
                "read_only_snapshot": True,
            }
        return {
            "budget_total": budget_total,
            "used_tokens": 0,
            "remaining": budget_total,
            "read_only_snapshot": True,
        }

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(self.path.suffix + ".lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                if self.path.exists():
                    state = json.loads(self.path.read_text(encoding="utf-8"))
                    if state.get("schema_version") != self.SCHEMA_VERSION:
                        raise ValueError(f"unsupported budget ledger version: {self.path}")
                else:
                    state = {
                        "schema_version": self.SCHEMA_VERSION,
                        "budget_total": self.budget_total,
                        "reservation": self.reservation,
                        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "entries": [],
                    }
                    self._save(state)
                yield state
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _save(self, state: dict[str, Any]) -> None:
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            with tmp.open("rb") as handle:
                os.fsync(handle.fileno())
            tmp.replace(self.path)
        finally:
            tmp.unlink(missing_ok=True)

    @property
    def state(self) -> dict[str, Any]:
        with self._locked() as state:
            return json.loads(json.dumps(state))

    @staticmethod
    def _charged(state: dict[str, Any]) -> int:
        return sum(int(e.get("charged_tokens", 0)) for e in state["entries"])

    @staticmethod
    def _entry_complete(entry: dict[str, Any]) -> bool:
        return bool(
            entry.get(
                "usage_complete", (entry.get("usage") or {}).get("availability") == "available"
            )
        )

    @property
    def used_tokens(self) -> int:
        with self._locked() as state:
            return self._charged(state)

    @property
    def remaining(self) -> int:
        with self._locked() as state:
            return self.budget_total - self._charged(state)

    def admit(self, run_key: str, *, reservation: int | None = None) -> bool:
        """Reserve budget for one task. Returns False when the cap forbids it.

        Idempotent per ``run_key``: a task already admitted or finalized (e.g.
        resuming after an interruption) is not charged twice.
        """
        res = int(reservation if reservation is not None else self.reservation)
        if res <= 0:
            raise ValueError("reservation must be positive")
        with self._locked() as state:
            for entry in state["entries"]:
                if entry["run_key"] == run_key and entry["status"] in ("admitted", "finalized"):
                    return True
            if self._charged(state) + res > self.budget_total:
                state["entries"].append(
                    {
                        "run_key": run_key,
                        "status": "refused",
                        "reservation": res,
                        "charged_tokens": 0,
                        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    }
                )
                self._save(state)
                return False
            state["entries"].append(
                {
                    "run_key": run_key,
                    "status": "admitted",
                    "reservation": res,
                    "charged_tokens": res,
                    "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }
            )
            self._save(state)
            return True

    def admit_many(self, run_keys: list[str]) -> bool:
        """Admit an entire job under one lock, or admit none of it."""
        if len(run_keys) != len(set(run_keys)):
            raise ValueError("duplicate run keys")
        with self._locked() as state:
            existing = {
                entry["run_key"]
                for entry in state["entries"]
                if entry["status"] in ("admitted", "finalized")
            }
            new_keys = [key for key in run_keys if key not in existing]
            if self._charged(state) + len(new_keys) * self.reservation > self.budget_total:
                return False
            for key in new_keys:
                state["entries"].append(
                    {
                        "run_key": key,
                        "status": "admitted",
                        "reservation": self.reservation,
                        "charged_tokens": self.reservation,
                        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    }
                )
            if new_keys:
                self._save(state)
            return True

    def finalize(self, run_key: str, *, usage: dict[str, Any] | None) -> None:
        """Correct the admission charge with the exact usage of a finished task.

        ``usage`` is the Morrow terminal metrics usage object; unknown/unavailable
        keeps the reservation (conservative, never silently zero).
        """
        known = self._known_tokens(usage)
        complete = bool(usage and usage.get("availability") == "available")
        with self._locked() as state:
            for entry in reversed(state["entries"]):
                if entry["run_key"] == run_key and entry["status"] in ("admitted", "finalized"):
                    if entry["status"] == "finalized" and self._entry_complete(entry):
                        return
                    if not complete:
                        known = max(known, int(entry.get("known_actual_tokens", 0)))
                    entry["status"] = "finalized"
                    entry["charged_tokens"] = (
                        known if complete else max(entry["reservation"], known)
                    )
                    entry["known_actual_tokens"] = known
                    entry["usage_complete"] = complete
                    entry["usage"] = usage
                    self._save(state)
                    return
        raise KeyError(f"no admitted or incomplete entry for run_key={run_key}")

    @staticmethod
    def _known_tokens(usage: dict[str, Any] | None) -> int:
        if not usage:
            return 0
        try:
            total = usage.get("total_tokens")
            if total is not None:
                return max(0, int(total))
            return max(0, int(usage.get("input_tokens") or 0)) + max(
                0, int(usage.get("output_tokens") or 0)
            )
        except (TypeError, ValueError):
            return 0

    def summary(self) -> dict[str, Any]:
        with self._locked() as state:
            entries = state["entries"]
            charged = self._charged(state)
            return {
                "budget_total": self.budget_total,
                "used_tokens": charged,
                "remaining": self.budget_total - charged,
                "known_actual_tokens": sum(
                    int(e.get("known_actual_tokens", self._known_tokens(e.get("usage"))))
                    for e in entries
                ),
                "unknown_exposure_count": sum(
                    e["status"] != "refused" and not self._entry_complete(e) for e in entries
                ),
                "admitted": sum(e["status"] != "refused" for e in entries),
                "refused": sum(e["status"] == "refused" for e in entries),
            }
