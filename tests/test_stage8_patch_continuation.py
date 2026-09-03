"""Stage 8 Subplan 2: future-only patches and continuation lineage."""

from __future__ import annotations

import pytest

from morrow.application.workflows.integrity import verify_workflow_rows
from morrow.core.application import ApplicationError
from morrow.core.domain import TaskOutcomeTrigger, TaskRunStatus
from morrow.core.workflows.contracts import ContractRef, NodeOutputRef, OutputContract
from morrow.core.workflows.definitions import WorkflowBudget
from morrow.core.workflows.patches import FutureGraphPatch
from morrow.core.workflows.runs import WorkflowStatus
from test_stage7_multi_agent_pipeline import _script_submit_then_stop
from test_stage7_serial_scheduler import (
    CATALOG,
    MODEL,
    WS,
    DagFixture,
    ScriptBank,
    node_by_id,
    pair_source,
    publish,
    root,
    start,
)


async def _paused_after_first_node(tmp_path):
    cell = {}

    def pause_at_second_preparation(count):
        if count == 2:
            cell["fx"].runtime.transitions.request_pause(cell["run_id"])

    fixture = DagFixture(tmp_path, bank=ScriptBank(on_create=pause_at_second_preparation))
    cell["fx"] = fixture
    fixture.bank.scripts.extend([["phase one"], ["unused"], ["phase three continued"]])
    _, publication = publish(fixture, pair_source)
    started = start(fixture, publication.revision)
    cell["run_id"] = started.run.workflow_run_id
    paused = await fixture.runtime.scheduler.run(cell["run_id"])
    assert paused.status is WorkflowStatus.PAUSED
    return fixture, publication.revision, paused


def _patch(parent, base, *, source, patch_id="wpatch_one"):
    return FutureGraphPatch(
        workflow_patch_id=patch_id,
        workspace_id=WS,
        parent_run_id=parent.workflow_run_id,
        base_workflow_revision_id=base.workflow_revision_id,
        expected_parent_row_version=parent.row_version,
        source=source,
        requested_by="cmd_patch",
    )


