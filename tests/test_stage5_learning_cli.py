"""S51.6 REPL and Typer surfaces stay on the typed Learning API."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from morrow.application.commands import CommandService
from morrow.application.orchestrator import DispatchResult
from morrow.core.application import ApplicationError
from morrow.core.learning import (
    LearningMode,
    LearningReviewFailureCode,
    LearningReviewStatus,
    LearningScope,
)
from morrow.interfaces import cli as cli_module
from morrow.interfaces import learning_cli
from morrow.interfaces import terminal as terminal_module
from test_stage5_configuration_promotion import _promotion_subjects
from test_stage5_project_knowledge import _project_candidate
from test_stage5_review_pipeline import EmptyReviewer, _accepted, _api
from test_terminal import ScriptedTerminal


class CommandOrchestrator:
    def __init__(self, command_service, session):
        self.command_service = command_service
        self.session = session

    async def stream(self, text):
        result = self.command_service.execute(text)
        yield DispatchResult(lines=result.lines, action=result.action, value=result.value)


class FailingReviewer:
    async def review(self, context, *, model, timeout_seconds):
        del context, model, timeout_seconds
        raise RuntimeError("synthetic provider failure")


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
    assert "retry" in learning.stdout
    assert "set-mode" in learning.stdout
    assert memory.exit_code == 0
    assert "disable" in memory.stdout


def test_headless_learning_review_failure_uses_nonzero_exit():
    result = SimpleNamespace(
        review=SimpleNamespace(
            status=LearningReviewStatus.FAILED,
        )
    )

    with pytest.raises(typer.Exit) as error:
        cli_module._emit_learning_review_result(result)

    assert error.value.exit_code == 2


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


def test_repl_learning_accept_can_explicitly_select_global_scope(tmp_path):
    _app, identity, handle, _journal, api, candidate = _promotion_subjects(
        tmp_path, candidate_scope=LearningScope.GLOBAL
    )
    try:
        command_service, _session = _command_service(api, tmp_path)
        result = command_service._learn_command(
            ["/learn", "accept", candidate.candidate_id, "--scope", "global"]
        )
        assert result.action == "learning_accept_preview"
        assert result.value.scope == "global"
        assert "作用域：global" in result.lines[2]
    finally:
        handle.close()


def test_repl_learning_mode_is_explicit_and_rejects_explicit_auto(tmp_path):
    session, _journal, api = _api(tmp_path)
    try:
        command_service, _repl_session = _command_service(api, tmp_path)
        result = command_service._learn_command(["/learn", "mode", "off"])
        assert result.value.policy.mode is LearningMode.OFF
        assert api.learning_policy_status().policy.mode is LearningMode.OFF
        with pytest.raises(ApplicationError) as error:
            command_service._learn_command(["/learn", "mode", "explicit-auto"])
        assert "explicit_auto" in error.value.message
    finally:
        session.close()


@pytest.mark.asyncio
async def test_repl_learning_review_runs_in_foreground_and_reports_zero_candidates(
    tmp_path, monkeypatch
):
    session, journal, api = _api(tmp_path, reviewer=EmptyReviewer())
    try:
        accepted = _accepted(api, journal)
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        review = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items[0]
        command_service, repl_session = _command_service(api, tmp_path)
        terminal = ScriptedTerminal([f"/learn review {review.review_id}", "/exit"])
        _install_scripted_terminal(monkeypatch, terminal)

        code = await terminal_module.run_repl(
            CommandOrchestrator(command_service, repl_session), session=repl_session
        )

        assert code == 0
        assert any("没有生成候选" in line for line in terminal.console.lines)
        assert api.get_learning_review(review.review_id).status.value == "completed"
    finally:
        session.close()


@pytest.mark.asyncio
async def test_repl_learning_review_reports_failure_and_retry_hint(tmp_path, monkeypatch):
    session, journal, api = _api(tmp_path, reviewer=FailingReviewer())
    try:
        accepted = _accepted(api, journal, with_user_turn=True)
        outcome = api.list_outcomes(accepted.value.task_run_id)[0]
        review = api.list_learning_reviews(task_outcome_id=outcome.outcome_id).items[0]
        command_service, repl_session = _command_service(api, tmp_path)
        terminal = ScriptedTerminal([f"/learn review {review.review_id}", "/exit"])
        _install_scripted_terminal(monkeypatch, terminal)

        code = await terminal_module.run_repl(
            CommandOrchestrator(command_service, repl_session), session=repl_session
        )

        assert code == 0
        assert any("未完成" in line and "retry" in line for line in terminal.console.lines)
        failed = api.get_learning_review(review.review_id)
        assert failed.status.value == "failed"
        assert failed.failure_code is LearningReviewFailureCode.PROVIDER_UNAVAILABLE
    finally:
        session.close()
