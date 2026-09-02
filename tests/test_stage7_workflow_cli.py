"""Focused command-shape and write-free validation proofs for the Stage 7 CLI."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
import yaml
from typer.testing import CliRunner

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.bootstrap import build_application
from morrow.core.agent_definitions import AgentDefinitionSource
from morrow.core.agent_runs import AgentDefinitionRef
from morrow.core.models import ModelRef, ProviderConfig, ProviderModelConfig
from morrow.core.store import StoreOpenMode
from morrow.core.workflows.contracts import NodeOutputRef, OutputContract, TaskContract
from morrow.core.workflows.definitions import (
    AgentNodeSource,
    WorkflowBudget,
    WorkflowDefinitionSource,
    WorkflowEdge,
)
from morrow.core.workflows.runs import WorkflowStatus
from morrow.interfaces.cli import app


def configured_workspace(tmp_path):
    state_root = tmp_path / "state"
    project = tmp_path / "project"
    project.mkdir()
    application = build_application(state_root=state_root)
    identity = application.workspace_service.confirm(application.workspace_service.resolve(project))
    loaded = application.global_store.load()
    application.global_store.update(
        lambda value: value.model_copy(
            update={
                "providers": {
                    "fake": ProviderConfig(
                        adapter="openai-compatible",
                        base_url="https://example.test/v1",
                        models={"m1": ProviderModelConfig(api_model_id="m1")},
                    )
                },
                "active_model": ModelRef(provider_id="fake", model_id="m1"),
            }
        ),
        expected_revision=loaded.revision,
    )
    OperationalStore(state_root).initialize().close()
    return state_root, project, identity


def test_agent_and_workflow_help_expose_the_complete_stage7_surface():
    runner = CliRunner()
    agent = runner.invoke(app, ["agent", "--help"])
    workflow = runner.invoke(app, ["workflow", "--help"])
    assert agent.exit_code == workflow.exit_code == 0
    for command in (
        "list",
        "show",
        "create",
        "edit",
        "validate",
        "publish",
        "enable",
        "disable",
        "revoke",
    ):
        assert command in agent.output
        assert command in workflow.output
    for command in ("run", "runs", "status", "resume", "abandon", "node"):
        assert command in workflow.output


def test_agent_cli_create_and_repeated_validate_do_not_publish(tmp_path):
    state_root, project, identity = configured_workspace(tmp_path)
    source = AgentDefinitionSource(
        definition_id="helper",
        name="Helper",
        role_prompt="Inspect authorization behavior.",
        model_selection=ModelRef(provider_id="fake", model_id="m1"),
    )
    path = tmp_path / "agent.yaml"
    path.write_text(yaml.safe_dump(source.model_dump(mode="json")), encoding="utf-8")
    runner = CliRunner()
    common = ["--dir", str(project), "--state-root", str(state_root)]
    created = runner.invoke(
        app,
        ["agent", "create", "--file", str(path), "--expected-revision", "0", *common],
    )
    assert created.exit_code == 0, created.output
    first = runner.invoke(app, ["agent", "validate", "helper", *common])
    second = runner.invoke(app, ["agent", "validate", "helper", *common])
    assert first.exit_code == second.exit_code == 0
    with OperationalStore(state_root).open(StoreOpenMode.READ_ONLY) as handle:
        journal = SqliteOperationalJournal(handle)
        assert journal.agent_definitions.list_versions(identity.workspace_id) == ()


def test_agent_cli_builtin_is_visible_but_unpublished(tmp_path):
    state_root, project, _identity = configured_workspace(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "agent",
            "list",
            "--dir",
            str(project),
            "--state-root",
            str(state_root),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    direct = next(item for item in payload if item["definition_id"] == "builtin_direct")
    assert direct["head"] is None
    assert direct["desired_ahead_of_published"] is True

    workflows = CliRunner().invoke(
        app,
        [
            "workflow",
            "list",
            "--dir",
            str(project),
            "--state-root",
            str(state_root),
        ],
    )
    assert workflows.exit_code == 0, workflows.output
    listed = json.loads(workflows.output)
    assert {item["workflow_definition_id"] for item in listed} == {
        "builtin_direct_workflow",
        "builtin_explore_implement_verify",
        "builtin_parallel_research",
        "builtin_planned_refactor",
    }
    assert all(item["head"] is None for item in listed)


def test_workflow_cli_permission_mode_selects_truthful_builtin_contract(tmp_path, monkeypatch):
    state_root, project, _identity = configured_workspace(tmp_path)
    monkeypatch.setattr(
        "morrow.interfaces.workflow_cli.default_sandbox_backend",
        lambda: SimpleNamespace(probe=lambda: SimpleNamespace(supported=True, reason="available")),
    )
    common = ["--dir", str(project), "--state-root", str(state_root)]
    host = CliRunner().invoke(app, ["workflow", "list", *common])
    sandboxed = CliRunner().invoke(
        app,
        ["workflow", "--permission-mode", "auto-sandboxed", "list", *common],
    )
    assert host.exit_code == sandboxed.exit_code == 0

    def coder_contract(result):
        values = json.loads(result.output)
        template = next(
            item
            for item in values
            if item["workflow_definition_id"] == "builtin_explore_implement_verify"
        )
        coder = next(item for item in template["source"]["nodes"] if item["node_id"] == "coder")
        return coder["output_contracts"][0]["kind"]

    assert coder_contract(host) == "TextResult"
    assert coder_contract(sandboxed) == "ImplementationPatch"


def test_workflow_run_revision_and_ensure_published_are_unambiguous(tmp_path):
    state_root, project, _identity = configured_workspace(tmp_path)
    common = [
        "workflow",
        "run",
        "pipeline",
        "--session",
        "ses_missing",
        "--root-task",
        "task_missing",
        "--expected-task-version",
        "1",
        "--task",
        "inspect",
        "--dir",
        str(project),
        "--state-root",
        str(state_root),
    ]
    runner = CliRunner()
    missing = runner.invoke(app, common)
    ambiguous = runner.invoke(
        app,
        [
            *common,
            "--revision",
            "wrev_old",
            "--ensure-published",
            "--expected-head-revision",
            "0",
        ],
    )
    assert missing.exit_code == ambiguous.exit_code == 2
    assert "requires an exact --revision" in missing.output
    assert "omit --revision" in ambiguous.output


def test_workflow_validate_serializes_nonempty_diagnostics(tmp_path):
    state_root, project, _identity = configured_workspace(tmp_path)
    source = WorkflowDefinitionSource(
        workflow_definition_id="invalid_pipeline",
        name="Invalid pipeline",
        default_budget=WorkflowBudget(
            max_agent_generation_requests=3,
            default_node_max_agent_generation_requests=3,
            admission_timeout_seconds=30,
            max_concurrency=1,
        ),
        nodes=(
            AgentNodeSource(
                node_id="worker",
                agent_definition_ref=AgentDefinitionRef(
                    definition_id="missing",
                    version_id="adev_missing",
                    content_hash="a" * 64,
                ),
                task_contract=TaskContract(objective="Inspect"),
                output_contracts=(OutputContract(slot="result"),),
                access_mode="read",
            ),
        ),
        required_outputs=(NodeOutputRef(node_id="worker", output_slot="result"),),
    )
    path = tmp_path / "workflow.yaml"
    path.write_text(yaml.safe_dump(source.model_dump(mode="json")), encoding="utf-8")
    common = ["--dir", str(project), "--state-root", str(state_root)]
    runner = CliRunner()

    created = runner.invoke(
        app,
        ["workflow", "create", "--file", str(path), "--expected-revision", "0", *common],
    )
    validated = runner.invoke(app, ["workflow", "validate", "invalid_pipeline", *common])

    assert created.exit_code == 0, created.output
    assert validated.exit_code == 0, validated.output
    payload = json.loads(validated.output)
    assert payload["candidate"] is None
    assert payload["diagnostics"][0]["severity"] == "error"
    assert payload["diagnostics"][0]["code"] == "agent_version_unresolved"


def test_workflow_publish_with_warning_reports_success_after_commit(tmp_path):
    state_root, project, _identity = configured_workspace(tmp_path)
    runner = CliRunner()
    common = ["--dir", str(project), "--state-root", str(state_root)]
    agent = AgentDefinitionSource(
        definition_id="worker",
        name="Worker",
        role_prompt="Inspect the requested files.",
        model_selection=ModelRef(provider_id="fake", model_id="m1"),
    )
    agent_path = tmp_path / "agent.yaml"
    agent_path.write_text(yaml.safe_dump(agent.model_dump(mode="json")), encoding="utf-8")
    created_agent = runner.invoke(
        app,
        ["agent", "create", "--file", str(agent_path), "--expected-revision", "0", *common],
    )
    published_agent = runner.invoke(
        app,
        [
            "agent",
            "publish",
            "worker",
            "--expected-head-revision",
            "0",
            "--command-id",
            "cmd_agent",
            *common,
        ],
    )
    assert created_agent.exit_code == published_agent.exit_code == 0
    version = json.loads(published_agent.output)
    ref = AgentDefinitionRef(
        definition_id="worker",
        version_id=version["version_id"],
        content_hash=version["content_hash"],
    )
    workflow = WorkflowDefinitionSource(
        workflow_definition_id="warning_pipeline",
        name="Warning pipeline",
        default_budget=WorkflowBudget(
            max_agent_generation_requests=6,
            default_node_max_agent_generation_requests=3,
            admission_timeout_seconds=30,
            max_concurrency=1,
        ),
        nodes=(
            AgentNodeSource(
                node_id="unconsumed",
                agent_definition_ref=ref,
                task_contract=TaskContract(objective="Inspect"),
                output_contracts=(OutputContract(slot="notes"),),
                access_mode="read",
            ),
            AgentNodeSource(
                node_id="final",
                agent_definition_ref=ref,
                task_contract=TaskContract(objective="Summarize"),
                output_contracts=(OutputContract(slot="result"),),
                access_mode="read",
            ),
        ),
        edges=(WorkflowEdge(from_node_id="unconsumed", to_node_id="final"),),
        required_outputs=(NodeOutputRef(node_id="final", output_slot="result"),),
    )
    workflow_path = tmp_path / "workflow-warning.yaml"
    workflow_path.write_text(yaml.safe_dump(workflow.model_dump(mode="json")), encoding="utf-8")
    created_workflow = runner.invoke(
        app,
        [
            "workflow",
            "create",
            "--file",
            str(workflow_path),
            "--expected-revision",
            "0",
            *common,
        ],
    )
    published_workflow = runner.invoke(
        app,
        [
            "workflow",
            "publish",
            "warning_pipeline",
            "--expected-head-revision",
            "0",
            "--command-id",
            "cmd_workflow",
            *common,
        ],
    )

    assert created_workflow.exit_code == 0, created_workflow.output
    assert published_workflow.exit_code == 0, published_workflow.output
    payload = json.loads(published_workflow.output)
    assert payload["created"] is True
    assert payload["diagnostics"][0]["severity"] == "warning"
    assert payload["diagnostics"][0]["code"] == "unconsumed_outputs"
    shown = runner.invoke(app, ["workflow", "show", "warning_pipeline", *common])
    assert (
        json.loads(shown.output)["head"]["workflow_revision_id"]
        == payload["revision"]["workflow_revision_id"]
    )


@dataclass(frozen=True)
class _FakeStartedRun:
    workflow_run_id: str = "wrun_visible"


@dataclass(frozen=True)
class _FakeStartResult:
    run: _FakeStartedRun = _FakeStartedRun()


@dataclass(frozen=True)
class _FakeTerminalRun:
    status: WorkflowStatus


@dataclass(frozen=True)
class _FakeForegroundResult:
    workflow_revision_id: str
    published: bool
    run: _FakeTerminalRun


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    [
        (WorkflowStatus.COMPLETED, 0),
        (WorkflowStatus.FAILED, 1),
        (WorkflowStatus.CANCELLED, 1),
        (WorkflowStatus.BLOCKED, 1),
    ],
)
def test_workflow_run_prints_durable_id_early_and_maps_terminal_exit(
    tmp_path, monkeypatch, status, expected_exit
):
    project = tmp_path / "project"
    project.mkdir()

    class Management:
        def requires_client_message(self, *_args, **_kwargs):
            return False

        def start_foreground(self, _command):
            return _FakeStartResult()

        async def drive_foreground(self, _started):
            return _FakeForegroundResult(
                workflow_revision_id="wrev_exact",
                published=False,
                run=_FakeTerminalRun(status=status),
            )

    products = SimpleNamespace(
        workflow_management=Management(),
        persistence=SimpleNamespace(store_session=SimpleNamespace(close=lambda: None)),
    )
    monkeypatch.setattr(
        "morrow.interfaces.workflow_cli._session_management", lambda *_args: products
    )
    result = CliRunner().invoke(
        app,
        [
            "workflow",
            "run",
            "pipeline",
            "--revision",
            "wrev_exact",
            "--session",
            "ses_one",
            "--root-task",
            "task_one",
            "--expected-task-version",
            "1",
            "--task",
            "inspect",
            "--command-id",
            "cmd_one",
            "--dir",
            str(project),
        ],
    )

    assert result.exit_code == expected_exit, result.output
    lines = result.output.splitlines()
    assert lines[0] == "command_id: cmd_one"
    assert lines[1] == "workflow_run_id: wrun_visible"
    assert json.loads(lines[2])["run"]["status"] == status.value
