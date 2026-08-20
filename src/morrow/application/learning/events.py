"""Sanitized application events emitted by the Learning pipeline."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from morrow.core.application import ApplicationEvent
from morrow.core.ports import IdSource


def _utc(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class LearningEventWriter:
    """Create only bounded Learning events inside an existing journal transaction."""

    def __init__(
        self,
        *,
        workspace_id: str,
        id_source: IdSource,
        clock: Callable[[], datetime],
    ) -> None:
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.clock = clock

    def put(
        self,
        txn,
        *,
        event_type: str,
        aggregate_kind: str,
        aggregate_id: str,
        payload: dict[str, object],
    ) -> ApplicationEvent:
        return txn.put_application_event_in_txn(
            self.workspace_id,
            ApplicationEvent(
                event_id=self.id_source.new_id("evt"),
                workspace_id=self.workspace_id,
                event_type=event_type,
                aggregate_kind=aggregate_kind,
                aggregate_id=aggregate_id,
                payload=payload,
                created_at=_utc(self.clock),
            ),
        )


__all__ = ["LearningEventWriter"]
