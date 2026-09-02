"""Focused command-shape and write-free validation proofs for the Stage 7 CLI."""

from __future__ import annotations

import json

import yaml
from typer.testing import CliRunner

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.bootstrap import build_application
from morrow.core.agent_definitions import AgentDefinitionSource
from morrow.core.models import ModelRef, ProviderConfig, ProviderModelConfig
from morrow.core.store import StoreOpenMode
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
    for command in ("run", "status", "resume", "abandon", "node"):
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
