"""Leaf-local desktop selection through production Workflow and the original AgentLoop."""

import asyncio

import pytest

from morrow.application.computer_requests import ComputerUseSelection
from morrow.application.workflows.start import StartWorkflowCommand
from morrow.bootstrap import build_session_application
from morrow.core.agent_definitions import AgentDefinitionSource, ToolRequirement
from morrow.core.agent_runs import AgentDefinitionRef, ProviderCapabilities
from morrow.core.capabilities import PermissionPreset, PermissionProfile
from morrow.core.computer_use import (
    TRUSTED_COMPUTER_USE_AUTHORITY,
    ComputerUseAppIdentity,
    ComputerUseContractError,
    ComputerUseImageShare,
)
from morrow.core.execution import ToolExecutionDisposition, ToolExecutionState
from morrow.core.permissions import IsolationLabel
from morrow.core.runtime_policy import ComputerUseMode, ComputerUseSettings, RuntimePolicyOverrides
from morrow.core.workflows.contracts import NodeOutputRef, OutputContract, TaskContract
from morrow.core.workflows.definitions import (
    AgentNodeSource,
    WorkflowBudget,
    WorkflowDefinitionSource,
)
from morrow.core.workflows.runs import WorkflowStatus
from test_agent_run_preparation import _app, _configure_active, _dispatch_prepared
from test_computer_use_loop import Approval, ImageProvider, Lifecycle


class BlockingApproval(Approval):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()

    async def request(self, request):
        self.requests.append(request)
        self.entered.set()
        await asyncio.Event().wait()


