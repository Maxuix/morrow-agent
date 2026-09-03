"""Stage 8 Subplan 1: durable Pause/Drain/Resume runtime.

Pause is one persisted OCC fact; the single admission transaction rechecks it,
so Pause and queued→running have exactly one winner. Drain settles Active nodes
without admitting new ones; recovery resolve-success under a standing pause
returns to draining and never admits; restart preserves the fact. All Providers
are scripted; races are proven with deterministic seams, never wall-clock
sleeps.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest
from pydantic import ValidationError

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.migrations import (
    MigrationRegistry,
    SchemaMigration,
    production_registry,
)
from morrow.adapters.state.operational import OperationalStore
from morrow.core.application import ApplicationError
from morrow.core.faults import FaultPoint, InjectedFault, OnceFaultInjector
from morrow.core.models import AssistantMessage, FunctionToolCall, ToolApprovalDecision
from morrow.core.store import StorageError, StorageErrorCode, StoreOpenMode
from morrow.core.workflows.contracts import ArtifactBinding
from morrow.core.workflows.runs import WorkflowRun, WorkflowStatus, validate_run_transition
from morrow.runtime.policy import ToolApproval, ToolExecutionPolicy
from morrow.runtime.tools import ToolExecutor, ToolRegistry, make_tool
from test_stage7_serial_scheduler import (
    DagFixture,
    ReadArgs,
    ScriptBank,
    _read_handler,
    node_by_id,
    pair_source,
    publish,
    reader_agent,
    resolve_blocking,
    root,
    start,
    wait_for,
)
from test_stage7_workflow_domain import BUDGET, NOW


@pytest.fixture
def fx(tmp_path):
    fixture = DagFixture(tmp_path)
    yield fixture
    fixture.close()


def _run(**changes) -> WorkflowRun:
    return WorkflowRun(
        **{
            "workflow_run_id": "wrun_one",
            "workspace_id": "ws_one",
            "workflow_revision_id": "wrev_one",
            "root_task_run_id": "task_root",
            "budget_snapshot": BUDGET,
            "started_at": NOW,
            "admission_deadline_at": NOW + timedelta(seconds=BUDGET.admission_timeout_seconds),
            "input_artifacts": (
                ArtifactBinding(
                    name="task", artifact_id="art_one", contract={"kind": "TaskContract"}
                ),
            ),
            **changes,
        }
    )


# Domain ------------------------------------------------------------------------


def test_pause_drain_statuses_and_transition_map():
    assert not WorkflowStatus.DRAINING.terminal
    assert not WorkflowStatus.PAUSED.terminal
    assert WorkflowStatus.SUPERSEDED.terminal
    validate_run_transition(WorkflowStatus.RUNNING, WorkflowStatus.DRAINING)
    validate_run_transition(WorkflowStatus.DRAINING, WorkflowStatus.PAUSED)
    validate_run_transition(WorkflowStatus.DRAINING, WorkflowStatus.COMPLETED)
    validate_run_transition(WorkflowStatus.DRAINING, WorkflowStatus.FAILED)
    validate_run_transition(WorkflowStatus.DRAINING, WorkflowStatus.BLOCKED)
    validate_run_transition(WorkflowStatus.PAUSED, WorkflowStatus.RUNNING)
    validate_run_transition(WorkflowStatus.QUEUED, WorkflowStatus.PAUSED)
    validate_run_transition(WorkflowStatus.BLOCKED, WorkflowStatus.DRAINING)
    with pytest.raises(ValueError, match="illegal"):
        validate_run_transition(WorkflowStatus.PAUSED, WorkflowStatus.COMPLETED)
    with pytest.raises(ValueError, match="illegal"):
        validate_run_transition(WorkflowStatus.DRAINING, WorkflowStatus.QUEUED)
    with pytest.raises(ValueError, match="illegal"):
        validate_run_transition(WorkflowStatus.SUPERSEDED, WorkflowStatus.RUNNING)


def test_run_validators_bind_pause_fact_and_lineage():
    paused = _run(status="paused", pause_requested=True)
    assert paused.effective_lineage_budget_root_run_id == "wrun_one"
    with pytest.raises(ValidationError, match="pause_requested"):
        _run(status="running", pause_requested=True)
    with pytest.raises(ValidationError, match="pause fact"):
        _run(status="paused")
    with pytest.raises(ValidationError, match="no parent"):
        _run(parent_run_id="wrun_parent")
    with pytest.raises(ValidationError, match="lineage"):
        _run(run_relation="continuation")
    child = _run(
        workflow_run_id="wrun_child",
        run_relation="continuation",
        parent_run_id="wrun_one",
        lineage_budget_root_run_id="wrun_one",
    )
    assert child.effective_lineage_budget_root_run_id == "wrun_one"


# Migration ---------------------------------------------------------------------


def _v25_registry() -> MigrationRegistry:
    registry = MigrationRegistry(supported_version=25)
    for migration in production_registry().pending(0):
        if migration.version <= 25:
            registry.add(migration)
    return registry


def _legacy_run_body() -> str:
    """A genuine pre-v26 row body: no pause/lineage keys at all."""

    body = _run(workflow_run_id="wrun_old", status="running").model_dump(mode="json")
    for key in (
        "pause_requested",
        "run_relation",
        "lineage_budget_root_run_id",
        "parent_run_id",
        "superseded_reason",
    ):
        del body[key]
    return json.dumps(body)


def test_migration_v26_backfills_legacy_rows_and_rebuilds_root_exclusivity(tmp_path):
    root_dir = tmp_path / "v25"
    with OperationalStore(root_dir, registry=_v25_registry()).initialize() as handle:
        handle.run_write(
            lambda ex: ex.execute(
                "INSERT INTO sessions(session_id, workspace_id, lifecycle, health, conversation_position, created_at_unix, updated_at_unix) VALUES('ses_old','ws_one','active','ok',0,1,1)"
            )
        )
        handle.run_write(
            lambda ex: ex.execute(
                "INSERT INTO task_runs(task_run_id, session_id, workspace_id, status, row_version, attempt, created_at_unix, updated_at_unix) VALUES('task_old','ses_old','ws_one','open',1,1,1,1)"
            )
        )
        handle.run_write(
            lambda ex: ex.execute(
                "INSERT INTO workflow_revisions(workflow_revision_id, workspace_id, workflow_definition_id, revision, content_hash, body_json) VALUES('wrev_one','ws_one','pipeline',1,?,?)",
                ("a" * 64, "{}"),
            )
        )
        handle.run_write(
            lambda ex: ex.execute(
                "INSERT INTO workflow_runs VALUES(?,?,?,?,?,?)",
                ("wrun_old", "ws_one", "wrev_one", "task_old", "running", _legacy_run_body()),
            )
        )
    store = OperationalStore(root_dir)
    assert store.migrate().applied == ("workflow_pause_drain_lineage",)
    with store.open(StoreOpenMode.READ_WRITE) as handle:
        rows = handle.run_read(
            lambda ex: ex.execute(
                "SELECT status, pause_requested, run_relation, lineage_budget_root_run_id, parent_run_id FROM workflow_runs WHERE workflow_run_id='wrun_old'"
            )
        )
        assert rows == (("running", 0, "initial", "wrun_old", None),)
        tables = {
            row[0]
            for row in handle.run_read(
                lambda ex: ex.execute("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        assert "workflow_run_execution_nodes" in tables
        assert "workflow_run_artifact_imports" in tables
        # The legacy running row loads through the new model with derived facts.
        journal = SqliteOperationalJournal(handle)
        run = journal.workflows.get_run("ws_one", "wrun_old")
        assert run.status is WorkflowStatus.RUNNING and not run.pause_requested
        assert run.run_relation == "initial" and run.parent_run_id is None
        assert run.effective_lineage_budget_root_run_id == "wrun_old"
        # The rebuilt partial unique index still blocks a second active root,
        # including the new draining/paused statuses.
        for status in ("queued", "running", "blocked", "draining", "paused"):
            with pytest.raises(StorageError):
                handle.run_write(
                    lambda ex, status=status: ex.execute(
                        "INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            f"wrun_dup_{status}",
                            "ws_one",
                            "wrev_one",
                            "task_old",
                            status,
                            0,
                            "initial",
                            f"wrun_dup_{status}",
                            None,
                            "{}",
                        ),
                    )
                )


def test_rebuild_migration_verification_rolls_back_and_restores_pragmas(tmp_path):
    root_dir = tmp_path / "framework"
    with OperationalStore(root_dir, registry=_v25_registry()).initialize():
        pass
    bad = SchemaMigration(
        version=26,
        name="bad_rebuild",
        statements=(
            "CREATE TABLE stray_orphans (run_id TEXT NOT NULL REFERENCES workflow_runs(workflow_run_id))",
            "INSERT INTO stray_orphans VALUES('wrun_missing')",
        ),
        requires_foreign_keys_off=True,
        requires_rebuild_verification=True,
    )
    broken = _v25_registry()
    broken.supported_version = 26
    broken.add(bad)
    with pytest.raises(StorageError) as excinfo:
        OperationalStore(root_dir, registry=broken).migrate()
    assert excinfo.value.code is StorageErrorCode.NEEDS_REPAIR
    # Nothing landed: the store is still v25 and healthy...
    assert OperationalStore(root_dir).classify().schema_version == 25
    # ...and the real v26 migration succeeds afterwards (a distinct fixed clock
    # keeps the pre-migration backup name apart from the failed attempt's).
    from morrow.testing import FixedClock

    assert OperationalStore(root_dir, clock=FixedClock()).migrate().applied == (
        "workflow_pause_drain_lineage",
    )


# Scheduler: Pause/Drain/Resume --------------------------------------------------


@pytest.mark.asyncio
async def test_pause_before_first_admission_blocks_then_resume_completes(fx):
    fx.bank.scripts.extend([["phase one"], ["phase three"]])
    _, publication = publish(fx, pair_source)
    started = start(fx, publication.revision)
    run_id = started.run.workflow_run_id

    paused = fx.runtime.transitions.request_pause(run_id)
    assert paused.status is WorkflowStatus.PAUSED and paused.pause_requested
    with pytest.raises(ApplicationError, match="paused"):
        await fx.runtime.scheduler.run(run_id)
    # A paused run still owns the root: a second start is rejected.
    with pytest.raises(ApplicationError, match="active WorkflowRun"):
        start(fx, publication.revision, command_id="cmd_second")

    resumed = fx.runtime.transitions.resume_run(run_id)
    assert resumed.status is WorkflowStatus.RUNNING and not resumed.pause_requested
    final = await fx.runtime.scheduler.recover(run_id)
    assert final.status is WorkflowStatus.COMPLETED
    assert node_by_id(fx, run_id, "alpha").status is WorkflowStatus.COMPLETED
    assert root(fx).status.value == "ready_for_acceptance"


@pytest.mark.asyncio
async def test_pause_committed_before_admission_wins_the_race(tmp_path):
    """Pause lands after the scheduler's prechecks but before admission commits."""

    cell = {}

    def pause_at_second_preparation(count):
        if count == 2:
            cell["fx"].runtime.transitions.request_pause(cell["run_id"])

    fixture = DagFixture(tmp_path, bank=ScriptBank(on_create=pause_at_second_preparation))
    try:
        cell["fx"] = fixture
        fixture.bank.scripts.extend([["phase one"], ["unused"], ["phase three"]])
        _, publication = publish(fixture, pair_source)
        started = start(fixture, publication.revision)
        cell["run_id"] = started.run.workflow_run_id

        run = await fixture.runtime.scheduler.run(cell["run_id"])
        assert run.status is WorkflowStatus.PAUSED and run.pause_requested
        gamma = node_by_id(fixture, cell["run_id"], "gamma")
        alpha = node_by_id(fixture, cell["run_id"], "alpha")
        assert gamma.status is WorkflowStatus.COMPLETED
        # Admission rolled back atomically: no AgentRun, no provider call.
        assert alpha.status is WorkflowStatus.QUEUED and alpha.agent_run_id is None
        assert len(fixture.bank.providers) == 2
        assert fixture.bank.providers[1].stream_calls == []
        # The root stays open; nothing terminal was fabricated.
        assert root(fixture).status.value == "open"

        resumed = fixture.runtime.transitions.resume_run(cell["run_id"])
        assert resumed.status is WorkflowStatus.RUNNING and not resumed.pause_requested
        final = await fixture.runtime.scheduler.recover(cell["run_id"])
        assert final.status is WorkflowStatus.COMPLETED
        assert node_by_id(fixture, cell["run_id"], "alpha").status is WorkflowStatus.COMPLETED
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_pause_on_blocked_run_survives_resolve_and_drains_without_admission(fx):
    fx.bank.scripts.extend(
        [
            [
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(id="call_a", name="read", arguments='{"path": "a.py"}'),
                    )
                )
            ],
            ["phase one recovered"],
            ["phase three"],
        ]
    )
    _, publication = publish(fx, pair_source, agent=reader_agent())
    started = start(fx, publication.revision)
    run_id = started.run.workflow_run_id
    fx.runtime.scheduler.faults = OnceFaultInjector(
        FaultPoint.CONVERSATION_BEFORE_TOOL_MESSAGE_COMMIT
    )
    with pytest.raises(InjectedFault):
        await fx.runtime.scheduler.run(run_id)
    fx.runtime.scheduler.faults = None

    blocked = await fx.runtime.scheduler.recover(run_id)
    assert blocked.status is WorkflowStatus.BLOCKED
    # Pause on a blocked run records only the fact; unknown evidence is untouched.
    paused = fx.runtime.transitions.request_pause(run_id)
    assert paused.status is WorkflowStatus.BLOCKED and paused.pause_requested
    assert node_by_id(fx, run_id, "gamma").status is WorkflowStatus.BLOCKED

    resolve_blocking(fx, node_by_id(fx, run_id, "gamma"))
    drained = await fx.runtime.scheduler.recover(run_id)
    # Resolve-success under a standing pause drains: the blocked node finishes,
    # the queued sibling is never admitted, and the run pauses.
    assert drained.status is WorkflowStatus.PAUSED and drained.pause_requested
    assert node_by_id(fx, run_id, "gamma").status is WorkflowStatus.COMPLETED
    alpha = node_by_id(fx, run_id, "alpha")
    assert alpha.status is WorkflowStatus.QUEUED and alpha.agent_run_id is None

    fx.runtime.transitions.resume_run(run_id)
    final = await fx.runtime.scheduler.recover(run_id)
    assert final.status is WorkflowStatus.COMPLETED
    assert node_by_id(fx, run_id, "alpha").status is WorkflowStatus.COMPLETED


