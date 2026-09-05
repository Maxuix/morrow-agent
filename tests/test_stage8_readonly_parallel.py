"""Bounded read frontiers through real leaf/ledger/transition composition; no Live IO."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from morrow.application.workflows.integrity import verify_workflow_rows
from morrow.core.agent_definitions import ToolRequirement
from morrow.core.capabilities import (
    ApprovalMode,
    OperationIntent,
    OperationKind,
    PermissionProfile,
    WorkspaceCapability,
)
from morrow.core.domain import TaskRunStatus
from morrow.core.faults import FaultPoint, InjectedFault
from morrow.core.models import AssistantMessage, FunctionToolCall, ToolEffect
from morrow.core.workflows.contracts import NodeOutputRef
from morrow.core.workflows.definitions import WorkflowBudget, WorkflowDefinitionSource, WorkflowEdge
from morrow.core.workflows.runs import WorkflowStatus
from morrow.runtime.capabilities import CapabilityPolicy
from morrow.runtime.tools import ToolExecutor, ToolRegistry, make_tool
from morrow.testing import ScriptedModelProvider
from test_stage7_serial_scheduler import (
    WS,
    DagFixture,
    ReadArgs,
    agent_source,
    chain_node,
    publish,
    start,
)


class BarrierBank:
    def __init__(self, count=3):
        self.count = count
        self.providers = []
        self.entered = set()
        self.all_entered = asyncio.Event()
        self.release = [asyncio.Event() for _ in range(count)]
        self.closed = set()

    def __call__(self, config, credential):
        index = len(self.providers)
        bank = self

        class Provider(ScriptedModelProvider):
            async def stream(self, *args, **kwargs):
                bank.entered.add(index)
                if len(bank.entered) == bank.count:
                    bank.all_entered.set()
                try:
                    if index < bank.count:
                        await bank.release[index].wait()
                    async for chunk in super().stream(*args, **kwargs):
                        yield chunk
                finally:
                    bank.closed.add(index)

        provider = Provider([[f"Result {index}"]])
        self.providers.append(provider)
        return provider


def fanout(ref, *, count=3, concurrency=3, cap=None, writer=None):
    ids = tuple(f"reader_{i}" for i in range(count))
    return WorkflowDefinitionSource(
        workflow_definition_id="pipeline",
        name="Read frontier",
        default_budget=WorkflowBudget(
            max_concurrency=concurrency, max_agent_generation_requests=cap
        ),
        nodes=tuple(
            chain_node(
                ref,
                name,
                f"Inspect area {index}",
                access_mode="write" if name == writer else "read",
            )
            for index, name in enumerate((*ids, "summary"))
        ),
        edges=tuple(WorkflowEdge(from_node_id=name, to_node_id="summary") for name in ids),
        required_outputs=(NodeOutputRef(node_id="summary", output_slot="result"),),
    )


def configure(fx, tmp_path, *, drift=False):
    workspace = WorkspaceCapability(workspace_id=WS, root=tmp_path)
    profile = PermissionProfile(approval_mode=ApprovalMode.AUTO_SAFE)
    registry = ToolRegistry()

    async def read(arguments):
        return "read evidence"

    tool = make_tool(
        name="read",
        description="Read",
        arguments_model=ReadArgs,
        handler=read,
        intent_resolver=lambda a, c: OperationIntent(kind=OperationKind.WORKSPACE_READ),
    )
    if drift:
        tool = replace(
            tool,
            runtime_contract=replace(
                tool.runtime_contract, intent_effect=ToolEffect.PERSISTENT_WRITE
            ),
        )
    registry.register(tool)
    fx.preparation.tool_factory = lambda policy: ToolExecutor(
        registry.snapshot(),
        policy,
        capability_policy=CapabilityPolicy(profile, workspace) if not drift else None,
    )

    def context(session):
        session.workspace_capability = workspace
        session.permission_profile = profile

    fx.runtime.scheduler.initialize_context = context


def nodes(fx, run):
    return fx.journal.workflows.list_nodes(WS, run.workflow_run_id)


def completion_events(fx):
    events = {}
    original = fx.runtime.scheduler._drive_node

    async def drive(run, revision, definition, node, cap, **kwargs):
        try:
            return await original(run, revision, definition, node, cap, **kwargs)
        finally:
            events.setdefault(definition.node_id, asyncio.Event()).set()

    fx.runtime.scheduler._drive_node = drive
    return events


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled_is_user", [True, False])
async def test_sibling_failure_closes_admitted_leaves_even_with_server_driver_semantics(
    tmp_path, cancelled_is_user
):
    from morrow.core.models import ModelErrorCode, ModelEvent, ModelFailure, ModelFailureOrigin

    entered = asyncio.Event()
    waiting = asyncio.Event()
    created = []
    fx = DagFixture(tmp_path)

    class Provider(ScriptedModelProvider):
        def __init__(self, index):
            super().__init__([["done"]])
            self.index = index

        async def stream(self, *args, **kwargs):
            if self.index == 0:
                await entered.wait()
                yield ModelEvent(
                    kind="error",
                    failure=ModelFailure(
                        code=ModelErrorCode.AUTH,
                        origin=ModelFailureOrigin.PROVIDER,
                        message="authentication unavailable",
                        retryable=False,
                    ),
                )
                return
            entered.set()
            await waiting.wait()
            async for event in super().stream(*args, **kwargs):
                yield event

    def make(config, credential):
        provider = Provider(len(created))
        created.append(provider)
        return provider

    try:
        configure(fx, tmp_path)
        fx.app.registry.register("fake-adapter", make)
        _, publication = publish(fx, lambda ref: fanout(ref, count=2, concurrency=2))
        run = start(fx, publication.revision).run
        result = await asyncio.wait_for(
            fx.runtime.scheduler.run(run.workflow_run_id, cancelled_is_user=cancelled_is_user), 15
        )
        assert result.status is WorkflowStatus.FAILED
        admitted = [node for node in nodes(fx, run) if node.agent_run_id]
        assert len(admitted) == 2
        for node in admitted:
            assert not fx.journal.has_open_turn_submission(WS, node.conversation_session_id)
            leaf = fx.journal.get_task_run(WS, node.leaf_task_run_id)
            assert leaf.status in {TaskRunStatus.FAILED, TaskRunStatus.CANCELLED}
            transitions = fx.journal.list_task_transitions(WS, node.leaf_task_run_id)
            assert (
                sum(
                    t.to_status in {TaskRunStatus.FAILED, TaskRunStatus.CANCELLED}
                    for t in transitions
                )
                == 1
            )
        assert not fx.runtime.scheduler._live_node_run_ids
    finally:
        fx.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled_is_user", [True, False])
async def test_serial_fallback_signal_pauses_and_continues_without_losing_queued_work(
    tmp_path, cancelled_is_user
):
    from morrow.core.workflows.replan import ReplanRequest
    from test_stage7_serial_scheduler import ScriptBank

    request = ReplanRequest(
        target_node_id="summary", task_contract={"objective": "Use revised evidence"}
    )
    submission = AssistantMessage(
        tool_calls=(
            FunctionToolCall(
                id="call_replan",
                name="submit_node_result",
                arguments='{"outputs": {}, "replan":' + request.model_dump_json() + "}",
            ),
        )
    )
    fx = DagFixture(tmp_path, bank=ScriptBank([[submission, "first reader complete"], ["unused"]]))
    try:
        # The fixture's executor has no workspace proof: the declared frontier
        # must use the supported serial fallback, not parallel admission.
        _, publication = publish(fx, lambda ref: fanout(ref, count=2, concurrency=2))
        run = start(fx, publication.revision).run
        paused = await fx.runtime.scheduler.run(
            run.workflow_run_id, cancelled_is_user=cancelled_is_user
        )
        assert paused.status is WorkflowStatus.PAUSED
        assert {n.node_id: n.status for n in nodes(fx, run)} == {
            "reader_0": WorkflowStatus.COMPLETED,
            "reader_1": WorkflowStatus.QUEUED,
            "summary": WorkflowStatus.QUEUED,
        }
        proposals = fx.journal.workflows.list_replan_proposals(WS, run.workflow_run_id)
        assert len(proposals) == 1 and proposals[0].status == "pending"
        decision = fx.runtime.replan.decide(
            proposals[0].proposal_id, approved=True, expected_row_version=1
        )
        child = await fx.runtime.scheduler.run(
            decision.child_run_id, cancelled_is_user=cancelled_is_user
        )
        assert child.status is WorkflowStatus.COMPLETED
        assert {n.node_id for n in nodes(fx, child)} == {"reader_1", "summary"}
        assert fx.journal.count_lineage_agent_requests(WS, run.workflow_run_id) == 4
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_frontier_admits_together_and_publishes_in_stable_order(tmp_path):
    bank = BarrierBank()
    fx = DagFixture(tmp_path, bank=bank)
    try:
        configure(fx, tmp_path)
        _, revision = publish(fx, fanout)
        run = start(fx, revision.revision).run
        finished = completion_events(fx)
        published = []
        fx.runtime.transitions.event_sink = lambda kind, entity, ident, data: (
            published.append(data["node_id"])
            if data.get("status") == "completed" and entity == "workflow_node"
            else None
        )
        task = asyncio.create_task(fx.runtime.scheduler.run(run.workflow_run_id))
        await asyncio.wait_for(bank.all_entered.wait(), 15)
        active = [n for n in nodes(fx, run) if n.status is WorkflowStatus.RUNNING]
        assert len(active) == 3
        assert all(n.parallel_read_digest for n in active)
        assert len({n.conversation_session_id for n in active}) == 3
        for n in active:
            permission = fx.journal.get_permission_snapshot_for_run(WS, n.agent_run_id)
            assert permission.workspace_read_only
        assert fx.journal.count_workflow_agent_requests(WS, run.workflow_run_id) == 3
        for index in (2, 1):
            finished.setdefault(f"reader_{index}", asyncio.Event())
            bank.release[index].set()
            await asyncio.wait_for(finished[f"reader_{index}"].wait(), 15)
            assert (
                fx.runtime.scheduler.outputs.resolve(
                    run.workflow_run_id, f"reader_{index}", "result"
                )
                is None
            )
            assert not published
        bank.release[0].set()
        result = await asyncio.wait_for(task, 15)
        assert result.status is WorkflowStatus.COMPLETED
        assert published == ["reader_0", "reader_1", "reader_2", "summary"]
        assert fx.journal.count_workflow_agent_requests(WS, run.workflow_run_id) == 4
        assert all(n.status is WorkflowStatus.COMPLETED for n in nodes(fx, run))
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_cancel_preserves_completed_leaf_and_joins_every_active_node(tmp_path):
    bank = BarrierBank()
    fx = DagFixture(tmp_path, bank=bank)
    try:
        configure(fx, tmp_path)
        _, revision = publish(fx, fanout)
        run = start(fx, revision.revision).run
        finished = completion_events(fx)
        task = asyncio.create_task(fx.runtime.scheduler.run(run.workflow_run_id))
        await asyncio.wait_for(bank.all_entered.wait(), 15)
        finished.setdefault("reader_2", asyncio.Event())
        bank.release[2].set()
        await asyncio.wait_for(finished["reader_2"].wait(), 15)
        task.cancel()
        result = await asyncio.wait_for(task, 15)
        assert result.status is WorkflowStatus.CANCELLED
        assert bank.closed == {0, 1, 2}
        assert {n.node_id: n.status for n in nodes(fx, run)} == {
            "reader_0": WorkflowStatus.CANCELLED,
            "reader_1": WorkflowStatus.CANCELLED,
            "reader_2": WorkflowStatus.COMPLETED,
            "summary": WorkflowStatus.CANCELLED,
        }
        assert not fx.runtime.scheduler._live_node_run_ids
        transitions = fx.journal.list_task_transitions(WS, "task_root")
        assert sum(t.to_status is TaskRunStatus.CANCELLED for t in transitions) == 1
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_atomic_request_cap_rejects_overclaim(tmp_path):
    bank = BarrierBank()
    fx = DagFixture(tmp_path, bank=bank)
    try:
        configure(fx, tmp_path)
        for event in bank.release:
            event.set()
        _, revision = publish(fx, lambda ref: fanout(ref, cap=2))
        run = start(fx, revision.revision).run
        result = await asyncio.wait_for(fx.runtime.scheduler.run(run.workflow_run_id), 15)
        assert result.status is WorkflowStatus.FAILED
        assert fx.journal.count_workflow_agent_requests(WS, run.workflow_run_id) == 2
        assert len(bank.entered) == 2
    finally:
        fx.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [1, 2, 3])
async def test_bounded_capacity_and_serial_fallback(tmp_path, concurrency):
    fx = DagFixture(tmp_path)
    try:
        # No frozen workspace capability: capacity alone cannot establish proof.
        _, revision = publish(fx, lambda ref: fanout(ref, concurrency=concurrency))
        run = start(fx, revision.revision).run
        result = await asyncio.wait_for(fx.runtime.scheduler.run(run.workflow_run_id), 15)
        assert result.status is WorkflowStatus.COMPLETED
        assert all(n.parallel_read_digest is None for n in nodes(fx, run))
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_pause_drains_whole_frontier_before_resuming_summary(tmp_path):
    bank = BarrierBank()
    fx = DagFixture(tmp_path, bank=bank)
    try:
        configure(fx, tmp_path)
        _, revision = publish(fx, fanout)
        run = start(fx, revision.revision).run
        task = asyncio.create_task(fx.runtime.scheduler.run(run.workflow_run_id))
        await asyncio.wait_for(bank.all_entered.wait(), 15)
        draining = fx.runtime.transitions.request_pause(run.workflow_run_id)
        assert draining.status is WorkflowStatus.DRAINING
        for event in bank.release:
            event.set()
        paused = await asyncio.wait_for(task, 15)
        assert paused.status is WorkflowStatus.PAUSED
        assert len(bank.providers) == 3
        assert nodes(fx, run)[-1].status is WorkflowStatus.QUEUED
        fx.runtime.transitions.resume_run(run.workflow_run_id)
        completed = await fx.runtime.scheduler.run(run.workflow_run_id)
        assert completed.status is WorkflowStatus.COMPLETED
        assert len(bank.providers) == 4
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_driver_loss_recovers_pending_outputs_without_repeating_completed_leaves(tmp_path):
    bank = BarrierBank()
    fx = DagFixture(tmp_path, bank=bank)
    try:
        configure(fx, tmp_path)
        _, revision = publish(fx, fanout)
        run = start(fx, revision.revision).run
        finished = completion_events(fx)
        task = asyncio.create_task(
            fx.runtime.scheduler.run(run.workflow_run_id, cancelled_is_user=False)
        )
        await asyncio.wait_for(bank.all_entered.wait(), 15)
        finished.setdefault("reader_2", asyncio.Event())
        bank.release[2].set()
        await asyncio.wait_for(finished["reader_2"].wait(), 15)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        third = nodes(fx, run)[2]
        assert (
            fx.journal.get_task_run(WS, third.leaf_task_run_id).status
            is TaskRunStatus.READY_FOR_ACCEPTANCE
        )
        assert (
            fx.runtime.scheduler.outputs.resolve(run.workflow_run_id, "reader_2", "result") is None
        )
        assert all(n.status is WorkflowStatus.RUNNING for n in nodes(fx, run)[:3])
        result = await asyncio.wait_for(fx.runtime.scheduler.recover(run.workflow_run_id), 15)
        assert result.status is WorkflowStatus.COMPLETED
        assert len(bank.providers) == 6  # initial three + two interrupted rehydrations + summary
        assert fx.journal.count_node_agent_requests(WS, third.node_run_id) == 1
        assert len(bank.providers[2].stream_calls) == 1
        assert fx.journal.transact(lambda txn: verify_workflow_rows(txn._backend.executor()))[0]
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_static_read_contract_drift_fails_without_request_or_fallback(tmp_path):
    fx = DagFixture(tmp_path)
    try:
        configure(fx, tmp_path, drift=True)
        agent = agent_source(
            tool_requirements=(ToolRequirement(name="read", requirement="required"),)
        )
        _, revision = publish(fx, fanout, agent=agent)
        run = start(fx, revision.revision).run
        result = await asyncio.wait_for(fx.runtime.scheduler.run(run.workflow_run_id), 15)
        assert result.status is WorkflowStatus.FAILED
        assert all(n.status is WorkflowStatus.FAILED for n in nodes(fx, run)[:3])
        assert fx.journal.count_workflow_agent_requests(WS, run.workflow_run_id) == 0
        assert all(not p.stream_calls for p in fx.bank.providers)
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_dynamic_read_contract_drift_never_enters_handler_or_retries_model(tmp_path):
    fx = DagFixture(tmp_path)
    try:
        configure(fx, tmp_path)
        original = fx.preparation.tool_factory
        entered = []

        def tools(policy):
            executor = original(policy)
            tool = executor.tool_set.tools["read"]
            registry = ToolRegistry()

            async def unsafe(arguments):
                entered.append(True)
                return "unsafe"

            registry.register(
                replace(
                    tool,
                    handler=unsafe,
                    intent_resolver=lambda a, c: OperationIntent(
                        kind=OperationKind.WORKSPACE_WRITE, effect=ToolEffect.PERSISTENT_WRITE
                    ),
                )
            )
            return ToolExecutor(
                registry.snapshot(), policy, capability_policy=executor.capability_policy
            )

        fx.preparation.tool_factory = tools
        call = AssistantMessage(
            tool_calls=(
                FunctionToolCall(id="read_call", name="read", arguments='{"path":"a.txt"}'),
            )
        )
        fx.bank.scripts.extend([[call, ["must not request"]] for _ in range(3)])
        agent = agent_source(
            tool_requirements=(ToolRequirement(name="read", requirement="required"),)
        )
        _, revision = publish(fx, fanout, agent=agent)
        run = start(fx, revision.revision).run
        result = await asyncio.wait_for(fx.runtime.scheduler.run(run.workflow_run_id), 15)
        assert result.status is WorkflowStatus.FAILED
        assert not entered
        assert all(len(p.stream_calls) <= 1 for p in fx.bank.providers)
        assert "read_contract_drift" in fx.journal.list_task_outcomes(WS, "task_root")[-1].summary
    finally:
        fx.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("writer", [None, "reader_2"])
async def test_actual_capacity_bound_and_writer_exclusivity(tmp_path, writer):
    fx = DagFixture(tmp_path)
    try:
        configure(fx, tmp_path)
        _, revision = publish(
            fx,
            lambda ref: fanout(ref, count=5, concurrency=2, writer=writer),
            agent=agent_source(access_mode_ceiling="write"),
        )
        run = start(fx, revision.revision).run
        occupancies = []

        def observe(kind, entity, ident, data):
            if entity != "workflow_node" or data.get("status") != "running":
                return
            active = [n for n in nodes(fx, run) if n.status is WorkflowStatus.RUNNING]
            occupancies.append(len(active))
            assert len(active) <= 2
            if any(n.node_id == writer for n in active):
                assert len(active) == 1

        fx.runtime.transitions.event_sink = observe
        result = await fx.runtime.scheduler.run(run.workflow_run_id)
        assert result.status is WorkflowStatus.COMPLETED
        assert max(occupancies) == 2
        assert len(fx.bank.providers) == 6
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_crash_after_leaf_terminal_retains_other_pending_nodes_for_recovery(tmp_path):
    fx = DagFixture(tmp_path)
    try:
        configure(fx, tmp_path)
        _, revision = publish(fx, fanout)
        run = start(fx, revision.revision).run
        original = fx.runtime.scheduler._drive_node
        fired = False

        async def crash(*args, **kwargs):
            nonlocal fired
            result = await original(*args, **kwargs)
            if not fired:
                fired = True
                raise InjectedFault(FaultPoint.TURN_AFTER_TERMINAL_COMMIT)
            return result

        fx.runtime.scheduler._drive_node = crash
        with pytest.raises(InjectedFault):
            await fx.runtime.scheduler.run(run.workflow_run_id)
        fx.runtime.scheduler._drive_node = original
        ready = [
            n
            for n in nodes(fx, run)
            if n.leaf_task_run_id
            and fx.journal.get_task_run(WS, n.leaf_task_run_id).status
            is TaskRunStatus.READY_FOR_ACCEPTANCE
        ]
        assert ready
        previous = {
            n.node_run_id: fx.journal.count_node_agent_requests(WS, n.node_run_id) for n in ready
        }
        result = await fx.runtime.scheduler.recover(run.workflow_run_id)
        assert result.status is WorkflowStatus.COMPLETED
        assert all(
            fx.journal.count_node_agent_requests(WS, nid) == count
            for nid, count in previous.items()
        )
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_process_cwd_environment_isolation_stress(tmp_path):
    import os
    import sys

    from morrow.adapters.local.process import HostProcessAdapter

    parent_cwd = os.getcwd()
    parent_value = os.environ.get("MORROW_PARALLEL_TEST")
    barrier = asyncio.Barrier(16)
    adapter = HostProcessAdapter()

    async def read(index):
        directory = tmp_path / str(index)
        directory.mkdir()
        (directory / "value.txt").write_text(str(index))
        await barrier.wait()
        result = await adapter.run(
            argv=(
                sys.executable,
                "-c",
                "import os; from pathlib import Path; print(Path('value.txt').read_text() + ':' + os.environ['MORROW_PARALLEL_TEST'])",
            ),
            shell=None,
            cwd=directory,
            timeout_seconds=15,
            environment={"MORROW_PARALLEL_TEST": str(index)},
            output_limit=1024,
        )
        assert result.returncode == 0
        assert result.stdout_tail.decode().strip() == f"{index}:{index}"

    await asyncio.gather(*(read(i) for i in range(16)))
    assert os.getcwd() == parent_cwd
    assert os.environ.get("MORROW_PARALLEL_TEST") == parent_value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cap,expected", [(7, WorkflowStatus.COMPLETED), (3, WorkflowStatus.FAILED)]
)
async def test_parallel_tools_claim_each_followup_request_without_node_reservations(
    tmp_path, cap, expected
):
    fx = DagFixture(tmp_path)
    try:
        configure(fx, tmp_path)
        call = AssistantMessage(
            tool_calls=(
                FunctionToolCall(id="read_call", name="read", arguments='{"path":"a.txt"}'),
            )
        )
        fx.bank.scripts.extend([[call, ["read complete"]] for _ in range(3)])
        agent = agent_source(
            tool_requirements=(ToolRequirement(name="read", requirement="required"),),
            max_agent_generation_requests=2,
        )
        _, revision = publish(fx, lambda ref: fanout(ref, cap=cap), agent=agent)
        run = start(fx, revision.revision).run
        result = await fx.runtime.scheduler.run(run.workflow_run_id)
        assert result.status is expected
        assert fx.journal.count_workflow_agent_requests(WS, run.workflow_run_id) == cap
        assert all(
            n.effective_node_generation_request_cap == 2
            for n in nodes(fx, run)[:3]
            if n.agent_run_id
        )
        for node in nodes(fx, run)[:3]:
            executions = fx.journal.list_executions(WS, agent_run_id=node.agent_run_id)
            assert len(executions) == 1 if cap == 7 else len(executions) <= 1
            assert all(execution.disposition.value == "succeeded" for execution in executions)
            requests = fx.journal.list_model_requests(WS, node.agent_run_id)
            assert len(requests) <= 2
            assert all(request.state.value != "admitted" for request in requests)
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_unknown_tool_contract_uses_serial_fallback(tmp_path):
    fx = DagFixture(tmp_path)
    try:
        configure(fx, tmp_path)
        original = fx.preparation.tool_factory
        access = {"opaque_read": "read"}
        catalog = replace(fx.agents.catalog, tool_access=access, allowed_tools=frozenset(access))
        fx.agents.catalog = catalog
        fx.compiler.catalog = catalog

        def tools(policy):
            executor = original(policy)
            registry = ToolRegistry()

            async def read(arguments):
                return "opaque"

            registry.register(
                make_tool(
                    name="opaque_read",
                    description="Opaque",
                    arguments_model=ReadArgs,
                    handler=read,
                    intent_resolver=lambda a, c: OperationIntent(kind=OperationKind.WORKSPACE_READ),
                )
            )
            return ToolExecutor(
                registry.snapshot(), policy, capability_policy=executor.capability_policy
            )

        fx.preparation.tool_factory = tools
        agent = agent_source(
            tool_requirements=(ToolRequirement(name="opaque_read", requirement="optional"),)
        )
        _, revision = publish(fx, fanout, agent=agent)
        run = start(fx, revision.revision).run
        result = await fx.runtime.scheduler.run(run.workflow_run_id)
        assert result.status is WorkflowStatus.COMPLETED
        assert all(n.parallel_read_digest is None for n in nodes(fx, run))
        assert len(fx.bank.providers) == 4
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_store_rejects_excess_slots_even_if_scheduler_frontier_is_too_large(tmp_path):
    fx = DagFixture(tmp_path)
    try:
        configure(fx, tmp_path)
        _, publication = publish(fx, lambda ref: fanout(ref, concurrency=2))
        revision = publication.revision
        run = start(fx, revision).run
        seen = []
        fx.runtime.transitions.event_sink = lambda kind, entity, ident, data: (
            seen.append(sum(n.status is WorkflowStatus.RUNNING for n in nodes(fx, run)))
            if entity == "workflow_node"
            else None
        )
        entries = tuple(zip(revision.nodes[:3], nodes(fx, run)[:3], strict=True))
        await asyncio.wait_for(
            fx.runtime.scheduler._drive_frontier(run, revision, entries, cancelled_is_user=True), 15
        )
        assert max(seen) == 2
        assert fx.runtime.transitions.get_run(run.workflow_run_id).status is WorkflowStatus.FAILED
        assert sum(n.agent_run_id is not None for n in nodes(fx, run)) == 2
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_frozen_permission_is_required_by_atomic_parallel_admission(tmp_path, monkeypatch):
    from morrow.application import turn_permissions

    fx = DagFixture(tmp_path)
    try:
        configure(fx, tmp_path)
        original = turn_permissions.build_permission_snapshot
        monkeypatch.setattr(
            turn_permissions,
            "build_permission_snapshot",
            lambda *a, **kw: original(*a, **kw).model_copy(update={"workspace_read_only": False}),
        )
        _, publication = publish(fx, fanout)
        run = start(fx, publication.revision).run
        result = await asyncio.wait_for(fx.runtime.scheduler.run(run.workflow_run_id), 15)
        assert result.status is WorkflowStatus.FAILED
        assert not any(n.agent_run_id for n in nodes(fx, run))
        assert fx.journal.count_workflow_agent_requests(WS, run.workflow_run_id) == 0
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_provider_rate_limit_retries_have_one_leaf_owner_and_one_durable_ledger(tmp_path):
    from morrow.core.models import ModelErrorCode, ModelEvent, ModelFailure, ModelFailureOrigin

    fx = DagFixture(tmp_path)
    try:
        configure(fx, tmp_path)
        created = []

        class RateLimited(ScriptedModelProvider):
            def __init__(self):
                super().__init__([["complete"]])
                self.attempts = 0

            async def stream(self, *a, **kw):
                self.attempts += 1
                if self.attempts == 1:
                    yield ModelEvent(
                        kind="error",
                        failure=ModelFailure(
                            code=ModelErrorCode.RATE_LIMIT,
                            origin=ModelFailureOrigin.PROVIDER,
                            retryable=True,
                            retry_after_seconds=2,
                            message="limited",
                        ),
                    )
                    return
                async for event in super().stream(*a, **kw):
                    yield event

        def make(config, credential):
            provider = RateLimited()
            created.append(provider)
            return provider

        fx.app.registry.register("fake-adapter", make)
        waits = []

        async def backoff(delay):
            waits.append(delay)

        fx.runtime.scheduler.retry_sleep = backoff
        _, publication = publish(fx, fanout)
        run = start(fx, publication.revision).run
        result = await fx.runtime.scheduler.run(run.workflow_run_id)
        assert result.status is WorkflowStatus.COMPLETED
        assert len(created) == 4 and all(p.attempts == 2 for p in created)
        assert len(waits) == 4 and all(delay >= 2 for delay in waits)
        assert fx.journal.count_workflow_agent_requests(WS, run.workflow_run_id) == 8
        for node in nodes(fx, run):
            requests = fx.journal.list_model_requests(WS, node.agent_run_id)
            assert [request.state.value for request in requests] == ["failed", "completed"]
    finally:
        fx.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("user_cancel", [False, True])
async def test_cancel_at_admission_barrier_preserves_driver_loss_semantics(tmp_path, user_cancel):
    fx = DagFixture(tmp_path)
    try:
        configure(fx, tmp_path)
        _, publication = publish(fx, fanout)
        run = start(fx, publication.revision).run
        fired = False
        task = None

        def stop(kind, entity, ident, data):
            nonlocal fired
            if not fired and entity == "workflow_node" and data.get("status") == "running":
                fired = True
                task.cancel()

        fx.runtime.transitions.event_sink = stop
        task = asyncio.create_task(
            fx.runtime.scheduler.run(run.workflow_run_id, cancelled_is_user=user_cancel)
        )
        if user_cancel:
            result = await task
            assert result.status is WorkflowStatus.CANCELLED
        else:
            with pytest.raises(asyncio.CancelledError):
                await task
            active = [n for n in nodes(fx, run) if n.agent_run_id]
            assert active
            assert all(
                fx.journal.get_task_run(WS, n.leaf_task_run_id).status is TaskRunStatus.OPEN
                for n in active
            )
            fx.runtime.transitions.event_sink = None
            assert (
                await fx.runtime.scheduler.recover(run.workflow_run_id)
            ).status is WorkflowStatus.COMPLETED
        assert not fx.runtime.scheduler._live_node_run_ids
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_partial_admission_recovery_drains_later_active_before_earlier_queued(
    tmp_path, monkeypatch
):
    from morrow.application.workflows.parallel import ReadFrontier

    fx = DagFixture(tmp_path)
    try:
        configure(fx, tmp_path)
        _, publication = publish(fx, fanout)
        run = start(fx, publication.revision).run
        admitted = asyncio.Event()
        never = asyncio.Event()
        original = ReadFrontier.prepare

        async def pause_before_admission(self, node_id, *args, **kwargs):
            prepared = await original(self, node_id, *args, **kwargs)
            if node_id != "reader_2":
                await never.wait()
            return prepared

        monkeypatch.setattr(ReadFrontier, "prepare", pause_before_admission)
        fx.runtime.transitions.event_sink = lambda kind, entity, ident, data: (
            admitted.set()
            if entity == "workflow_node" and data.get("status") == "running"
            else None
        )
        task = asyncio.create_task(
            fx.runtime.scheduler.run(run.workflow_run_id, cancelled_is_user=False)
        )
        await asyncio.wait_for(admitted.wait(), 15)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert [n.status for n in nodes(fx, run)] == [
            WorkflowStatus.QUEUED,
            WorkflowStatus.QUEUED,
            WorkflowStatus.RUNNING,
            WorkflowStatus.QUEUED,
        ]
        monkeypatch.setattr(ReadFrontier, "prepare", original)
        fx.runtime.transitions.event_sink = None
        result = await fx.runtime.scheduler.recover(run.workflow_run_id)
        assert result.status is WorkflowStatus.COMPLETED
        assert fx.journal.count_workflow_agent_requests(WS, run.workflow_run_id) == 4
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_catalog_filter_cannot_hide_frozen_optional_read_contract_drift(tmp_path):
    fx = DagFixture(tmp_path)
    try:
        configure(fx, tmp_path, drift=True)
        agent = agent_source(
            tool_requirements=(ToolRequirement(name="read", requirement="optional"),)
        )
        _, publication = publish(fx, fanout, agent=agent)
        # The current catalog removes the optional tool, but its frozen read
        # requirement still has to be checked against the actual backend.
        fx.agents.catalog = replace(fx.agents.catalog, tool_access={"read": "write"})
        run = start(fx, publication.revision).run
        result = await fx.runtime.scheduler.run(run.workflow_run_id)
        assert result.status is WorkflowStatus.FAILED
        assert all(n.status is WorkflowStatus.FAILED for n in nodes(fx, run)[:3])
        assert fx.journal.count_workflow_agent_requests(WS, run.workflow_run_id) == 0
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_production_core_api_composition_enables_proven_read_parallelism(tmp_path):
    from test_stage8_core_api import (
        ServerFixture,
        create_session_and_task,
        publish_pipeline,
        start_run,
        wait_for_run,
    )

    fx = ServerFixture(tmp_path)
    try:
        revision = await publish_pipeline(fx, make_source=fanout)
        session, task, version = await create_session_and_task(fx.client)
        reply = await start_run(fx.client, revision.workflow_revision_id, session, task, version)
        assert reply.status == 200, reply.body
        run_id = reply.json()["result"]["run"]["workflow_run_id"]
        await wait_for_run(fx.client, run_id, "completed")

        def evidence():
            journal = fx.host.context.journal
            stored = journal.workflows.list_nodes(fx.workspace_id, run_id)
            return stored, tuple(
                journal.get_permission_snapshot_for_run(fx.workspace_id, node.agent_run_id)
                for node in stored[:3]
            )

        stored, permissions = await fx.on_core(evidence)
        assert all(node.parallel_read_digest for node in stored[:3])
        assert all(permission.workspace_read_only for permission in permissions)
        assert len({permission.workspace_root_digest for permission in permissions}) == 1
        response = await fx.client.get("/v1/events?after=0&limit=100")
        assert response.status == 200, response.body
        active = set()
        peak = 0
        for event in response.json()["events"]:
            if event["event_type"] != "workflow_node.status_changed":
                continue
            payload = event["payload"]
            if payload["workflow_run_id"] != run_id:
                continue
            if payload["status"] == "running":
                active.add(payload["node_id"])
                peak = max(peak, len(active))
            else:
                active.discard(payload["node_id"])
        assert peak == 3
    finally:
        fx.close()
