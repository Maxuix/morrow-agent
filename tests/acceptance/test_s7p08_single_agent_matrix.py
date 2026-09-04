from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from morrow.bootstrap import build_application, build_session_application
from morrow.core.models import ModelRef
from morrow.interfaces import cli as cli_module
from morrow.interfaces import terminal as terminal_module
from morrow.testing import ScriptedModelProvider

ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = Path(__file__).with_name("s7p08_single_agent_matrix.json")
EXPECTED_CELL_IDS = {
    "chat",
    "workspace_discovery",
    "structured_mutation",
    "shell",
    "sandbox_approval",
    "git_read_only",
    "validation_truth",
    "provider_model",
    "session_task",
    "persistence_artifact",
    "context",
    "profile_memory",
    "skill",
    "mcp",
    "permission_grant",
    "runtime_control",
    "security",
    "entrypoint_consistency",
}
HIGH_RISK_IDS = {
    "chat",
    "structured_mutation",
    "shell",
    "sandbox_approval",
    "validation_truth",
    "provider_model",
    "session_task",
    "persistence_artifact",
    "context",
    "profile_memory",
    "skill",
    "mcp",
    "permission_grant",
    "runtime_control",
    "security",
    "entrypoint_consistency",
}
SNAPSHOT_CLASSES = {
    "provider_model",
    "skill",
    "mcp",
    "preference",
    "knowledge",
    "toolset",
    "permission",
    "context_policy",
}


def _load_ledger() -> dict:
    return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))


def _selectors(cell: dict) -> list[str]:
    return [
        *cell["positive_selectors"],
        *cell["failure_selectors"],
        *cell["recovery_selectors"],
    ]


def test_s7p08_ledger_contract_is_complete_and_strict() -> None:
    ledger = _load_ledger()
    cells = ledger["cells"]
    cell_ids = [cell["id"] for cell in cells]

    assert ledger["schema_version"] == 1
    assert len(cells) == 18
    assert len(cell_ids) == len(set(cell_ids))
    assert set(cell_ids) == EXPECTED_CELL_IDS
    assert set(ledger["required_snapshot_classes"]) == SNAPSHOT_CLASSES

    referenced_snapshot_classes: set[str] = set()
    all_selectors: list[str] = []
    for cell in cells:
        assert cell["classification"] == "covered"
        assert cell["risk"] in {"medium", "high"}
        assert cell["positive_selectors"]
        assert cell["acceptance_sources"]
        assert cell["last_execution"]["status"] == "passed"
        assert cell["last_execution"]["command"] == "focused-ledger-selectors"
        if cell["id"] in HIGH_RISK_IDS:
            assert cell["risk"] == "high"
            assert cell["failure_selectors"]
            assert cell["recovery_selectors"]
        if cell["id"] == "sandbox_approval":
            assert cell["platform_conditions"]
        referenced_snapshot_classes.update(cell["snapshot_classes"])
        all_selectors.extend(_selectors(cell))
        for source in cell["acceptance_sources"]:
            assert (ROOT / source).is_file(), source
        for historical in cell["superseded_selectors"]:
            assert historical["selector"]
            assert historical["replacement"]
            assert historical["reason"]

    assert referenced_snapshot_classes == SNAPSHOT_CLASSES
    assert len(all_selectors) == len(set(all_selectors)), "selectors must have one owning cell"


def test_s7p08_ledger_selectors_collect_in_the_current_tree() -> None:
    ledger = _load_ledger()
    selectors = [selector for cell in ledger["cells"] for selector in _selectors(cell)]
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *selectors],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        # Collection imports many application modules in a fresh interpreter.
        # This is a validity check, not a startup performance benchmark.
        timeout=120,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "no tests collected" not in completed.stdout


@pytest.mark.asyncio
async def test_interactive_and_headless_paths_share_run_preparation_and_terminal_truth(
    tmp_path: Path,
) -> None:
    application = build_application(state_root=tmp_path / "state")
    workspace = tmp_path / "project"
    workspace.mkdir()
    identity = application.workspace_service.confirm(
        application.workspace_service.resolve(workspace)
    )
    model = ModelRef(provider_id="scripted", model_id="test-model")
    interactive = build_session_application(
        application,
        identity,
        provider=ScriptedModelProvider(["done"]),
        model=model,
    )
    headless = build_session_application(
        application,
        identity,
        provider=ScriptedModelProvider(["done"]),
        model=model,
    )

    class SinkTerminal:
        def __init__(self) -> None:
            self.events = []
            self.console = SimpleNamespace(print=lambda *_args, **_kwargs: None)

        def show_event(self, event) -> None:
            self.events.append(event)

        def show_run_summary(self, _session) -> None:
            return None

    sink = SinkTerminal()
    interactive_result = await terminal_module._consume_dispatch(
        interactive.orchestrator, "same prompt", sink
    )
    headless_terminal, headless_result = await cli_module._headless_stream(headless, "same prompt")

    assert not interactive_result.degraded
    assert headless_result is not None and not headless_result.degraded
    assert sink.events[-1].type == "turn.completed"
    assert sink.events[-1].payload["finish_reason"] == "stop"
    assert headless_terminal is not None
    assert headless_terminal.type == "turn.completed"
    assert headless_terminal.payload["finish_reason"] == "stop"
    assert type(interactive.orchestrator.runtime.loop) is type(headless.orchestrator.runtime.loop)

    interactive_snapshot = interactive.persistence.get_open_run_snapshot()
    headless_snapshot = headless.persistence.get_open_run_snapshot()
    assert interactive_snapshot is not None
    assert headless_snapshot is not None
    assert interactive_snapshot.model == headless_snapshot.model == model
    assert interactive_snapshot.provider_runtime is not None
    assert interactive_snapshot.run_policy is not None
    assert interactive_snapshot.preference_projection_digest is not None
    assert interactive_snapshot.skill_catalog_digest is not None
    assert interactive_snapshot.skill_binding_digest is not None
    assert interactive_snapshot.prompt_profile_digest is not None
    assert interactive_snapshot.provider_runtime == headless_snapshot.provider_runtime
    assert interactive_snapshot.run_policy == headless_snapshot.run_policy
    assert interactive_snapshot.run_policy_digest == headless_snapshot.run_policy_digest
    assert interactive_snapshot.tool_schema_digest == headless_snapshot.tool_schema_digest
    assert (
        interactive_snapshot.permission_profile_digest
        == headless_snapshot.permission_profile_digest
    )
    assert (
        interactive_snapshot.preference_projection_digest
        == headless_snapshot.preference_projection_digest
    )
    assert interactive_snapshot.skill_catalog_digest == headless_snapshot.skill_catalog_digest
    assert interactive_snapshot.skill_binding_digest == headless_snapshot.skill_binding_digest
    assert interactive_snapshot.prompt_profile_digest == headless_snapshot.prompt_profile_digest

    interactive_observation = interactive.api.get_agent_run_observation(
        interactive.persistence.current_agent_run_id
    )
    headless_observation = headless.api.get_agent_run_observation(
        headless.persistence.current_agent_run_id
    )
    assert interactive_observation.terminal_metrics.finish_reason == "stop"
    assert headless_observation.terminal_metrics.finish_reason == "stop"
