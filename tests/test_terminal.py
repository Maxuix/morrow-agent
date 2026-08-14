from __future__ import annotations

import asyncio
from contextlib import nullcontext

import pytest

from morrow.application.orchestrator import DispatchResult
from morrow.interfaces import terminal as terminal_module
from morrow.runtime.session import Session


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
    def __init__(self, actions) -> None:
        self.actions = actions
        self.reset_count = 0

    async def stream(self, text):
        yield DispatchResult(action=self.actions.get(text))

    def reset_session(self):
        self.reset_count += 1


class StoreStub:
    def load_handoff(self, workspace_id):
        del workspace_id
        return type("Loaded", (), {"revision": 1, "value": None})()


class SuccessfulSave:
    status = type("Status", (), {"value": "ok"})()


class HandoffStub:
    def __init__(self, result=None) -> None:
        self.calls = 0
        self.result = result or (SuccessfulSave(), False)

    async def generate_and_publish(self, session, *, expected_revision):
        del session, expected_revision
        self.calls += 1
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def install_terminal(monkeypatch, terminal):
    monkeypatch.setattr(terminal_module, "Terminal", lambda: terminal)
    monkeypatch.setattr(terminal_module, "PromptSession", lambda: object())
    monkeypatch.setattr(terminal_module, "patch_stdout", nullcontext)


@pytest.mark.asyncio
async def test_clean_primary_eof_exits_once(monkeypatch):
    terminal = ScriptedTerminal([EOFError()])
    install_terminal(monkeypatch, terminal)

    code = await terminal_module.run_repl(
        OrchestratorStub({"/exit": "exit"}),
        project_store=StoreStub(),
        workspace_id="ws",
        session=Session(session_id="s"),
    )

    assert code == 0
    assert terminal.prompt_count == 1


@pytest.mark.asyncio
async def test_dirty_independent_eof_during_exit_confirmation_terminates_code_two(monkeypatch):
    terminal = ScriptedTerminal([EOFError(), EOFError()])
    install_terminal(monkeypatch, terminal)
    session = Session(session_id="s")
    session.accept_user("unsaved")
    handoff = HandoffStub()

    code = await asyncio.wait_for(
        terminal_module.run_repl(
            OrchestratorStub({"/exit": "exit"}),
            handoff_service=handoff,
            project_store=StoreStub(),
            workspace_id="ws",
            session=session,
        ),
        timeout=0.1,
    )

    assert code == 2
    assert terminal.prompt_count == 2
    assert handoff.calls == 0
    assert session.messages[0].content == "unsaved"


@pytest.mark.asyncio
async def test_closed_switch_prompt_terminates_without_save_or_reset(monkeypatch):
    terminal = ScriptedTerminal(["/new", EOFError()])
    install_terminal(monkeypatch, terminal)
    session = Session(session_id="s")
    session.accept_user("unsaved")
    handoff = HandoffStub()
    orchestrator = OrchestratorStub({"/new": "switch_new"})

    code = await terminal_module.run_repl(
        orchestrator,
        handoff_service=handoff,
        project_store=StoreStub(),
        workspace_id="ws",
        session=session,
    )

    assert code == 2
    assert terminal.prompt_count == 2
    assert handoff.calls == 0
    assert orchestrator.reset_count == 0
    assert session.session_id == "s"
    assert session.messages[0].content == "unsaved"


@pytest.mark.asyncio
async def test_dirty_continuation_primary_eof_saves_then_exits(monkeypatch):
    terminal = ScriptedTerminal([EOFError()])
    install_terminal(monkeypatch, terminal)
    session = Session(session_id="s", handoff_source_revision=1)
    session.accept_user("continue")
    handoff = HandoffStub()

    code = await terminal_module.run_repl(
        OrchestratorStub({"/exit": "exit"}),
        handoff_service=handoff,
        project_store=StoreStub(),
        workspace_id="ws",
        session=session,
    )

    assert code == 0
    assert terminal.prompt_count == 1
    assert handoff.calls == 1


@pytest.mark.asyncio
async def test_cancelled_continuation_save_preserves_session():
    terminal = ScriptedTerminal(["/exit"])
    session = Session(session_id="s", handoff_source_revision=1)
    session.accept_user("continue")
    handoff = HandoffStub(result=asyncio.CancelledError())

    code = await terminal_module._exit(
        OrchestratorStub({"/exit": "exit"}),
        handoff,
        StoreStub(),
        "ws",
        session,
        terminal,
        object(),
    )

    assert code is None
    assert session.session_id == "s"
    assert session.messages[0].content == "continue"