@pytest.mark.asyncio
async def test_future_patch_handoff_inherits_past_and_runs_only_execution_set(tmp_path):
    fixture, base, parent = await _paused_after_first_node(tmp_path)
    try:
        ref = base.nodes[0].agent_definition_ref
        source = pair_source(ref)
        alpha = next(item for item in source.nodes if item.node_id == "alpha")
        source = source.model_copy(
            update={
                "nodes": tuple(
                    item.model_copy(
                        update={
                            "task_contract": item.task_contract.model_copy(
                                update={"objective": "Conclude from inherited phase-one evidence"}
                            )
                        }
                    )
                    if item.node_id == alpha.node_id
                    else item
                    for item in source.nodes
                )
            }
        )
        head_before = fixture.journal.workflows.get_head(WS, "pipeline")
        applied = fixture.runtime.patches.apply(
            _patch(parent, base, source=source), active_model=MODEL
        )
        child = applied.child
        assert child is not None and child.run_relation == "continuation"
        assert child.parent_run_id == parent.workflow_run_id
        assert child.effective_lineage_budget_root_run_id == parent.workflow_run_id
        assert child.admission_deadline_at == parent.admission_deadline_at
        superseded = fixture.journal.workflows.get_run(WS, parent.workflow_run_id)
        assert superseded.status is WorkflowStatus.SUPERSEDED
        assert superseded.superseded_reason == "continued_by_patch"
        assert fixture.journal.workflows.get_head(WS, "pipeline") == head_before
        assert [
            item.node_id
            for item in fixture.journal.workflows.list_execution_nodes(WS, child.workflow_run_id)
        ] == ["alpha"]
        imports = fixture.journal.workflows.list_artifact_imports(WS, child.workflow_run_id)
        assert [(item.source_node_id, item.output_slot) for item in imports] == [
            ("gamma", "result")
        ]
        assert [
            item.node_id for item in fixture.journal.workflows.list_nodes(WS, child.workflow_run_id)
        ] == ["alpha"]

        completed = await fixture.runtime.scheduler.run(child.workflow_run_id)
        assert completed.status is WorkflowStatus.COMPLETED, fixture.journal.list_task_outcomes(
            WS, "task_root"
        )[-1].completion_basis
        assert (
            node_by_id(fixture, child.workflow_run_id, "alpha").status is WorkflowStatus.COMPLETED
        )
        assert root(fixture).status.value == "ready_for_acceptance"
        view = fixture.runtime.queries.get_run_view(child.workflow_run_id)
        assert len(view.inherited_artifacts) == 1
        assert view.lineage_agent_generation_request_count == 2
        assert fixture.handle.run_read(verify_workflow_rows) == (True, ())
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_detached_revisions_do_not_consume_published_revision_numbers(tmp_path):
    fixture, base, parent = await _paused_after_first_node(tmp_path)
    try:
        ref = base.nodes[0].agent_definition_ref
        source = pair_source(ref)
        first = fixture.runtime.patches.save(
            _patch(parent, base, source=source, patch_id="wpatch_first"), active_model=MODEL
        )
        second = fixture.runtime.patches.save(
            _patch(parent, base, source=source, patch_id="wpatch_second"), active_model=MODEL
        )
        assert (first.revision.revision, second.revision.revision) == (-1, -2)
        assert fixture.journal.workflows.list_revisions(WS) == (base,)

        alpha = next(node for node in source.nodes if node.node_id == "alpha")
        published_source = source.model_copy(
            update={
                "nodes": tuple(
                    node.model_copy(
                        update={
                            "task_contract": node.task_contract.model_copy(
                                update={"objective": "Publish a changed future objective"}
                            )
                        }
                    )
                    if node.node_id == alpha.node_id
                    else node
                    for node in source.nodes
                )
            }
        )
        publication = fixture.compiler.publish(
            published_source,
            source_revision=1,
            expected_head_revision=1,
            command_id="cmd_publish_after_detached",
            active_model=MODEL,
        )
        assert publication.revision.revision == 2
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_chained_continuation_keeps_inherited_past_immutable(tmp_path):
    fixture, base, parent = await _paused_after_first_node(tmp_path)
    try:
        ref = base.nodes[0].agent_definition_ref
        source = pair_source(ref)
        first = fixture.runtime.patches.apply(
            _patch(parent, base, source=source, patch_id="wpatch_chain_one"),
            active_model=MODEL,
        )
        paused_child = fixture.runtime.transitions.request_pause(first.child.workflow_run_id)
        assert paused_child.status is WorkflowStatus.PAUSED

        forged = source.model_copy(
            update={
                "nodes": tuple(
                    node.model_copy(
                        update={
                            "task_contract": node.task_contract.model_copy(
                                update={"objective": "Rewrite inherited ancestor evidence"}
                            )
                        }
                    )
                    if node.node_id == "gamma"
                    else node
                    for node in source.nodes
                )
            }
        )
        with pytest.raises(ApplicationError, match="past_forgery"):
            fixture.runtime.patches.validate(
                _patch(
                    paused_child,
                    first.revision,
                    source=forged,
                    patch_id="wpatch_chain_forged",
                ),
                active_model=MODEL,
            )

        second = fixture.runtime.patches.apply(
            _patch(
                paused_child,
                first.revision,
                source=source,
                patch_id="wpatch_chain_two",
            ),
            active_model=MODEL,
        )
        assert second.validation.past_node_ids == ("gamma",)
        assert second.validation.execution_node_ids == ("alpha",)
        chained_import = fixture.journal.workflows.list_artifact_imports(
            WS, second.child.workflow_run_id
        )[0]
        assert chained_import.source_workflow_run_id == parent.workflow_run_id
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_patch_rejects_past_forgery_and_stale_parent_occ(tmp_path):
    fixture, base, parent = await _paused_after_first_node(tmp_path)
    try:
        ref = base.nodes[0].agent_definition_ref
        source = pair_source(ref)
        source = source.model_copy(
            update={
                "nodes": tuple(
                    item.model_copy(
                        update={
                            "task_contract": item.task_contract.model_copy(
                                update={"objective": "Rewrite already admitted Past"}
                            )
                        }
                    )
                    if item.node_id == "gamma"
                    else item
                    for item in source.nodes
                )
            }
        )
        with pytest.raises(ApplicationError, match="past_forgery"):
            fixture.runtime.patches.validate(
                _patch(parent, base, source=source), active_model=MODEL
            )

        fixture.runtime.transitions.resume_run(parent.workflow_run_id)
        with pytest.raises(ApplicationError, match="revision conflict"):
            fixture.runtime.patches.save(
                _patch(parent, base, source=pair_source(ref), patch_id="wpatch_stale"),
                active_model=MODEL,
            )
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_handoff_fault_rolls_back_without_root_ownership_gap(tmp_path):
    fixture, base, parent = await _paused_after_first_node(tmp_path)
    try:
        ref = base.nodes[0].agent_definition_ref
        patch = _patch(parent, base, source=pair_source(ref), patch_id="wpatch_fault")
        fixture.runtime.patches.save(patch, active_model=MODEL)
        fired = False

        def fail_before_commit(point):
            nonlocal fired
            if point == "before_commit" and not fired:
                fired = True
                raise RuntimeError("injected handoff failure")

        fixture.handle._failure_injector = fail_before_commit
        with pytest.raises(RuntimeError, match="injected handoff"):
            fixture.runtime.patches.apply(patch, active_model=MODEL)
        fixture.handle._failure_injector = None
        current = fixture.journal.workflows.get_run(WS, parent.workflow_run_id)
        assert current.status is WorkflowStatus.PAUSED
        assert fixture.journal.workflows.active_for_root(WS, parent.root_task_run_id) == current
        children = [
            item
            for item in fixture.journal.workflows.list_runs(WS)
            if item.parent_run_id == parent.workflow_run_id
        ]
        assert children == []
    finally:
        fixture.handle._failure_injector = None
        fixture.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["budget", "deadline"])
