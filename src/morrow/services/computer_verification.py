"""Bounded predicates over public observations, without SDK identity guesses."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

from morrow.core.computer_use import (
    ComputerUseContractError,
    Observation,
    ObservedWindow,
    Postcondition,
)
from morrow.core.ports import Clock

VerificationState = Literal["passed", "failed", "pending", "not_checked"]
_Checked = Literal["passed", "failed", "not_checked"]


def evaluate_postcondition(observation: Observation, predicate: Postcondition) -> VerificationState:
    if observation.degraded:
        return "not_checked"
    exhaustive = (
        observation.complete and not observation.truncated and observation.omitted_count == 0
    )
    elements = observation.elements
    if predicate.type == "text_appears":
        if any(
            predicate.text in (text or "")
            for element in elements
            for text in (element.label, element.value, element.value_description)
        ):
            return "passed"
        return "failed" if exhaustive else "pending"
    if predicate.selector is None:
        # The old element_ref identifies the consumed snapshot. A fresh node with
        # a matching role/index/label is not proof of that exact native identity.
        return "not_checked"
    selector = predicate.selector
    matches = tuple(
        element
        for element in elements
        if (selector.role is None or element.role == selector.role)
        and (selector.label is None or element.label == selector.label)
    )
    if predicate.type == "element_exists":
        if matches:
            return "passed"
        return "failed" if exhaustive else "pending"
    # A property assertion needs one proven match in an exhaustive tree. Local
    # uniqueness in a truncated/partial tree does not prove window uniqueness.
    if not exhaustive:
        return "not_checked"
    if not matches:
        return "failed"
    if len(matches) != 1:
        return "not_checked"
    actual = getattr(matches[0], predicate.attribute)
    if actual is None:
        return "not_checked"
    return "passed" if actual == (predicate.value == "true") else "failed"


async def collect_verified_observation(
    *,
    observe: Callable[[], Awaitable[ObservedWindow]],
    clock: Clock,
    wait: Callable[[float], Awaitable[None]],
    authority: Callable[[], None],
    predicate: Postcondition | None,
    before_observation_id: str,
    stop: Callable[[], None],
) -> tuple[ObservedWindow, _Checked]:
    """First observation, then at most nine more. The five-second budget starts after the first."""

    authority()
    read = await observe()
    if read.observation.observation_id == before_observation_id:
        stop()
        raise ComputerUseContractError("stale_observation")
    verification: VerificationState = "not_checked"
    if predicate is not None:
        verification = evaluate_postcondition(read.observation, predicate)
        started = clock.now()
        seen = {before_observation_id, read.observation.observation_id}
        for _ in range(9):
            if verification in {"passed", "not_checked"}:
                break
            elapsed = (clock.now() - started).total_seconds()
            if elapsed < 0 or elapsed >= 5:
                break
            authority()
            await wait(min(0.5, 5 - elapsed))
            authority()
            remaining = 5 - (clock.now() - started).total_seconds()
            if not 0 < remaining <= 5:
                break
            async with asyncio.timeout(remaining):
                read = await observe()
            if read.observation.observation_id in seen:
                stop()
                raise ComputerUseContractError("stale_observation")
            seen.add(read.observation.observation_id)
            verification = evaluate_postcondition(read.observation, predicate)
    if verification == "pending":
        verification = "not_checked"
    return read, verification