class _GatedApprovalPort:
    """Scripted approval: announces the pending Approval, then waits for the test."""

    def __init__(self, *, approved: bool) -> None:
        self.approved = approved
        self.requested = asyncio.Event()
        self.release = asyncio.Event()

    async def request(self, _request) -> ToolApprovalDecision:
        self.requested.set()
        await self.release.wait()
        return ToolApprovalDecision(approved=self.approved)


@pytest.mark.asyncio
async def test_approval_pending_node_drains_then_denial_maps_terminally(tmp_path):
    fixture = DagFixture(tmp_path)
    try:
        port = _GatedApprovalPort(approved=False)
        gated = ToolRegistry()
        gated.register(
            make_tool(
                name="read",
                description="Read",
                arguments_model=ReadArgs,
                handler=_read_handler,
                execution_policy=ToolExecutionPolicy(approval=ToolApproval.REQUIRED),
            )
        )
        fixture.preparation.tool_factory = lambda policy: ToolExecutor(
            gated.snapshot(), policy, approval_port=port
        )
        fixture.bank.scripts.extend(
            [
                ["phase one"],
                [
                    AssistantMessage(
                        tool_calls=(
                            FunctionToolCall(
                                id="call_a", name="read", arguments='{"path": "a.py"}'
                            ),
                        )
                    ),
                    RuntimeError("scripted provider loss after denial"),
                ],
            ]
        )
        _, publication = publish(fixture, pair_source, agent=reader_agent())
        started = start(fixture, publication.revision)
        run_id = started.run.workflow_run_id

        drive = asyncio.create_task(fixture.runtime.scheduler.run(run_id))
        await wait_for(lambda: port.requested.is_set())
        # The node waits on an unconsumed Approval: Active, not blocked.
        paused = fixture.runtime.transitions.request_pause(run_id)
        assert paused.status is WorkflowStatus.DRAINING
        view = fixture.runtime.queries.get_run_view(run_id)
        assert view.run.status is WorkflowStatus.DRAINING
        assert view.actionable_status.startswith("draining")
        alpha = next(item for item in view.nodes if item.node.node_id == "alpha")
        assert alpha.node.status is WorkflowStatus.RUNNING and alpha.approval_pending

        # Denial resolves through the fixed failure mapping; no drain deadlock.
        port.release.set()
        run = await drive
        assert run.status is WorkflowStatus.FAILED
        assert run.pause_requested
        assert node_by_id(fixture, run_id, "alpha").status is WorkflowStatus.FAILED
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_restart_preserves_pause_fact_and_root_exclusivity(tmp_path):
    cell = {}

    def pause_at_second_preparation(count):
        if count == 2:
            cell["fx"].runtime.transitions.request_pause(cell["run_id"])

    fixture = DagFixture(tmp_path, bank=ScriptBank(on_create=pause_at_second_preparation))
    fixture.bank.scripts.extend([["phase one"], ["unused"]])
    _, publication = publish(fixture, pair_source)
    started = start(fixture, publication.revision)
    cell["fx"] = fixture
    cell["run_id"] = started.run.workflow_run_id
    paused = await fixture.runtime.scheduler.run(cell["run_id"])
    assert paused.status is WorkflowStatus.PAUSED
    fixture.close()

    # A fresh process reads the same durable facts: paused, root still owned.
    store = OperationalStore(tmp_path / "state", clock=fixture.clock)
    handle = store.open(StoreOpenMode.READ_WRITE)
    try:
        journal = SqliteOperationalJournal(handle, clock=fixture.clock.now)
        reloaded = journal.workflows.get_run("ws_one", cell["run_id"])
        assert reloaded.status is WorkflowStatus.PAUSED and reloaded.pause_requested
        owner = journal.workflows.active_for_root("ws_one", "task_root")
        assert owner is not None and owner.workflow_run_id == cell["run_id"]
    finally:
        handle.close()
