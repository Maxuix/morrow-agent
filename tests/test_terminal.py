from __future__ import annotations

from contextlib import nullcontext

import pytest

from morrow.application.orchestrator import DispatchResult
from morrow.core.models import AgentEvent
from morrow.interfaces import terminal as terminal_module
from morrow.runtime.session import Session
from morrow.testing import seed_user_turn


class ConsoleStub:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *values, **kwargs) -> None:
        del kwargs
        self.lines.append(" ".join(str(value) for value in values))


class ScriptedTerminal:
    def __init__(self, inputs) -> None:
        self.inputs = list(inputs)
        self.prompt_count = 0
        self.console = ConsoleStub()

    async def prompt(self, session, message="你 > "):
        del session, message
        self.prompt_count += 1
        if not self.inputs:
            raise EOFError
        value = self.inputs.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class OrchestratorStub:
    def __init__(self, actions, session=None) -> None:
        self.actions = actions
        self.session = session
        self.reset_count = 0

    async def stream(self, text):
        yield DispatchResult(action=self.actions.get(text))

    def reset_session(self):
        self.reset_count += 1
        if self.session is not None:
            self.session.reset("reset")


def install_terminal(monkeypatch, terminal):
    monkeypatch.setattr(terminal_module, "Terminal", lambda: terminal)
    monkeypatch.setattr(terminal_module, "PromptSession", lambda: object())
    monkeypatch.setattr(terminal_module, "patch_stdout", nullcontext)


def test_terminal_segments_mixed_text_tool_and_final_text_without_replay_or_payload_leak():
    class RecordingConsole:
        def __init__(self):
            self.value = ""

        def print(self, *values, **kwargs):
            self.value += " ".join(str(value) for value in values)
            self.value += kwargs.get("end", "\n")

    console = RecordingConsole()
    terminal = terminal_module.Terminal(console=console)

    def event(event_type, sequence, payload):
        return AgentEvent(
            type=event_type,
            event_id=f"e{sequence}",
            session_id="s",
            turn_id="t",
            sequence=sequence,
            payload=payload,
        )

    events = [
        event("turn.started", 1, {}),
        event("text.delta", 2, {"text": "先查"}),
        event(
            "tool.status",
            3,
            {"name": "lookup_record", "status": "running", "ordinal": 1, "total": 1},
        ),
        event("text.delta", 4, {"text": "最终答案"}),
        event("turn.completed", 5, {"finish_reason": "stop"}),
    ]
    for item in events:
        terminal.show_event(item)

    assert console.value == "先查\n↳ 工具步骤 1/1：lookup_record\n最终答案\n"


@pytest.mark.asyncio
async def test_clean_primary_eof_exits_once(monkeypatch):
    terminal = ScriptedTerminal([EOFError()])
    install_terminal(monkeypatch, terminal)
    code = await terminal_module.run_repl(
        OrchestratorStub({"/exit": "exit"}), session=Session(session_id="s")
    )
    assert code == 0
    assert terminal.prompt_count == 1


@pytest.mark.asyncio
async def test_dirty_confirmation_eof_exits_two_without_reset(monkeypatch):
    terminal = ScriptedTerminal([EOFError(), EOFError()])
    install_terminal(monkeypatch, terminal)
    session = Session(session_id="s")
    seed_user_turn(session, "unsaved")
    orchestrator = OrchestratorStub({"/exit": "exit"}, session)
    code = await terminal_module.run_repl(orchestrator, session=session)
    assert code == 2
    assert orchestrator.reset_count == 0
    assert session.messages[0].content == "unsaved"


@pytest.mark.asyncio
async def test_dirty_new_confirmed_discards_only_process_local_session(monkeypatch):
    terminal = ScriptedTerminal(["/new", "y", "/exit"])
    install_terminal(monkeypatch, terminal)
    session = Session(session_id="s")
    seed_user_turn(session, "unsaved")
    orchestrator = OrchestratorStub({"/new": "discard_new", "/exit": "exit"}, session)
    code = await terminal_module.run_repl(orchestrator, session=session)
    assert code == 0
    assert orchestrator.reset_count == 1
    assert session.messages == ()


@pytest.mark.asyncio
async def test_dirty_new_cancelled_preserves_session(monkeypatch):
    terminal = ScriptedTerminal(["/new", "n", "/exit", "y"])
    install_terminal(monkeypatch, terminal)
    session = Session(session_id="s")
    seed_user_turn(session, "unsaved")
    orchestrator = OrchestratorStub({"/new": "discard_new", "/exit": "exit"}, session)
    code = await terminal_module.run_repl(orchestrator, session=session)
    assert code == 0
    assert orchestrator.reset_count == 0
    assert session.messages[0].content == "unsaved"


@pytest.mark.asyncio
async def test_dirty_exit_cancelled_stays_in_repl_then_confirmed_exits(monkeypatch):
    terminal = ScriptedTerminal(["/exit", "n", "/exit", "y"])
    install_terminal(monkeypatch, terminal)
    session = Session(session_id="s")
    seed_user_turn(session, "unsaved")
    code = await terminal_module.run_repl(
        OrchestratorStub({"/exit": "exit"}, session), session=session
    )
    assert code == 0
    assert terminal.prompt_count == 4
    assert session.messages[0].content == "unsaved"
