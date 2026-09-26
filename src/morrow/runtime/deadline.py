"""Optional monotonic run budget supplied by a host such as Harbor."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass


class RunDeadlineExceeded(TimeoutError):
    """No new work may be admitted before the host's terminal budget."""


@dataclass(frozen=True)
class RunDeadline:
    expires_at: float
    work_until: float
    clock: Callable[[], float] = time.monotonic

    @classmethod
    def from_seconds(
        cls, seconds: float, *, clock: Callable[[], float] = time.monotonic
    ) -> RunDeadline:
        if isinstance(seconds, bool) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("run timeout must be a positive finite number")
        now = clock()
        # Reserve time for terminal persistence and host log export. For short
        # synthetic runs, the reserve cannot consume the whole useful window.
        reserve = min(30.0, seconds * 0.1)
        return cls(expires_at=now + seconds, work_until=now + seconds - reserve, clock=clock)

    def remaining_seconds(self) -> float:
        return max(0.0, self.work_until - self.clock())

    def require_work(self) -> float:
        remaining = self.remaining_seconds()
        if remaining <= 0:
            raise RunDeadlineExceeded("任务运行时间已用尽，正在保存已有结果")
        return remaining
