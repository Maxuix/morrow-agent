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

_DEFAULT_BUDGET = WorkflowBudget(
    max_agent_generation_requests=48,
    default_node_max_agent_generation_requests=12,
    admission_timeout_seconds=3600,
    max_concurrency=1,
)


def _task_binding() -> WorkflowInputBinding:
    return WorkflowInputBinding(
        source="workflow_input",
        input_name="task",
        accepts=TaskContractRef(),
        workflow_input="task",
    )


def _node_binding(name: str, kind: str, node_id: str, slot: str) -> NodeOutputBinding:
    return NodeOutputBinding(
        source="node_output",
        input_name=name,
        accepts=ContractRef(kind=kind),
        node_output=NodeOutputRef(node_id=node_id, output_slot=slot),
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


def builtin_explore_implement_verify(
    explorer_ref: AgentDefinitionRef,
    coder_ref: AgentDefinitionRef,
    reviewer_ref: AgentDefinitionRef,
    *,
    native_sandbox: bool,
) -> WorkflowDefinitionSource:
    patch_kind = "ImplementationPatch" if native_sandbox else "TextResult"
    patch_slot = "patch" if native_sandbox else "implementation"
    return WorkflowDefinitionSource(
        workflow_definition_id="builtin_explore_implement_verify",
        name="Explore Implement Verify",
        description="Serial evidence gathering, implementation and independent review.",
        origin="builtin",
        default_budget=_DEFAULT_BUDGET,
        nodes=(
            AgentNodeSource(
                node_id="explorer",
                agent_definition_ref=explorer_ref,
                task_contract=TaskContract(objective="Gather bounded implementation evidence."),
                input_bindings=(_task_binding(),),
                output_contracts=(OutputContract(kind="EvidenceBundle", slot="evidence"),),
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
                    _node_binding("evidence", "EvidenceBundle", "explorer", "evidence"),
                ),
                output_contracts=(
                    OutputContract(kind=patch_kind, slot=patch_slot),
                    OutputContract(kind="TestReport", slot="tests"),
                ),
                access_mode="write",
            ),
            AgentNodeSource(
                node_id="reviewer",
                agent_definition_ref=reviewer_ref,
                task_contract=TaskContract(objective="Review implementation and test evidence."),
                input_bindings=(
                    _task_binding(),
                    _node_binding(patch_slot, patch_kind, "coder", patch_slot),
                    _node_binding("tests", "TestReport", "coder", "tests"),
                ),
                output_contracts=(OutputContract(kind="ReviewReport", slot="review"),),
                access_mode="read",
            ),
        ),
        edges=(
            WorkflowEdge(from_node_id="explorer", to_node_id="coder"),
            WorkflowEdge(from_node_id="coder", to_node_id="reviewer"),
        ),
        required_outputs=(
            NodeOutputRef(node_id="coder", output_slot=patch_slot),
            NodeOutputRef(node_id="reviewer", output_slot="review"),
        ),
    )


def builtin_planned_refactor(
    explorer_ref: AgentDefinitionRef,
    planner_ref: AgentDefinitionRef,
    coder_ref: AgentDefinitionRef,
    reviewer_ref: AgentDefinitionRef,
    *,
    native_sandbox: bool,
) -> WorkflowDefinitionSource:
    source = builtin_explore_implement_verify(
        explorer_ref, coder_ref, reviewer_ref, native_sandbox=native_sandbox
    )
    by_id = {item.node_id: item for item in source.nodes}
    explorer, coder, reviewer = by_id["explorer"], by_id["coder"], by_id["reviewer"]
    patch_slot = coder.output_contracts[0].slot
    planner = AgentNodeSource(
        node_id="planner",
        agent_definition_ref=planner_ref,
        task_contract=TaskContract(objective="Plan a safe, reviewable refactor."),
        input_bindings=(
            _task_binding(),
            _node_binding("evidence", "EvidenceBundle", "explorer", "evidence"),
        ),
        output_contracts=(OutputContract(kind="PlanArtifact", slot="plan"),),
        access_mode="read",
    )
    coder = coder.model_copy(
        update={
            "input_bindings": (
                _task_binding(),
                _node_binding("plan", "PlanArtifact", "planner", "plan"),
            )
        }
    )
    return WorkflowDefinitionSource(
        workflow_definition_id="builtin_planned_refactor",
        name="Planned Refactor",
        description="Explorer, Planner, Coder and Reviewer in one deterministic serial DAG.",
        origin="builtin",
        default_budget=_DEFAULT_BUDGET,
        nodes=(explorer, planner, coder, reviewer),
        edges=(
            WorkflowEdge(from_node_id="explorer", to_node_id="planner"),
            WorkflowEdge(from_node_id="planner", to_node_id="coder"),
            WorkflowEdge(from_node_id="coder", to_node_id="reviewer"),
        ),
        required_outputs=(
            NodeOutputRef(node_id="coder", output_slot=patch_slot),
            NodeOutputRef(node_id="reviewer", output_slot="review"),
        ),
    )


def builtin_parallel_research(
    explorer_ref: AgentDefinitionRef,
    synthesizer_ref: AgentDefinitionRef,
    *,
    fanout: int = 3,
) -> WorkflowDefinitionSource:
    explorers = tuple(
        AgentNodeSource(
            node_id=f"explorer_{index}",
            agent_definition_ref=explorer_ref,
            task_contract=TaskContract(objective=f"Research bounded perspective {index}."),
            input_bindings=(_task_binding(),),
            output_contracts=(OutputContract(kind="EvidenceBundle", slot="evidence"),),
            access_mode="read",
        )
        for index in range(1, fanout + 1)
    )
    synth = AgentNodeSource(
        node_id="synthesizer",
        agent_definition_ref=synthesizer_ref,
        task_contract=TaskContract(objective="Synthesize all research evidence."),
        input_bindings=(
            _task_binding(),
            *(
                _node_binding(
                    f"evidence_{index}", "EvidenceBundle", f"explorer_{index}", "evidence"
                )
                for index in range(1, fanout + 1)
            ),
        ),
        output_contracts=(OutputContract(kind="SynthesisReport", slot="synthesis"),),
        access_mode="read",
    )
    return WorkflowDefinitionSource(
        workflow_definition_id="builtin_parallel_research",
        name="Parallel Research",
        description="Fixed Explorer fan-out and Synthesizer fan-in; Stage 7 executes it serially.",
        origin="builtin",
        default_budget=_DEFAULT_BUDGET,
        nodes=(*explorers, synth),
        edges=tuple(
            WorkflowEdge(from_node_id=item.node_id, to_node_id="synthesizer") for item in explorers
        ),
        required_outputs=(NodeOutputRef(node_id="synthesizer", output_slot="synthesis"),),
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
        builtin_parallel_research(refs["builtin_explorer"], refs["builtin_synthesizer"]),
        builtin_planned_refactor(
            refs["builtin_explorer"],
            refs["builtin_planner"],
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
    if {"builtin_explorer", "builtin_synthesizer"} <= refs.keys():
        values.append(
            builtin_parallel_research(refs["builtin_explorer"], refs["builtin_synthesizer"])
        )
    if {"builtin_explorer", "builtin_planner", "builtin_coder", "builtin_reviewer"} <= refs.keys():
        values.append(
            builtin_planned_refactor(
                refs["builtin_explorer"],
                refs["builtin_planner"],
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
        "builtin_planner",
        "builtin_synthesizer",
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
