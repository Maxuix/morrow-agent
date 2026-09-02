"""Read-only Workflow source fixtures; publication remains explicit."""

from morrow.core.agent_runs import AgentDefinitionRef
from morrow.core.workflows.contracts import NodeOutputRef, OutputContract, TaskContract
from morrow.core.workflows.definitions import (
    AgentNodeSource,
    WorkflowBudget,
    WorkflowDefinitionSource,
)


def builtin_direct_workflow(direct_ref: AgentDefinitionRef) -> WorkflowDefinitionSource:
    """The opt-in one-node Direct template consumed by the management surface."""

    return WorkflowDefinitionSource(
        workflow_definition_id="builtin_direct_workflow",
        name="Direct",
        description="Run one task in the invoking Session through the Workflow Scheduler.",
        origin="builtin",
        default_budget=WorkflowBudget(
            max_agent_generation_requests=16,
            default_node_max_agent_generation_requests=16,
            admission_timeout_seconds=3600,
            max_concurrency=1,
        ),
        nodes=(
            AgentNodeSource(
                node_id="direct",
                agent_definition_ref=direct_ref,
                task_contract=TaskContract(objective="Complete the bound Workflow task."),
                output_contracts=(OutputContract(slot="result"),),
                access_mode="write",
                conversation_scope="invoking_session",
            ),
        ),
        required_outputs=(NodeOutputRef(node_id="direct", output_slot="result"),),
    )
