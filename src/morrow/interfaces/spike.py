"""Small, reusable terminal integration pattern proven by offline tests.

Input and generation are separate tasks.  A first cancellation request only
cancels the producer task; EOF is converted into one explicit ``exit`` result.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class StreamResult:
    text: str
    cancelled: bool = False


async def consume_stream(
    producer: Awaitable,
    render: Callable[[str], None],
    *,
    cancel_event: asyncio.Event | None = None,
) -> StreamResult:
    generation = asyncio.create_task(producer)
    if cancel_event is None:
        return StreamResult(text=await generation)
    cancellation = asyncio.create_task(cancel_event.wait())
    done, _ = await asyncio.wait({generation, cancellation}, return_when=asyncio.FIRST_COMPLETED)
    if cancellation in done and not generation.done():
        generation.cancel()
        await asyncio.gather(generation, return_exceptions=True)
        cancellation.cancel()
        return StreamResult(text="", cancelled=True)
    cancellation.cancel()
    text = await generation
    render(text)
    return StreamResult(text=text)


def eof_to_action(value: str | None) -> str:
    return "exit" if value is None else "input"
