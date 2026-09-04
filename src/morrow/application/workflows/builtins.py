"""Read-only Workflow source fixtures; publication remains explicit."""

from morrow.core.agent_runs import AgentDefinitionRef
from morrow.core.workflows.contracts import (
    ContractRef,
    NodeOutputBinding,
    NodeOutputRef,
    OutputContract,
    TaskContract,
    TaskContractRef,
    WorkflowInputBinding,
)
from morrow.core.workflows.definitions import (
    AgentNodeSource,
    WorkflowBudget,
    WorkflowDefinitionSource,
    WorkflowEdge,
)

_DEFAULT_BUDGET = WorkflowBudget()


def _task_binding() -> WorkflowInputBinding:
    return WorkflowInputBinding(
        source="workflow_input",
        input_name="task",
        accepts=TaskContractRef(),
        workflow_input="task",
    )


def _result_binding(name: str, node_id: str) -> NodeOutputBinding:
    return NodeOutputBinding(
        source="node_output",
        input_name=name,
        accepts=ContractRef(kind="TextResult"),
        node_output=NodeOutputRef(node_id=node_id, output_slot="result"),
    )


def builtin_direct_workflow(direct_ref: AgentDefinitionRef) -> WorkflowDefinitionSource:
    """The opt-in one-node Direct template consumed by the management surface."""

    return WorkflowDefinitionSource(
        workflow_definition_id="builtin_direct_workflow",
        name="Direct",
        description="Run one task in the invoking Session through the Workflow Scheduler.",
        origin="builtin",
        default_budget=_DEFAULT_BUDGET,
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


def builtin_explore_implement_verify(
    explorer_ref: AgentDefinitionRef,
    coder_ref: AgentDefinitionRef,
    reviewer_ref: AgentDefinitionRef,
    *,
    native_sandbox: bool,
) -> WorkflowDefinitionSource:
    # The permission mode still selects the executable ToolSet at publication
    # time. It no longer changes the template's communication protocol.
    del native_sandbox
    return WorkflowDefinitionSource(
        workflow_definition_id="builtin_explore_implement_verify",
        name="Explore Implement Verify",
        description=(
            "Minimal editable multi-Agent suggestion using one generic result link between "
            "ordinary nodes. Clone it to add, replace or remove roles and edges."
        ),
        origin="builtin",
        default_budget=_DEFAULT_BUDGET,
        nodes=(
            AgentNodeSource(
                node_id="explorer",
                agent_definition_ref=explorer_ref,
                task_contract=TaskContract(
                    objective="Explore the task and return a useful result."
                ),
                input_bindings=(_task_binding(),),
                output_contracts=(OutputContract(slot="result"),),
                access_mode="read",
            ),
            AgentNodeSource(
                node_id="coder",
                agent_definition_ref=coder_ref,
                task_contract=TaskContract(
                    objective="Implement and validate the requested change."
                ),
                input_bindings=(
                    _task_binding(),
                    _result_binding("previous_result", "explorer"),
                ),
                output_contracts=(OutputContract(slot="result"),),
                access_mode="write",
            ),
            AgentNodeSource(
                node_id="reviewer",
                agent_definition_ref=reviewer_ref,
                task_contract=TaskContract(
                    objective="Verify the task outcome and return the final result."
                ),
                input_bindings=(
                    _task_binding(),
                    _result_binding("previous_result", "coder"),
                ),
                output_contracts=(OutputContract(slot="result"),),
                access_mode="read",
            ),
        ),
        edges=(
            WorkflowEdge(from_node_id="explorer", to_node_id="coder"),
            WorkflowEdge(from_node_id="coder", to_node_id="reviewer"),
        ),
        required_outputs=(NodeOutputRef(node_id="reviewer", output_slot="result"),),
    )


def builtin_workflows(
    refs: dict[str, AgentDefinitionRef], *, native_sandbox: bool
) -> tuple[WorkflowDefinitionSource, ...]:
    """All packaged templates from exact, already-published Agent versions."""

    return (
        builtin_direct_workflow(refs["builtin_direct"]),
        builtin_explore_implement_verify(
            refs["builtin_explorer"],
            refs["builtin_coder"],
            refs["builtin_reviewer"],
            native_sandbox=native_sandbox,
        ),
    )


def available_builtin_workflows(
    refs: dict[str, AgentDefinitionRef], *, native_sandbox: bool
) -> tuple[WorkflowDefinitionSource, ...]:
    """Construct every template whose exact packaged Agent versions are published."""

    values = []
    if "builtin_direct" in refs:
        values.append(builtin_direct_workflow(refs["builtin_direct"]))
    if {"builtin_explorer", "builtin_coder", "builtin_reviewer"} <= refs.keys():
        values.append(
            builtin_explore_implement_verify(
                refs["builtin_explorer"],
                refs["builtin_coder"],
                refs["builtin_reviewer"],
                native_sandbox=native_sandbox,
            )
        )
    return tuple(values)


def visible_builtin_workflows(
    refs: dict[str, AgentDefinitionRef], *, native_sandbox: bool
) -> tuple[WorkflowDefinitionSource, ...]:
    """All templates, using explicit unresolved refs until packaged Agents publish.

    The placeholder is display/validation evidence only. The compiler reports
    the missing exact Agent version, so plain run/publish remains fail-closed and
    actionable without hiding the packaged Workflow source.
    """

    visible_refs = dict(refs)
    for definition_id in (
        "builtin_direct",
        "builtin_explorer",
        "builtin_coder",
        "builtin_reviewer",
    ):
        visible_refs.setdefault(
            definition_id,
            AgentDefinitionRef(
                definition_id=definition_id,
                version_id=f"adev_unpublished_{definition_id}",
                content_hash="0" * 64,
            ),
        )
    return builtin_workflows(visible_refs, native_sandbox=native_sandbox)
