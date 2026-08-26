"""Focused S7P-01 tests for the non-interactive JSONL entrypoint."""

from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner

from morrow.application.orchestrator import DispatchResult
from morrow.bootstrap import build_application
from morrow.core.models import AgentEvent, ModelRef, WorkspaceIdentity, WorkspaceResolution
from morrow.interfaces import cli as cli_module
from morrow.interfaces.cli import app
from morrow.services.workspace import DataRoot
from morrow.testing import ScriptedModelProvider


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


def test_headless_dispatch_does_not_reuse_an_existing_agent_run_id(capsys):
    class ExplodingApi:
        def get_agent_run_observation(self, _agent_run_id):
            raise AssertionError("dispatch-only input must not query the previous AgentRun")

    session_app = SimpleNamespace(
        session=SimpleNamespace(
            session_id="ses_old",
            committer=SimpleNamespace(
                current_task_run_id="task_old", current_agent_run_id="arun_old"
            ),
        ),
        persistence=SimpleNamespace(
            current_task_run_id="task_old", current_agent_run_id="arun_old"
        ),
        api=ExplodingApi(),
    )

    assert not cli_module._headless_terminal_record(
        session_app, None, DispatchResult(lines=["status"])
    )
    record = json.loads(capsys.readouterr().out)
    assert record["kind"] == "run.completed"
    assert record["agent_run_id"] is None
    assert record["session_id"] is None
    assert record["task_run_id"] is None
    assert record["turn_id"] is None
    assert record["metrics"] is None


def test_run_uses_the_real_session_builder_with_a_scripted_provider(monkeypatch, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    state_root = tmp_path / "state"
    real_application = build_application(state_root=state_root)
    resolution = real_application.workspace_service.resolve(workspace)
    identity = real_application.workspace_service.confirm(resolution)
    provider = ScriptedModelProvider(["done"])
    real_application.provider_service.build_active = lambda: (
        provider,
        ModelRef(provider_id="scripted", model_id="test-model"),
    )
    monkeypatch.setattr(cli_module, "build_application", lambda **_: real_application)

    result = CliRunner().invoke(
        app,
        [
            "run",
            "say hello",
            "--workspace",
            str(workspace),
            "--state-root",
            str(state_root),
        ],
    )

    assert result.exit_code == 0, result.output
    records = [json.loads(line) for line in result.output.splitlines()]
    assert [record["kind"] for record in records] == [
        "agent_event",
        "agent_event",
        "agent_event",
        "run.completed",
    ]
    terminal = records[-1]
    assert terminal["session_id"].startswith("ses_")
    assert terminal["task_run_id"].startswith("task_")
    assert terminal["agent_run_id"].startswith("arun_")
    assert terminal["turn_id"].startswith("turn_")
    assert terminal["metrics"]["finish_reason"] == "stop"
    assert identity.workspace_id
    assert "Traceback" not in result.output


def test_run_requires_explicit_prompt_and_workspace_without_reading_stdin(tmp_path):
    result = CliRunner().invoke(
        app, ["run", "--state-root", str(tmp_path / "state")], input="unexpected\n"
    )
    assert result.exit_code != 0
    assert "prompt" in result.output.casefold() or "workspace" in result.output.casefold()
    assert "unexpected" not in result.output
