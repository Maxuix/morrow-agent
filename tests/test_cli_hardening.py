"""Batch-5 CLI hardening regressions: P2-17(a)-(f)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from morrow.bootstrap import build_application
from morrow.core.execution import ApprovalResolution, EffectClass, ToolExecutionState
from morrow.interfaces import approval_cli, workflow_cli
from morrow.interfaces.cli import app


def test_model_show_malformed_target_reports_friendly_error(tmp_path):
    result = CliRunner().invoke(
        app, ["model", "show", "foo", "--state-root", str(tmp_path / "state")]
    )

    assert result.exit_code == 2
    assert "provider_id/model_id" in result.output
    assert "UnboundLocalError" not in result.output
    assert "Traceback" not in result.output


def test_model_show_unknown_model_reports_friendly_error(tmp_path):
    result = CliRunner().invoke(
        app, ["model", "show", "ghost/m", "--state-root", str(tmp_path / "state")]
    )

    assert result.exit_code == 2
    assert "未知 Provider: ghost" in result.output
    assert "Traceback" not in result.output


def _write_corrupt_global_config(state: Path) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / "config.yaml").write_text("providers: [unclosed\n", encoding="utf-8")


def test_provider_service_list_raises_on_corrupt_global_config(tmp_path):
    state = tmp_path / "state"
    _write_corrupt_global_config(state)
    service = build_application(state_root=state).provider_service

    with pytest.raises(ValueError, match="config.yaml") as exc_info:
        service.list()

    assert "corrupt" in str(exc_info.value)


def test_provider_list_cli_surfaces_corrupt_global_config(tmp_path):
    state = tmp_path / "state"
    _write_corrupt_global_config(state)

    result = CliRunner().invoke(app, ["provider", "list", "--state-root", str(state)])

    assert result.exit_code == 2
    assert "config.yaml" in result.output
    assert "Traceback" not in result.output


def test_model_list_cli_surfaces_corrupt_global_config(tmp_path):
    state = tmp_path / "state"
    _write_corrupt_global_config(state)

    result = CliRunner().invoke(app, ["model", "list", "--state-root", str(state)])

    assert result.exit_code == 2
    assert "config.yaml" in result.output
    assert "Traceback" not in result.output


@dataclass
class _Preview:
    proposal: dict
    expected_row_version: int = 1
    expected_document_revision: int = 0
    expected_target_revision: int = 1


@dataclass
class _AcceptResult:
    proposal_id: str
    accepted: bool = True


class _FakePreferenceApi:
    id_source = SimpleNamespace(new_id=lambda prefix: f"{prefix}_fake")

    def __init__(self):
        self.accepted = False

    def preview_preference_proposal(self, proposal_id, *, edit=None):
        return _Preview(proposal={"proposal_id": proposal_id, "edit": edit})

    def accept_preference_proposal(self, proposal_id, **kwargs):
        self.accepted = True
        return _AcceptResult(proposal_id=proposal_id)


def _patch_state_services(monkeypatch):
    from morrow.interfaces import cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "_state_services",
        lambda **_kwargs: (None, "handle", _FakePreferenceApi(), None, None),
    )
    monkeypatch.setattr(cli_module, "_close_state", lambda _handle: None)


def test_preference_accept_json_requires_yes(monkeypatch):
    _patch_state_services(monkeypatch)

    result = CliRunner().invoke(
        app,
        ["preferences", "inbox", "accept", "prop_1", "--json", "--workspace-id", "ws_1"],
    )

    assert result.exit_code == 2
    assert "--yes" in result.output


def test_preference_accept_json_yes_emits_single_json_document(monkeypatch):
    _patch_state_services(monkeypatch)

    result = CliRunner().invoke(
        app,
        [
            "preferences",
            "inbox",
            "accept",
            "prop_1",
            "--json",
            "--yes",
            "--workspace-id",
            "ws_1",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload == {"accepted": True, "proposal_id": "prop_1"}


def test_preference_accept_non_json_keeps_preview_and_prompt(monkeypatch):
    _patch_state_services(monkeypatch)

    result = CliRunner().invoke(
        app,
        ["preferences", "inbox", "accept", "prop_1", "--yes", "--workspace-id", "ws_1"],
        input="y\n",
    )

    assert result.exit_code == 0, result.output
    assert "expected_row_version" in result.output


def test_workflow_source_rejects_oversized_file(tmp_path):
    oversized = tmp_path / "definition.yaml"
    oversized.write_bytes(b"x" * (workflow_cli._MAX_INPUT_CHARS + 1))
    model = SimpleNamespace(model_validate=lambda raw: raw)

    with pytest.raises(ValueError, match="limit"):
        workflow_cli._source(oversized, model)


def test_workflow_run_rejects_oversized_stdin(tmp_path, monkeypatch):
    monkeypatch.setattr(
        workflow_cli,
        "_session_management",
        lambda *args, **kwargs: pytest.fail("session management must not be reached"),
    )

    result = CliRunner().invoke(
        app,
        [
            "workflow",
            "run",
            "definition-1",
            "--revision",
            "rev_1",
            "--session",
            "ses_1",
            "--root-task",
            "task_1",
            "--expected-task-version",
            "1",
            "--stdin",
            "--workspace-id",
            "ws_1",
        ],
        input="x" * (workflow_cli._MAX_INPUT_CHARS + 1),
    )

    assert result.exit_code == 2
    assert "limit" in result.output
    assert "Traceback" not in result.output


def _approval(approval_id: str, created_at: datetime):
    return SimpleNamespace(
        approval_id=approval_id,
        tool_execution_id=f"exec_{approval_id}",
        requested_scope=None,
        granted_scope=None,
        preview=(),
        resolution=ApprovalResolution.PENDING,
        created_at=created_at,
        expires_at=created_at,
        resolved_at=None,
        row_version=1,
    )


def _execution(session_id: str, execution_id: str, created_at: datetime):
    return SimpleNamespace(
        tool_execution_id=execution_id,
        session_id=session_id,
        task_run_id=f"task_{session_id}",
        agent_run_id=f"run_{session_id}",
        tool_name="demo_tool",
        state=ToolExecutionState.AWAITING_APPROVAL,
        isolation=None,
        intent=SimpleNamespace(
            effect_class=EffectClass.BOUNDED_READ,
            redacted_arguments={},
            file_evidence=(),
            config_evidence=None,
        ),
        created_at=created_at,
    )


class _FakeApprovalJournal:
    def __init__(self, sessions):
        self._sessions = sessions  # [(session_id, [executions])] ascending by creation time
        self.fetched_sessions: list[str] = []

    def list_sessions(self, _workspace_id):
        return [SimpleNamespace(session_id=session_id) for session_id, _ in self._sessions]

    def list_session_executions(self, _workspace_id, session_id):
        self.fetched_sessions.append(session_id)
        return dict(self._sessions)[session_id]

    def get_approval_for_execution(self, _workspace_id, tool_execution_id):
        return self._approvals[tool_execution_id]

    def get_agent_run(self, _workspace_id, _agent_run_id):
        return None

    @classmethod
    def with_pending_approvals(cls, per_session: int, session_count: int):
        sessions = []
        approvals = {}
        counter = 0
        for session_index in range(session_count):
            session_id = f"ses_{session_index}"
            executions = []
            for _ in range(per_session):
                counter += 1
                created = datetime(2026, 1, counter, tzinfo=UTC)
                execution = _execution(session_id, f"exec_{counter}", created)
                executions.append(execution)
                approvals[execution.tool_execution_id] = _approval(f"app_{counter}", created)
            sessions.append((session_id, executions))
        journal = cls(sessions)
        journal._approvals = approvals
        return journal


def test_list_approvals_limit_stops_traversal_early():
    journal = _FakeApprovalJournal.with_pending_approvals(per_session=2, session_count=3)

    rows = approval_cli._list_approvals(journal, "ws_1", pending_only=True, limit=2)

    assert [row["approval_id"] for row in rows] == ["app_5", "app_6"]
    assert journal.fetched_sessions == ["ses_2"]
    created = [row["created_at"] for row in rows]
    assert created == sorted(created)


def test_list_approvals_without_limit_returns_everything():
    journal = _FakeApprovalJournal.with_pending_approvals(per_session=2, session_count=3)

    rows = approval_cli._list_approvals(journal, "ws_1", pending_only=True, limit=50)

    assert len(rows) == 6


def test_list_approvals_limit_option_is_exposed():
    result = CliRunner().invoke(app, ["approval", "list", "--help"])

    assert result.exit_code == 0
    assert "--limit" in result.output


@pytest.mark.parametrize("bad_port", ["65536", "-1"])
def test_serve_and_gui_reject_out_of_range_port(bad_port):
    runner = CliRunner()

    serve = runner.invoke(app, ["serve", "--port", bad_port])
    assert serve.exit_code == 2
    assert "Invalid value" in serve.output
    assert "Traceback" not in serve.output

    gui = runner.invoke(app, ["gui", "--port", bad_port, "--no-browser"])
    assert gui.exit_code == 2
    assert "Invalid value" in gui.output
    assert "Traceback" not in gui.output
