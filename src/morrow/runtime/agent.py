"""Single model turn runtime and public event lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from morrow.core.events import completion_payload, make_event
from morrow.core.models import (
    AgentEvent,
    FinishReason,
    ModelErrorCode,
    ModelRef,
    sanitize_text,
)
from morrow.core.ports import Clock, IdSource, ModelProvider
from morrow.runtime.session import Session


class AgentRuntime:
    def __init__(
        self,
        provider: ModelProvider,
        model: ModelRef,
        context_builder,
        *,
        id_source: IdSource | None = None,
        clock: Clock | None = None,
        max_retries: int = 1,
    ) -> None:
        self.provider = provider
        self.model = model
        self.context_builder = context_builder
        self.id_source = id_source
        self.clock = clock
        self.max_retries = max_retries

    def _id(self, prefix: str) -> str:
        return self.id_source.new_id(prefix) if self.id_source else f"{prefix}_{id(object())}"

    async def run_turn(self, session: Session, user_input: str) -> AsyncIterator[AgentEvent]:
        session.accept_user(user_input)
        turn_id = self._id("turn")
        sequence = 0
        visible = ""
        started = False

        def event(event_type: str, payload: dict) -> AgentEvent:
            nonlocal sequence
            sequence += 1
            return make_event(
                event_type=event_type,
                event_id=self._id("evt"),
                session_id=session.session_id,
                turn_id=turn_id,
                sequence=sequence,
                payload=payload,
                timestamp=self.clock.now() if self.clock else None,
            )

        started = True
        yield event("turn.started", {})
        attempt = 0
        try:
            while True:
                try:
                    context = self.context_builder.build(session, current_user=user_input)
                except ValueError as exc:
                    yield event(
                        "error",
                        {
                            "code": ModelErrorCode.INVALID_RESPONSE.value,
                            "message": sanitize_text(str(exc)),
                        },
                    )
                    yield event("turn.completed", completion_payload(FinishReason.ERROR, visible))
                    return
                try:
                    async for model_event in self.provider.stream(self.model, context.messages):
                        if model_event.kind == "text_delta" and model_event.text:
                            visible += model_event.text
                            yield event("text.delta", {"text": model_event.text})
                        elif model_event.kind == "error":
                            if (
                                not visible
                                and attempt < self.max_retries
                                and model_event.error_code
                                in {
                                    ModelErrorCode.NETWORK,
                                    ModelErrorCode.RATE_LIMIT,
                                    ModelErrorCode.TIMEOUT,
                                }
                            ):
                                attempt += 1
                                yield event("status.changed", {"status": "retrying"})
                                break
                            message = sanitize_text(model_event.error_message or "模型调用失败")
                            yield event(
                                "error",
                                {
                                    "code": (
                                        model_event.error_code or ModelErrorCode.INTERNAL
                                    ).value,
                                    "message": message,
                                },
                            )
                            yield event(
                                "turn.completed", completion_payload(FinishReason.ERROR, visible)
                            )
                            return
                        elif model_event.kind == "completed":
                            if not visible.strip():
                                yield event(
                                    "error",
                                    {
                                        "code": ModelErrorCode.INVALID_RESPONSE.value,
                                        "message": "模型没有返回可见文本",
                                    },
                                )
                                yield event(
                                    "turn.completed",
                                    completion_payload(FinishReason.ERROR, visible),
                                )
                                return
                            session.accept_assistant(visible)
                            session.dirty = True
                            yield event(
                                "turn.completed", completion_payload(FinishReason.STOP, visible)
                            )
                            return
                    else:
                        if not visible:
                            yield event(
                                "error",
                                {
                                    "code": ModelErrorCode.INVALID_RESPONSE.value,
                                    "message": "模型没有返回可见文本",
                                },
                            )
                            yield event(
                                "turn.completed", completion_payload(FinishReason.ERROR, visible)
                            )
                        else:
                            session.accept_assistant(visible)
                            yield event(
                                "turn.completed", completion_payload(FinishReason.STOP, visible)
                            )
                        return
                    if attempt <= self.max_retries:
                        continue
                except asyncio.CancelledError:
                    yield event(
                        "turn.completed", completion_payload(FinishReason.CANCELLED, visible)
                    )
                    return
        except asyncio.CancelledError:
            if started:
                yield event("turn.completed", completion_payload(FinishReason.CANCELLED, visible))
            return
