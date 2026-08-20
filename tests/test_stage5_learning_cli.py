"""S51.6 REPL and Typer surfaces stay on the typed Learning API."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from morrow.application.commands import CommandService
from morrow.application.orchestrator import DispatchResult
from morrow.interfaces import cli as cli_module
from morrow.interfaces import learning_cli
from morrow.interfaces import terminal as terminal_module
from test_stage5_project_knowledge import _project_candidate
from test_terminal import ScriptedTerminal


class CommandOrchestrator:
    def __init__(self, command_service, session):
        self.command_service = command_service
        self.session = session

    async def stream(self, text):
        result = self.command_service.execute(text)
        yield DispatchResult(lines=result.lines, action=result.action, value=result.value)


def _command_service(api, tmp_path):
    session = SimpleNamespace(
        read_only=False,
        workspace_preferences_read_only=False,
        persisted=True,
        dirty=False,
        session_id="ses_1",
    )
    identity = SimpleNamespace(
        workspace_id="ws_1",
        display_name="test",
        path=tmp_path,
    )
    return CommandService(
        session=session,
        identity=identity,
        project_store=None,
        api=api,
        id_source=api.id_source,
    ), session


def _install_scripted_terminal(monkeypatch, terminal):
    monkeypatch.setattr(terminal_module, "Terminal", lambda: terminal)
    monkeypatch.setattr(terminal_module, "PromptSession", lambda: object())
    monkeypatch.setattr(terminal_module, "patch_stdout", nullcontext)


@pytest.mark.asyncio
async def test_repl_learning_accept_cancel_does_not_write(tmp_path, monkeypatch):
    session_handle, journal, api, candidate, _reviewer = await _project_candidate(tmp_path)
    try:
        command_service, repl_session = _command_service(api, tmp_path)
        terminal = ScriptedTerminal([f"/learn accept {candidate.candidate_id}", "n", "/exit"])
        _install_scripted_terminal(monkeypatch, terminal)
        code = await terminal_module.run_repl(
            CommandOrchestrator(command_service, repl_session), session=repl_session
        )
        assert code == 0
        assert journal.get_learning_candidate("ws_1", candidate.candidate_id).status.value == (
            "proposed"
        )
        assert journal.list_learning_candidate_decisions("ws_1") == ()
        assert journal.list_project_knowledge_heads("ws_1") == ()
    finally:
        session_handle.close()


@pytest.mark.asyncio
async def test_repl_learning_accept_and_memory_disable_use_application_services(
    tmp_path, monkeypatch
):
    session_handle, journal, api, candidate, _reviewer = await _project_candidate(tmp_path)
    try:
        command_service, repl_session = _command_service(api, tmp_path)
        terminal = ScriptedTerminal([f"/learn accept {candidate.candidate_id}", "y", "/exit"])
        _install_scripted_terminal(monkeypatch, terminal)
        code = await terminal_module.run_repl(
            CommandOrchestrator(command_service, repl_session), session=repl_session
        )
        assert code == 0
        head = journal.list_project_knowledge_heads("ws_1")[0]
        assert head.status.value == "active"
        command_service, repl_session = _command_service(api, tmp_path)
        terminal = ScriptedTerminal(
            ["/memory list", f"/memory disable {head.knowledge_id}", "y", "/exit"]
        )
        _install_scripted_terminal(monkeypatch, terminal)
        code = await terminal_module.run_repl(
            CommandOrchestrator(command_service, repl_session), session=repl_session
        )
        assert code == 0
        assert any(head.knowledge_id in line for line in terminal.console.lines)

        assert journal.get_project_knowledge_head("ws_1", head.knowledge_id).status.value == (
            "disabled"
        )
    finally:
        session_handle.close()


def test_learning_and_memory_typer_surfaces_are_registered():
    runner = CliRunner()
    learning = runner.invoke(cli_module.app, ["learning", "--help"])
    memory = runner.invoke(cli_module.app, ["memory", "--help"])
    assert learning.exit_code == 0
    assert "accept" in learning.stdout
    assert memory.exit_code == 0
    assert "disable" in memory.stdout


def test_memory_show_passes_requested_revision_to_application_service():
    class FakeApi:
        def __init__(self):
            self.calls = []

        def get_project_knowledge(self, knowledge_id, *, revision=None):
            self.calls.append((knowledge_id, revision))
            return {"knowledge_id": knowledge_id, "revision": revision}

    api = FakeApi()
    value = learning_cli._knowledge_or_error(api, "knw_history", revision=1)
    assert value == {"knowledge_id": "knw_history", "revision": 1}
    assert api.calls == [("knw_history", 1)]