async def test_patch_can_be_saved_when_continuation_admission_is_closed(tmp_path, gate):
    fixture, base, parent = await _paused_after_first_node(tmp_path)
    try:
        ref = base.nodes[0].agent_definition_ref
        source = pair_source(ref)
        if gate == "budget":
            source = source.model_copy(
                update={
                    "default_budget": WorkflowBudget(
                        max_agent_generation_requests=1,
                        default_node_max_agent_generation_requests=3,
                        admission_timeout_seconds=300,
                        max_concurrency=1,
                    )
                }
            )
        else:
            fixture.clock.advance(301)
        patch = _patch(parent, base, source=source, patch_id=f"wpatch_{gate}")
        saved = fixture.runtime.patches.save(patch, active_model=MODEL)
        assert saved.child is None
        assert (
            fixture.journal.workflows.get_revision(WS, saved.revision.workflow_revision_id)
            == saved.revision
        )
        with pytest.raises(ApplicationError, match=gate):
            fixture.runtime.patches.apply(patch, active_model=MODEL)
        assert (
            fixture.journal.workflows.get_run(WS, parent.workflow_run_id).status
            is WorkflowStatus.PAUSED
        )
        assert (
            fixture.journal.workflows.active_for_root(WS, parent.root_task_run_id).workflow_run_id
            == parent.workflow_run_id
        )
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_empty_execution_set_closes_child_atomically_from_inherited_outputs(
    tmp_path, monkeypatch
):
    fixture = DagFixture(tmp_path)
    try:
        fixture.bank.scripts.extend([["phase one"], ["phase three"]])
        _, publication = publish(fixture, pair_source)
        started = start(fixture, publication.revision)
        run_id = started.run.workflow_run_id
        # Leave the fully completed graph nonterminal to model a crash exactly
        # between last-node commit and finalization, then drain it to PAUSED.
        monkeypatch.setattr(fixture.runtime.finalizer, "finalize_success", lambda _run_id: None)
        await fixture.runtime.scheduler.run(run_id)
        fixture.runtime.transitions.request_pause(run_id)
        parent = fixture.runtime.transitions.complete_drain(run_id)
        assert parent.status is WorkflowStatus.PAUSED
        monkeypatch.undo()

        ref = publication.revision.nodes[0].agent_definition_ref
        applied = fixture.runtime.patches.apply(
            _patch(parent, publication.revision, source=pair_source(ref), patch_id="wpatch_empty"),
            active_model=MODEL,
        )
        child = applied.child
        assert child is not None and child.status is WorkflowStatus.COMPLETED
        assert child.result_status == "succeeded"
        assert fixture.journal.workflows.list_nodes(WS, child.workflow_run_id) == ()
        assert fixture.journal.workflows.list_execution_nodes(WS, child.workflow_run_id) == ()
        assert len(fixture.journal.workflows.list_artifact_imports(WS, child.workflow_run_id)) == 2
        assert root(fixture).status.value == "ready_for_acceptance"
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_empty_continuation_resolves_inherited_blocking_review_report(tmp_path):
    cell = {}

    def pause_at_second_preparation(count):
        if count == 2:
            cell["fx"].runtime.transitions.request_pause(cell["run_id"])

    fixture = DagFixture(tmp_path, bank=ScriptBank(on_create=pause_at_second_preparation))
    cell["fx"] = fixture
    try:

        def review_source(ref):
            source = pair_source(ref)
            gamma = next(node for node in source.nodes if node.node_id == "gamma")
            alpha = next(node for node in source.nodes if node.node_id == "alpha")
            gamma = gamma.model_copy(
                update={"output_contracts": (OutputContract(kind="ReviewReport", slot="review"),)}
            )
            binding = alpha.input_bindings[0]
            alpha = alpha.model_copy(
                update={
                    "input_bindings": (
                        binding.model_copy(
                            update={
                                "node_output": NodeOutputRef(node_id="gamma", output_slot="review"),
                                "accepts": ContractRef(kind="ReviewReport"),
                            }
                        ),
                    )
                }
            )
            return source.model_copy(
                update={
                    "nodes": (gamma, alpha),
                    "required_outputs": (NodeOutputRef(node_id="gamma", output_slot="review"),),
                }
            )

        fixture.bank.scripts.extend(
            [
                _script_submit_then_stop(
                    "call_review",
                    "review",
                    {"verdict": "request_changes", "findings": ["fix required"]},
                ),
                ["unused"],
            ]
        )
        _, publication = publish(fixture, review_source)
        started = start(fixture, publication.revision)
        cell["run_id"] = started.run.workflow_run_id
        parent = await fixture.runtime.scheduler.run(cell["run_id"])
        assert parent.status is WorkflowStatus.PAUSED

        source = review_source(publication.revision.nodes[0].agent_definition_ref)
        gamma = next(node for node in source.nodes if node.node_id == "gamma")
        empty_source = source.model_copy(update={"nodes": (gamma,), "edges": ()})
        applied = fixture.runtime.patches.apply(
            _patch(
                parent,
                publication.revision,
                source=empty_source,
                patch_id="wpatch_empty_review",
            ),
            active_model=MODEL,
        )
        assert applied.child.status is WorkflowStatus.COMPLETED
        assert applied.child.result_status == "needs_revision"
        assert root(fixture).status is TaskRunStatus.FAILED
        outcome = fixture.journal.list_task_outcomes(WS, "task_root")[-1]
        assert outcome.trigger is TaskOutcomeTrigger.TERMINAL_CLOSE
        imports = fixture.journal.workflows.list_artifact_imports(WS, applied.child.workflow_run_id)
        assert {item.artifact_id for item in outcome.artifact_refs} == {
            item.artifact_id for item in imports
        }
        assert fixture.journal.workflows.list_nodes(WS, applied.child.workflow_run_id) == ()
        assert (
            fixture.runtime.queries.get_run_view(applied.child.workflow_run_id)
            .effective_outputs[0]
            .inherited
        )
    finally:
        fixture.close()