@pytest.mark.parametrize("selected", [True, False, "root_only", "cancelled"])
async def test_workflow_desktop_requires_its_own_local_selection(tmp_path, selected):
    app = _app(tmp_path)
    providers = []

    def provider(config, credential):
        value = ImageProvider()
        providers.append(value)
        return value

    app.registry.register(
        "fake-adapter",
        provider,
        capabilities=ProviderCapabilities(
            tool_protocol="openai_function", input_types=("text", "image")
        ),
    )
    _configure_active(app)
    config = app.global_store.load()
    app.global_store.update(
        lambda value: value.model_copy(
            update={
                "runtime_policy": RuntimePolicyOverrides(
                    computer_use=ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID)
                )
            }
        ),
        expected_revision=config.revision,
    )
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    lifecycle = Lifecycle("completed")
    approval = BlockingApproval() if selected == "cancelled" else Approval()
    products = build_session_application(
        app,
        identity,
        permission_profile=PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL),
        computer_use_lifecycle=lifecycle,
        approval_port=approval,
    )
    factory = products.orchestrator.preparation.computer_factory
    journal, ws = factory.journal, factory.workspace_id
    try:
        management = products.workflow_management
        version = management.agent_publication.publish(
            AgentDefinitionSource(
                definition_id="desktop",
                name="Desktop",
                role_prompt="Click the controlled button.",
                access_mode_ceiling="write",
                tool_requirements=tuple(
                    ToolRequirement(name=name, requirement="required")
                    for name in ("computer_observe", "computer_action")
                ),
            ),
            source_revision=0,
            expected_head_revision=0,
            command_id="cmd_agent",
        )
        ref = AgentDefinitionRef(
            definition_id=version.source.definition_id,
            version_id=version.version_id,
            content_hash=version.content_hash,
        )
        publication = management.workflow_publication.publish(
            WorkflowDefinitionSource(
                workflow_definition_id="desktop",
                name="Desktop",
                default_budget=WorkflowBudget(
                    max_agent_generation_requests=8,
                    default_node_max_agent_generation_requests=8,
                    admission_timeout_seconds=300,
                    max_concurrency=1,
                ),
                nodes=(
                    AgentNodeSource(
                        node_id="click",
                        agent_definition_ref=ref,
                        task_contract=TaskContract(objective="Click the controlled button"),
                        output_contracts=(OutputContract(slot="result"),),
                        access_mode="write",
                    ),
                ),
                required_outputs=(NodeOutputRef(node_id="click", output_slot="result"),),
            ),
            source_revision=0,
            expected_head_revision=0,
            command_id="cmd_workflow",
            active_model=app.global_store.load().value.active_model,
        )
        selection = ComputerUseSelection(
            apps=(ComputerUseAppIdentity(bundle_id="com.example.Controlled"),),
            image_share=ComputerUseImageShare.CONTROLLED_WINDOW,
        )
        if selected == "root_only":
            request = factory.select(
                selection, products.session, authority=TRUSTED_COMPUTER_USE_AUTHORITY
            )
            prepared = products.orchestrator.preparation.prepare_new(
                agent_run_id="arun_root", computer_request=request
            )
            await _dispatch_prepared(products.orchestrator, "Click the controlled button", prepared)
            assert journal.get_permission_snapshot_for_run(ws, "arun_root").grant_id is not None
        initial_calls = list(lifecycle.calls)
        initial_approvals = len(approval.requests)
        initial_grants = journal.list_capability_grants(ws)
        products.tasks.new_task(products.session.session_id, command_id="cmd_new_task")
        root = journal.get_session(ws, products.session.session_id)
        task = journal.get_task_run(ws, root.current_task_run_id)
        runtime = products.workflow_runtime
        started = runtime.start.start(
            StartWorkflowCommand(
                workflow_definition_id="desktop",
                workflow_revision_id=publication.revision.workflow_revision_id,
                session_id=root.session_id,
                root_task_run_id=task.task_run_id,
                expected_root_row_version=task.row_version,
                contract=TaskContract(objective="Click the controlled button"),
                command_id="cmd_start",
            )
        )
        node = journal.workflows.list_nodes(ws, started.run.workflow_run_id)[0]
        selection = ComputerUseSelection(
            apps=(ComputerUseAppIdentity(bundle_id="com.example.Controlled"),),
            image_share=ComputerUseImageShare.CONTROLLED_WINDOW,
        )
        with pytest.raises(ComputerUseContractError):
            factory.select_workflow_leaf(selection, node_run_id=node.node_run_id, authority="model")
        with pytest.raises(ComputerUseContractError):
            factory.select_workflow_leaf(
                selection, node_run_id="nrun_missing", authority=TRUSTED_COMPUTER_USE_AUTHORITY
            )
        if selected is True or selected == "cancelled":
            factory.select_workflow_leaf(
                selection, node_run_id=node.node_run_id, authority=TRUSTED_COMPUTER_USE_AUTHORITY
            )
        if selected == "cancelled":
            running = asyncio.create_task(runtime.scheduler.run(started.run.workflow_run_id))
            try:
                await asyncio.wait_for(approval.entered.wait(), timeout=5)
            finally:
                running.cancel()
                result = await running
            assert result.status is WorkflowStatus.CANCELLED
            node = journal.workflows.get_node(ws, node.node_run_id)
            assert lifecycle.calls == ["open", "close"]
            assert lifecycle.device.actions == []
            assert len(lifecycle.device.reads) == 1
            assert len(approval.requests) == 1
            executions = journal.list_session_executions(ws, node.conversation_session_id)
            action = next(row for row in executions if row.tool_name == "computer_action")
            assert action.state is ToolExecutionState.CLOSED
            assert action.disposition is ToolExecutionDisposition.CANCELLED
            assert action.result_envelope is None
            assert (
                journal.get_approval_for_execution(ws, action.tool_execution_id).consumed_at is None
            )
            return
        result = await runtime.scheduler.run(started.run.workflow_run_id)
        assert result.status is (
            WorkflowStatus.COMPLETED if selected is True else WorkflowStatus.FAILED
        )
        node = journal.workflows.get_node(ws, node.node_run_id)
        if selected is not True:
            assert lifecycle.calls == initial_calls
            assert journal.list_capability_grants(ws) == initial_grants
            assert len(approval.requests) == initial_approvals
            return
        assert lifecycle.calls == ["open", "close"]
        assert len(lifecycle.device.actions) == 1
        assert len(lifecycle.device.reads) == 2
        assert len(approval.requests) == 1
        assert providers[-1].image_pixels == [[], [], [(255, 0, 0)], [(255, 0, 0), (0, 0, 255)]]
        snapshot = journal.get_permission_snapshot_for_run(ws, node.agent_run_id)
        assert snapshot.session_id == node.conversation_session_id != root.session_id
        assert snapshot.task_run_id == node.leaf_task_run_id != task.task_run_id
        assert snapshot.computer_use_scope.agent_run_id == node.agent_run_id
        assert snapshot.computer_use_scope.task_run_id == node.leaf_task_run_id
        executions = journal.list_session_executions(ws, node.conversation_session_id)
        assert len(executions) == 3
        assert all(
            row.grant_id == snapshot.grant_id and row.isolation is IsolationLabel.COMPUTER_USE_HOST
            for row in executions
        )
        assert not journal.list_session_executions(ws, root.session_id)
        with pytest.raises(ComputerUseContractError):
            factory.select_workflow_leaf(
                selection, node_run_id=node.node_run_id, authority=TRUSTED_COMPUTER_USE_AUTHORITY
            )
    finally:
        products.persistence.store_session.close()
