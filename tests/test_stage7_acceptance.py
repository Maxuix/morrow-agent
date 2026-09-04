"""Integrated Stage 7 acceptance and deterministic comparison evidence.

The matrix points at the focused owner tests instead of duplicating their setup.  The two
executable comparisons below use only ScriptedModelProvider instances and an injected clock; no
Live Provider, network access, credential, or wall-clock timing is involved.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from morrow.bootstrap import build_application, build_session_application
from morrow.core.models import ModelRef
from morrow.core.workflows.runs import WorkflowStatus
from morrow.interfaces import terminal as terminal_module
from morrow.testing import ScriptedModelProvider
from test_stage7_direct_adapter import publish_direct, start_direct
from test_stage7_isolated_workflow_slice import CONTRACT, SliceFixture
from test_stage7_serial_scheduler import WS, DagFixture, pair_source, publish, start

ROOT = Path(__file__).resolve().parents[1]

# Each Stage 7 closeout claim has one focused owner selector.  The final offline gate executes all
# of them; the contract test below additionally prevents documentation from drifting to stale or
# renamed evidence.
ACCEPTANCE_MATRIX = {
    "ordinary_direct": (
        "tests/acceptance/test_s7p08_single_agent_matrix.py::"
        "test_interactive_and_headless_paths_share_run_preparation_and_terminal_truth"
    ),
    "isolated_slice_and_outcome_ordering": (
        "tests/test_stage7_isolated_workflow_slice.py::test_isolated_slice_completes_end_to_end"
    ),
    "serial_dag_artifact_handoff": (
        "tests/test_stage7_serial_scheduler.py::"
        "test_chain_runs_in_topological_order_with_typed_artifact_handoff"
    ),
    "explorer_coder_reviewer": (
        "tests/test_stage7_multi_agent_pipeline.py::"
        "test_explore_implement_verify_succeeds_and_isolates_sessions"
    ),
    "serial_fan_in_and_planned_refactor": (
        "tests/test_stage7_workflow_management.py::"
        "test_two_builtin_suggestions_publish_through_generic_compiler"
    ),
    "direct_invoking_session": (
        "tests/test_stage7_direct_adapter.py::"
        "test_direct_adapter_reuses_root_and_finalizes_after_turn"
    ),
    "cancellation": (
        "tests/test_stage7_serial_scheduler.py::"
        "test_cancel_during_later_node_preserves_completed_nodes"
    ),
    "late_failure_and_completed_evidence": (
        "tests/test_stage7_serial_scheduler.py::"
        "test_provider_failure_in_later_node_preserves_completed_work"
    ),
    "blocked_recovery": (
        "tests/test_stage7_serial_scheduler.py::"
        "test_crash_unknown_tool_outcome_blocks_then_resumes_remaining_nodes"
    ),
    "emergency_revocation": (
        "tests/test_stage7_serial_scheduler.py::"
        "test_revocation_before_later_admission_closes_policy_revoked"
    ),
    "ordinary_disable_after_admission": (
        "tests/test_stage7_serial_scheduler.py::"
        "test_ordinary_disable_mid_run_never_reaches_the_admitted_run"
    ),
    "revision_drift": (
        "tests/test_stage7_isolated_workflow_slice.py::"
        "test_published_revision_keeps_its_frozen_model_after_active_switch"
    ),
    "definition_error_isolation": (
        "tests/test_stage7_agent_definitions.py::"
        "test_factory_required_backend_unavailable_is_preparation_local"
    ),
    "pure_validate_and_explicit_publish": (
        "tests/test_stage7_workflow_management.py::"
        "test_management_create_update_validate_is_write_free_and_publish_is_explicit"
    ),
    "tool_requirement_precedence": (
        "tests/test_stage7_workflow_compiler.py::"
        "test_tool_merge_forbidden_over_required_conflict_is_an_error"
    ),
    "durable_structured_submission": (
        "tests/test_stage7_multi_agent_pipeline.py::"
        "test_schema_violation_then_correction_and_conflicting_submission"
    ),
    "missing_submission": (
        "tests/test_stage7_multi_agent_pipeline.py::"
        "test_missing_structured_submission_closes_output_contract_unsatisfied"
    ),
    "needs_revision_truth": (
        "tests/test_stage7_multi_agent_pipeline.py::"
        "test_blocking_review_is_needs_revision_and_still_runs_pending_node"
    ),
    "graph_connectivity": (
        "tests/test_stage7_workflow_compiler.py::"
        "test_disconnected_component_error_and_control_edge_fix"
    ),
    "connected_unconsumed_node": (
        "tests/test_stage7_serial_scheduler.py::"
        "test_unconsumed_warning_node_still_executes_and_can_fail_the_workflow"
    ),
    "source_and_database_backup": (
        "tests/test_stage7_workflow_store.py::"
        "test_profile_roundtrip_backup_malformed_source_and_doctor"
    ),
    "deadline_freeze": (
        "tests/test_stage7_isolated_workflow_slice.py::"
        "test_deadline_exceeded_before_admission_fails_run"
    ),
    "aggregate_budget": (
        "tests/test_stage7_serial_scheduler.py::"
        "test_positive_remainder_runs_later_node_under_shrunken_cap"
    ),
    "current_ready_evidence_only": (
        "tests/test_stage7_isolated_workflow_slice.py::"
        "test_acceptance_selects_only_the_current_ready_snapshot"
    ),
    "stale_workflow_evidence_rejected": (
        "tests/test_stage7_isolated_workflow_slice.py::"
        "test_stale_workflow_refs_never_leak_after_ordinary_direct_turn"
    ),
    "durable_change_capture": (
        "tests/test_stage7_multi_agent_pipeline.py::"
        "test_write_capture_publishes_complete_diff_and_replays"
    ),
    "value_sensitive_root_projection": (
        "tests/test_stage7_isolated_workflow_slice.py::"
        "test_secret_shaped_output_is_redacted_without_blocking_terminal"
    ),
    "management_cli": (
        "tests/test_stage7_workflow_cli.py::"
        "test_agent_and_workflow_help_expose_the_complete_stage7_surface"
    ),
}


def test_acceptance_matrix_has_live_unique_owner_selectors() -> None:
    assert len(ACCEPTANCE_MATRIX) == 28
    selectors = tuple(ACCEPTANCE_MATRIX.values())
    assert len(selectors) == len(set(selectors))
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *selectors],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "28 tests collected" in completed.stdout


@pytest.mark.asyncio
async def test_paired_ordinary_direct_and_opt_in_direct_workflow(tmp_path: Path) -> None:
    """Both Direct paths complete the same task with one admitted generation request."""

    application = build_application(state_root=tmp_path / "direct-state")
    workspace = tmp_path / "direct-workspace"
    workspace.mkdir()
    identity = application.workspace_service.confirm(
        application.workspace_service.resolve(workspace)
    )
    provider = ScriptedModelProvider(["paired result"])
    direct = build_session_application(
        application,
        identity,
        provider=provider,
        model=ModelRef(provider_id="scripted", model_id="test-model"),
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
    direct_result = await terminal_module._consume_dispatch(
        direct.orchestrator, CONTRACT.objective, sink
    )
    assert not direct_result.degraded
    assert len(provider.stream_calls) == 1
    assert sink.events[-1].type == "turn.completed"

    workflow = SliceFixture(tmp_path / "workflow")
    try:
        workflow.bank.scripts.append([["paired result"]])
        _, revision = publish_direct(workflow)
        run = await workflow.runtime.scheduler.run(
            start_direct(workflow, revision).run.workflow_run_id
        )
        view = workflow.runtime.queries.get_run_view(run.workflow_run_id)
        assert run.status is WorkflowStatus.COMPLETED
        assert run.result_status == "succeeded"
        assert view is not None
        assert view.agent_generation_request_count == 1
        assert view.usage_availability == "unavailable"
        assert len(workflow.bank.providers[0].stream_calls) == 1
    finally:
        workflow.close()


@pytest.mark.asyncio
async def test_late_failure_full_rerun_cost_is_explicit(tmp_path: Path) -> None:
    """A new run after late failure pays for both nodes again; failed runs never resume in place."""

    fixture = DagFixture(
        tmp_path,
        scripts=(
            [["upstream first"]],
            [RuntimeError("late failure")],
            [["upstream again"]],
            [["downstream succeeds"]],
        ),
    )
    try:
        _, publication = publish(fixture, pair_source)
        first = await fixture.runtime.scheduler.run(
            start(fixture, publication.revision).run.workflow_run_id
        )
        assert first.status is WorkflowStatus.FAILED
        first_requests = fixture.journal.count_workflow_agent_requests(WS, first.workflow_run_id)

        fixture.tasks.resume("task_root", command_id="cmd_acceptance_resume")
        second = await fixture.runtime.scheduler.run(
            start(
                fixture,
                publication.revision,
                command_id="cmd_acceptance_second_run",
            ).run.workflow_run_id
        )
        second_requests = fixture.journal.count_workflow_agent_requests(WS, second.workflow_run_id)

        assert second.status is WorkflowStatus.COMPLETED
        assert second.workflow_run_id != first.workflow_run_id
        # The late node exhausts the existing three-attempt Provider retry chain.  A new
        # WorkflowRun then pays for the already-successful upstream node again.
        assert (first_requests, second_requests) == (4, 2)
        assert len(fixture.bank.providers) == 4
    finally:
        fixture.close()
