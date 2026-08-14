"""Deterministic test doubles also useful for local demos."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime

from morrow.core.models import FinishReason, Message, ModelErrorCode, ModelEvent, ModelRef


class FixedClock:
    def __init__(self, value: datetime | None = None) -> None:
        self.value = value or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class FixedIdSource:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def new_id(self, prefix: str) -> str:
        self.counts[prefix] = self.counts.get(prefix, 0) + 1
        return f"{prefix}_{self.counts[prefix]}"


class ScriptedModelProvider:
    def __init__(self, responses: Iterable[object] = ()) -> None:
        self.responses = list(responses)
        self.stream_calls: list[list[Message]] = []
        self.complete_calls: list[list[Message]] = []
        self._index = 0

    def _next(self) -> object:
        if not self.responses:
            return "好的。"
        item = self.responses[min(self._index, len(self.responses) - 1)]
        self._index += 1
        return item

    async def stream(self, model: ModelRef, messages: list[Message]) -> AsyncIterator[ModelEvent]:
        del model
        self.stream_calls.append(list(messages))
        response = self._next()
        if isinstance(response, BaseException):
            yield ModelEvent(
                kind="error", error_code=ModelErrorCode.NETWORK, error_message="scripted failure"
            )
            return
        if response == "cancel":
            await asyncio.sleep(10)
            return
        if isinstance(response, (list, tuple)):
            chunks = response
        else:
            chunks = [str(response)]
        for chunk in chunks:
            await asyncio.sleep(0)
            yield ModelEvent(kind="text_delta", text=str(chunk))
        yield ModelEvent(kind="completed", finish_reason=FinishReason.STOP)

    async def complete(self, model: ModelRef, messages: list[Message]) -> str:
        del model
        self.complete_calls.append(list(messages))
        response = self._next()
        if isinstance(response, BaseException):
            raise response
        if response == "cancel":
            await asyncio.sleep(10)
        return str(response)