def test_initial_run_persists_full_execution_set(tmp_path):
    fixture = DagFixture(tmp_path)
    try:
        _, publication = publish(fixture, pair_source)
        run = start(fixture, publication.revision).run
        rows = fixture.journal.workflows.list_execution_nodes(WS, run.workflow_run_id)
        assert [item.node_id for item in rows] == ["gamma", "alpha"]
        assert {item.inclusion_reason for item in rows} == {"initial"}
        assert fixture.runtime.patches.catalog == CATALOG
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_failed_retry_and_full_rerun_derivation_matrix(tmp_path):
    fixture = DagFixture(tmp_path)
    try:
        fixture.bank.scripts.extend(
            [["phase one"], [RuntimeError("late failure")], ["retry succeeds"]]
        )
        _, publication = publish(fixture, pair_source)
        failed = await fixture.runtime.scheduler.run(
            start(fixture, publication.revision).run.workflow_run_id
        )
        assert failed.status is WorkflowStatus.FAILED
        failed_root = root(fixture)
        fixture.tasks.resume(
            failed_root.task_run_id,
            command_id="cmd_resume_failed",
            expected_row_version=failed_root.row_version,
        )
        retry = fixture.runtime.patches.rerun(failed.workflow_run_id, full=False)
        assert retry.inherited_node_ids == ("gamma",)
        assert retry.execution_node_ids == ("alpha",)
        assert retry.child.effective_lineage_budget_root_run_id == retry.child.workflow_run_id
        assert (
            len(fixture.journal.workflows.list_artifact_imports(WS, retry.child.workflow_run_id))
            == 1
        )
        retried = await fixture.runtime.scheduler.run(retry.child.workflow_run_id)
        assert retried.status is WorkflowStatus.COMPLETED

        ready_root = root(fixture)
        fixture.tasks.resume(
            ready_root.task_run_id,
            command_id="cmd_resume_full",
            expected_row_version=ready_root.row_version,
        )
        fixture.bank.scripts.extend([["full phase one"], ["full phase three"]])
        full = fixture.runtime.patches.rerun(retried.workflow_run_id, full=True)
        assert full.inherited_node_ids == ()
        assert full.execution_node_ids == ("gamma", "alpha")
        assert fixture.journal.workflows.list_artifact_imports(WS, full.child.workflow_run_id) == ()
        assert full.child.effective_lineage_budget_root_run_id == full.child.workflow_run_id
        completed = await fixture.runtime.scheduler.run(full.child.workflow_run_id)
        assert completed.status is WorkflowStatus.COMPLETED
    finally:
        fixture.close()
