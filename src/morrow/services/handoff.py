"""Typed Handoff replacement, model generation, and deterministic recovery."""

from __future__ import annotations

import asyncio

from morrow.core.models import (
    AssistantMessage,
    Handoff,
    StateWriteStatus,
    UserMessage,
    sanitize_text,
)
from morrow.runtime.structured import complete_structured


class HandoffService:
    def __init__(
        self,
        project_store,
        provider,
        model,
        context_builder,
        workspace_id: str,
        *,
        timeout: float = 30.0,
    ) -> None:
        self.project_store = project_store
        self.provider = provider
        self.model = model
        self.context_builder = context_builder
        self.workspace_id = workspace_id
        self.timeout = timeout

    def _fallback(self, session) -> Handoff:
        previous = session.loaded_handoff
        fallback = self.context_builder.build(session, purpose="handoff_fallback")
        last_user = next(
            (message.content for message in fallback.messages if isinstance(message, UserMessage)),
            "继续推进当前工作",
        )
        last_assistant = next(
            (
                message.content
                for message in fallback.messages
                if isinstance(message, AssistantMessage)
            ),
            "",
        )
        note = sanitize_text(f"摘要生成失败；最近请求：{last_user}；最近回复：{last_assistant}")
        if previous:
            return previous.model_copy(update={"recovery_note": note})
        return Handoff(current_goal=sanitize_text(last_user, 240), recovery_note=note)

    async def generate(
        self, session, *, allow_fallback: bool = True, cancel_without_fallback: bool = True
    ) -> tuple[Handoff, bool]:
        try:
            return await complete_structured(
                self.provider,
                self.model,
                self.context_builder,
                session,
                Handoff,
                "请只输出 JSON 格式的完整 Handoff，字段为 current_goal、progress、decisions、blockers、open_questions、next_actions、recovery_note。",
                timeout=self.timeout,
            )
        except asyncio.CancelledError:
            if cancel_without_fallback:
                raise
            if allow_fallback:
                return self._fallback(session), True
            raise
        except Exception:
            if allow_fallback:
                return self._fallback(session), True
            raise

    def publish(self, session, handoff: Handoff, *, expected_revision: int | None) -> object:
        return self.project_store.write_handoff(
            self.workspace_id, handoff, expected_revision=expected_revision
        )

    async def generate_and_publish(
        self, session, *, expected_revision: int | None
    ) -> tuple[object, bool]:
        if session.read_only:
            from morrow.core.models import StateWriteResult

            return StateWriteResult(status=StateWriteStatus.FAILED, error="read_only"), False
        handoff, degraded = await self.generate(session)
        result = self.publish(session, handoff, expected_revision=expected_revision)
        if result.status != StateWriteStatus.OK:
            return result, degraded
        session.loaded_handoff = handoff
        session.handoff_source_revision = result.revision
        session.dirty = False
        return result, degraded
