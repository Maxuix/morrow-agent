"""Retry eligibility and task resumability are independent decisions."""

import pytest

from morrow.adapters.models.openai_compatible import classify_failure
from morrow.core.events import lifecycle_is_valid
from morrow.core.execution_pause import LocalTurnPauseControl
from morrow.core.models import (
    ModelErrorCode,
    ModelEvent,
    ModelFailure,
    ModelFailureOrigin,
    ModelRef,
)
from morrow.runtime.agent import AgentLoop
from morrow.runtime.session import Session
from morrow.testing import make_context_builder


@pytest.mark.parametrize(
    "code",
    [
        ModelErrorCode.NETWORK,
        ModelErrorCode.TIMEOUT,
        ModelErrorCode.RATE_LIMIT,
        ModelErrorCode.AUTH,
        ModelErrorCode.INTERNAL,
        ModelErrorCode.INVALID_RESPONSE,
    ],
)
@pytest.mark.parametrize("retry", [True, False])
async def test_request_failure_exhausts_bounded_retries_then_interrupts(code, retry):
    calls = []
    sleeps = []

    class Provider:
        async def stream(self, *args, **kwargs):
            calls.append(1)
            yield ModelEvent(kind="text_delta", text="unfinished response")
            yield ModelEvent(
                kind="error",
                failure=ModelFailure(
                    code=code,
                    origin=ModelFailureOrigin.PROVIDER,
                    retryable=retry,
                    message="request unavailable",
                ),
            )

    async def sleep(delay):
        sleeps.append(delay)

    loop = AgentLoop(
        Provider(),
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(max_retries=2),
        retry_sleep=sleep,
        pause_control=LocalTurnPauseControl(),
    )
    session = Session(session_id="s")
    events = [e async for e in loop.run_task(session, "finish work")]
    assert lifecycle_is_valid(events)
    assert events[-1].payload["finish_reason"] == "interrupted"
    assert events[-1].payload["reason"] == "provider_failure"
    assert len(calls) == (3 if retry else 1)
    assert len(sleeps) == (2 if retry else 0)
    assert session.log.has_active_turn is False
    assert not any(
        getattr(r, "message", None) and r.message.role == "assistant"
        for r in session.log.snapshot().records
    )


@pytest.mark.parametrize(
    "error",
    [
        type("RemoteProtocolError", (Exception,), {})("disconnected"),
        RuntimeError("peer closed connection without sending complete message body"),
    ],
)
def test_wrapped_stream_disconnect_is_network(error):
    wrapper = RuntimeError("SDK request failed")
    wrapper.__cause__ = error
    failure = classify_failure(wrapper, phase="stream")
    assert failure.code is ModelErrorCode.NETWORK
    assert failure.retryable
    assert failure.origin.value == "provider"


def test_unrelated_programming_error_is_not_misclassified_as_network():
    failure = classify_failure(TypeError("invalid adapter state"))
    assert failure.code is ModelErrorCode.INVALID_RESPONSE
    assert not failure.retryable
