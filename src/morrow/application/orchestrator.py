"""One input dispatch path for slash commands and ordinary AgentLoop chat."""

from __future__ import annotations

from dataclasses import dataclass, field

from morrow.core.models import AgentEvent


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
        id_source=None,
    ) -> None:
        self.session = session
        self.runtime = runtime
        self.command_service = command_service
        self.context_builder = context_builder
        self.id_source = id_source

    def reset_session(self) -> None:
        if self.id_source is None:
            raise RuntimeError("session ID source is unavailable")
        self.command_service.reset_session(self.id_source.new_id("ses"))

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
