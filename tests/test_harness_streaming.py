"""Streaming visibility, provisional attempts, and safe chunk boundaries."""

import pytest

from morrow.core.models import (
    AssistantMessage,
    ModelEvent,
    ModelFailure,
    ModelRef,
)
from morrow.runtime.agent import AgentLoop
from morrow.runtime.session import Session
from morrow.runtime.text_stream import TextStreamProjection
from morrow.testing import make_context_builder


@pytest.mark.asyncio
async def test_complete_line_is_visible_before_provider_completes_and_is_not_replayed():
    class Provider:
        finished = False

        async def stream(self, model, messages, tools=()):
            yield ModelEvent(kind="text_delta", text="first line\n")
            yield ModelEvent(kind="text_delta", text="last line")
            self.finished = True
            yield ModelEvent(
                kind="completed",
                finish_reason="stop",
                message=AssistantMessage(content="first line\nlast line"),
            )

    provider = Provider()
    session = Session(session_id="s")
    loop = AgentLoop(provider, ModelRef(provider_id="p", model_id="m"), make_context_builder())
    stream = loop.run_task(session, "go")
    assert (await anext(stream)).type == "turn.started"
    first = await anext(stream)
    assert first.type == "text.delta" and first.payload["text"] == "first line\n"
    assert first.payload["attempt_ordinal"] == 1
    assert not provider.finished
    assert not any(isinstance(m, AssistantMessage) for m in session.log.messages_view())
    rest = [event async for event in stream]
    assert "".join(e.payload["text"] for e in [first, *rest] if e.type == "text.delta") == (
        "first line\nlast line"
    )
    assert rest[-1].payload["text"] == "first line\nlast line"


@pytest.mark.asyncio
async def test_failed_preview_does_not_enter_history_and_retry_has_distinct_attempt():
    class Provider:
        calls = 0

        async def stream(self, model, messages, tools=()):
            self.calls += 1
            if self.calls == 1:
                yield ModelEvent(kind="text_delta", text="provisional\n")
                yield ModelEvent(
                    kind="error",
                    failure=ModelFailure(
                        code="network", origin="provider", message="unavailable", retryable=True
                    ),
                )
            else:
                yield ModelEvent(kind="text_delta", text="accepted\n")
                yield ModelEvent(
                    kind="completed",
                    finish_reason="stop",
                    message=AssistantMessage(content="accepted\n"),
                )

    session = Session(session_id="s")
    loop = AgentLoop(Provider(), ModelRef(provider_id="p", model_id="m"), make_context_builder())
    events = [event async for event in loop.run_task(session, "go")]
    assert [e.payload["attempt_ordinal"] for e in events if e.type == "text.delta"] == [1, 2]
    assert any(e.payload.get("status") == "retrying" for e in events)
    assert [m.content for m in session.log.messages_view() if isinstance(m, AssistantMessage)] == [
        "accepted\n"
    ]


@pytest.mark.parametrize(
    "chunks",
    [
        ("ready\napi_key = syn", "thetic_private_value\nend"),
        ("ready\napi_key =\n", "synthetic_private_value\nend"),
    ],
)
def test_secrets_split_across_chunks_and_lines_are_redacted(chunks):
    projection = TextStreamProjection()
    visible = "".join(projection.feed(chunk) for chunk in chunks)
    remainder, reset = projection.finish("".join(chunks))
    assert not reset
    assert "synthetic_private_value" not in visible + remainder
    assert "<redacted>" in visible + remainder


def test_final_revision_requests_reset_and_filters_control_sequences():
    projection = TextStreamProjection()
    assert projection.feed("draft\n") == "draft\n"
    final, reset = projection.finish("revised\x1b[2J\x00")
    assert reset and final == "revised[2J"
