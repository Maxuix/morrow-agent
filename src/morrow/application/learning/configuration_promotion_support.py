"""Small shared helpers for configuration promotion mixins."""

from __future__ import annotations

from datetime import UTC, datetime


def promotion_now(context) -> datetime:
    value = context.clock()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class NeedsResolution:
    """Internal transaction return value used to preserve a typed failure."""

    def __init__(self, error) -> None:
        self.error = error
