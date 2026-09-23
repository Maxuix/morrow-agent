"""Shared token-budget ledger for all Morrow benchmark runs.

One JSON ledger file per campaign. The ledger records every admission
(reservation) and every finalized task usage so a driver can refuse to start
new work once the cumulative 100M-token budget would be exceeded.

Accounting rule (conservative, like evals/code-agent-mini campaign-capacity):
an admission first consumes ``max(reservation, known_usage)``; after the task
finalizes, the ledger is corrected with the exact ``total_tokens`` from the
Morrow ``run.completed`` record. Refused tasks are recorded as ``refused`` so
reports can distinguish "budget exhausted" from "failed".
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class TokenBudget:
    """Create-only-ish JSON ledger with a hard cumulative cap."""

    SCHEMA_VERSION = 1

    def __init__(self, path: Path, *, budget_total: int, reservation: int) -> None:
        self.path = Path(path)
        self.budget_total = int(budget_total)
        self.reservation = int(reservation)
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
            if self.state.get("schema_version") != self.SCHEMA_VERSION:
                raise ValueError(f"unsupported budget ledger version: {self.path}")
        else:
            self.state = {
                "schema_version": self.SCHEMA_VERSION,
                "budget_total": self.budget_total,
                "reservation": self.reservation,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "entries": [],
            }
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    @property
    def used_tokens(self) -> int:
        return sum(int(e.get("charged_tokens", 0)) for e in self.state["entries"])

    @property
    def remaining(self) -> int:
        return self.budget_total - self.used_tokens

    def admit(self, run_key: str, *, reservation: int | None = None) -> bool:
        """Reserve budget for one task. Returns False when the cap forbids it.

        Idempotent per ``run_key``: a task already admitted or finalized (e.g.
        resuming after an interruption) is not charged twice.
        """
        for entry in self.state["entries"]:
            if entry["run_key"] == run_key and entry["status"] in ("admitted", "finalized"):
                return True
        res = int(reservation if reservation is not None else self.reservation)
        if self.used_tokens + res > self.budget_total:
            self.state["entries"].append(
                {
                    "run_key": run_key,
                    "status": "refused",
                    "reservation": res,
                    "charged_tokens": 0,
                    "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }
            )
            self._save()
            return False
        self.state["entries"].append(
            {
                "run_key": run_key,
                "status": "admitted",
                "reservation": res,
                "charged_tokens": res,
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
        )
        self._save()
        return True

    def finalize(self, run_key: str, *, usage: dict[str, Any] | None) -> None:
        """Correct the admission charge with the exact usage of a finished task.

        ``usage`` is the Morrow terminal metrics usage object; unknown/unavailable
        keeps the reservation (conservative, never silently zero).
        """
        known = self._known_tokens(usage)
        for entry in reversed(self.state["entries"]):
            if entry["run_key"] == run_key and entry["status"] == "admitted":
                entry["status"] = "finalized"
                entry["charged_tokens"] = max(int(entry["reservation"]), known)
                entry["usage"] = usage
                self._save()
                return
        raise KeyError(f"no admitted entry for run_key={run_key}")

    @staticmethod
    def _known_tokens(usage: dict[str, Any] | None) -> int:
        if not usage:
            return 0
        try:
            if usage.get("availability") != "available":
                return 0
            total = usage.get("total_tokens")
            if total is not None:
                return int(total)
            return int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
        except (TypeError, ValueError):
            return 0

    def summary(self) -> dict[str, Any]:
        return {
            "budget_total": self.budget_total,
            "used_tokens": self.used_tokens,
            "remaining": self.remaining,
            "admitted": sum(1 for e in self.state["entries"] if e["status"] != "refused"),
            "refused": sum(1 for e in self.state["entries"] if e["status"] == "refused"),
        }
