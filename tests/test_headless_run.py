"""Focused S7P-01 tests for the non-interactive JSONL entrypoint."""

from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner

from morrow.application.orchestrator import DispatchResult
from morrow.core.models import AgentEvent, WorkspaceIdentity, WorkspaceResolution
from morrow.interfaces import cli as cli_module
from morrow.interfaces.cli import app
from morrow.services.workspace import DataRoot


def test_run_emits_only_versioned_jsonl_and_terminal_safe_record(monkeypatch, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    state_root = tmp_path / "state"
    emitted = []

    class FakeOrchestrator:
        async def stream(self, prompt):
            emitted.append(prompt)
            yield AgentEvent(
                type="turn.started",
                event_id="evt_1",
                session_id="ses_1",
                turn_id="turn_1",
                sequence=1,
            )
            yield AgentEvent(
                type="turn.completed",
                event_id="evt_2",
                session_id="ses_1",
                turn_id="turn_1",
                sequence=2,
                payload={"finish_reason": "stop", "text": "done"},
            )
            yield DispatchResult()

    class FakeApi:
        def get_agent_run_observation(self, agent_run_id):
            assert agent_run_id == "arun_1"
            return SimpleNamespace(
                model_dump=lambda mode="json": {
                    "agent_run_id": agent_run_id,
                    "terminal_metrics": {
                        "finish_reason": "stop",
                        "usage": {"availability": "unavailable"},
                    },
                }
            )

    fake_session_app = SimpleNamespace(
        orchestrator=FakeOrchestrator(),
        session=SimpleNamespace(
            session_id="ses_1",
            committer=SimpleNamespace(current_task_run_id="task_1", current_agent_run_id="arun_1"),
        ),
        persistence=SimpleNamespace(current_task_run_id="task_1", current_agent_run_id="arun_1"),
        api=FakeApi(),
    )

    class FakeWorkspaceService:
        def resolve(self, path):
            return WorkspaceResolution(
                status="existing",
                identity=WorkspaceIdentity(
                    workspace_id="ws_1", path=str(path), display_name="project"
                ),
            )

    fake_application = SimpleNamespace(
        data_root=DataRoot(state_root),
        workspace_service=FakeWorkspaceService(),
    )
    monkeypatch.setattr(cli_module, "build_application", lambda **_: fake_application)

    def build_session(**kwargs):
        assert kwargs["approval_port"].__class__.__name__ == "HeadlessApprovalPort"
        return fake_session_app

    monkeypatch.setattr(cli_module, "build_session_application", build_session)

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--workspace",
            str(workspace),
            "--prompt",
            "say hello",
            "--state-root",
            str(state_root),
        ],
    )

    assert result.exit_code == 0, result.output
    assert emitted == ["say hello"]
    records = [json.loads(line) for line in result.output.splitlines()]
    assert [record["kind"] for record in records] == ["agent_event", "agent_event", "run.completed"]
    assert all(record["schema_version"] == 1 for record in records)
    assert records[-1]["agent_run_id"] == "arun_1"
    assert records[-1]["metrics"]["usage"]["availability"] == "unavailable"
    assert "Traceback" not in result.output


def test_run_requires_explicit_prompt_and_workspace_without_reading_stdin(tmp_path):
    result = CliRunner().invoke(
        app, ["run", "--state-root", str(tmp_path / "state")], input="unexpected\n"
    )
    assert result.exit_code != 0
    assert "prompt" in result.output.casefold() or "workspace" in result.output.casefold()
    assert "unexpected" not in result.output
