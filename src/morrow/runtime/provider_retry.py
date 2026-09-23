"""Bounded provider retry delay shared by model calls and compaction summaries."""

from __future__ import annotations

import math
from dataclasses import dataclass

from morrow.core.runtime_policy import PROVIDER_RETRY_WAIT_BUDGET_SECONDS


def bounded_retry_after(value: float | None) -> float | None:
    """Keep a provider Retry-After numeric, finite, and inside the delay ceiling."""

    if value is None or isinstance(value, bool):
        return None
    try:
        if not math.isfinite(value) or value < 0:
            return None
        return min(float(value), 60.0)
    except (TypeError, ValueError):
        return None


def retry_unit(value: float) -> float:
    """Clamp one jitter sample to the closed unit interval."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    if not math.isfinite(value):
        return 0.0
    return min(1.0, max(0.0, float(value)))


def next_provider_retry_delay(
    *,
    policy,
    retry_index: int,
    retry_after_seconds: float | None,
    waited_seconds: float,
    unit: float,
) -> float | None:
    """Return the next sleep, or None when the attempt or wait budget is spent.

    ``retry_index`` is 1-based. Equal jitter keeps the exponential component in
    ``[0.5, 1]`` times its base. A numeric Retry-After is a floor until the
    per-attempt cap. A wait that would exceed the remaining cumulative budget
    is refused instead of being shortened below the provider hint.
    """

    if isinstance(retry_index, bool) or retry_index < 1 or retry_index > policy.max_retries:
        return None
    remaining = PROVIDER_RETRY_WAIT_BUDGET_SECONDS - waited_seconds
    if remaining <= 0:
        return None
    exponential = policy.retry_base_delay_seconds * (2 ** (retry_index - 1))
    jittered = exponential * (0.5 + 0.5 * retry_unit(unit))
    directed = bounded_retry_after(retry_after_seconds) or 0.0
    if directed > policy.max_provider_retry_delay_seconds:
        directed = policy.max_provider_retry_delay_seconds
    delay = min(max(jittered, directed), policy.max_provider_retry_delay_seconds)
    if delay > remaining:
        return None
    return delay


@dataclass
class RetryWait:
    """Cumulative provider-retry sleep for one run or one idle compaction."""

    seconds: float = 0.0


__all__ = [
    "RetryWait",
    "bounded_retry_after",
    "next_provider_retry_delay",
    "retry_unit",
]
