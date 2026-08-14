"""One input dispatch path for slash commands, config intent, and chat."""

from __future__ import annotations

from dataclasses import dataclass, field

from morrow.core.models import AgentEvent
from morrow.services.preferences import ConfigIntentGate


@dataclass
class DispatchResult:
    lines: list[str] = field(default_factory=list)
    events: list[AgentEvent] = field(default_factory=list)
    action: str | None = None
    degraded: bool = False
    value: object | None = None


class SessionOrchestrator:
    def __init__(
        self,
        *,
        session,
        runtime,
        command_service,
        context_builder,
        config_extractor=None,
        config_patch_service=None,
        id_source=None,
    ) -> None:
        self.session = session
        self.runtime = runtime
        self.command_service = command_service
        self.context_builder = context_builder
        self.config_extractor = config_extractor
        self.config_patch_service = config_patch_service
        self.gate = ConfigIntentGate()
        self.id_source = id_source

    async def stream(self, text: str):
        """Yield model events as they arrive, then a terminal dispatch result."""
        if text.startswith("/"):
            result = self.command_service.execute(text)
            yield DispatchResult(
                lines=result.lines,
                action=result.action,
                value=getattr(result, "value", None),
            )
            return
        if self.config_extractor and self.config_patch_service:
            decision = self.gate.match(text)
            if decision.mixed_task:
                yield DispatchResult(lines=["这条输入同时包含任务和配置请求，请拆成两条消息。"])
                return
            if decision.forbidden:
                yield DispatchResult(
                    lines=["自然语言配置不能修改凭据、Provider、模型、权限或安全边界。"]
                )
                return
            if decision.matched:
                extracted = await self.config_extractor(text, self.session)
                if extracted.result == "config_patch" and extracted.patch:
                    yield DispatchResult(
                        lines=["配置预览：即将写入一项经过字段白名单校验的配置。"],
                        action="config_preview",
                        value=extracted.patch,
                    )
                    return
                if extracted.result == "clarification_required":
                    yield DispatchResult(
                        lines=[extracted.question or "请说明要保存的作用域和字段。"]
                    )
                    return
        async for event in self.runtime.run_turn(self.session, text):
            yield event
        yield DispatchResult()

    async def dispatch(self, text: str) -> DispatchResult:
        """Compatibility wrapper that collects the streaming dispatch."""
        events: list[AgentEvent] = []
        result = DispatchResult()
        async for item in self.stream(text):
            if isinstance(item, AgentEvent):
                events.append(item)
            else:
                result = item
        result.events = events
        return result
