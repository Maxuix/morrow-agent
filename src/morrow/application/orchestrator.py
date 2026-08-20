"""One input dispatch path for slash commands and ordinary AgentLoop chat."""

from __future__ import annotations

from dataclasses import dataclass, field

from morrow.core.application import ApplicationError
from morrow.core.domain import SessionLifecycle, session_can_start_work
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
        new_id = self.id_source.new_id("ses")
        starter = getattr(self.session.committer, "start_new_session", None)
        if starter is not None:
            starter(self.session, new_id)
            return
        self.command_service.reset_session(new_id)

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
        api = getattr(self.command_service, "api", None)
        if api is not None:
            try:
                durable_session = api.get_session(self.session.session_id)
            except ApplicationError as exc:
                yield DispatchResult(lines=[exc.message], degraded=True)
                return
            if durable_session is not None:
                self.session.lifecycle = durable_session.lifecycle
                self.session.health = durable_session.health
        if self.session.lifecycle is not SessionLifecycle.ACTIVE:
            yield DispatchResult(lines=["当前 Session 已归档，无法开始新的 Turn。"])
            return
        client_message_id = None
        if self.id_source is not None:
            client_message_id = self.id_source.new_id("cmsg")
        async for event in self.runtime.run_turn(
            self.session, text, client_message_id=client_message_id
        ):
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

    async def resume_recovery(self):
        """Continue an already-open Turn after an explicit recovery decision."""

        if not session_can_start_work(self.session.lifecycle, self.session.health):
            raise RuntimeError("only an active healthy Session can resume a Turn")
        async for event in self.runtime.loop.run_task(
            self.session,
            "",
            resume_current_turn=True,
        ):
            yield event
