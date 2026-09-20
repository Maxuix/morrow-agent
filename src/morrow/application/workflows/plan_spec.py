"""Deterministic PlanSpec lowering into the existing Compiler's source language."""

from morrow.core.agent_presets import PRESET_ROLE_BY_ID
from morrow.core.domain import canonical_json_bytes, sha256_digest
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
    WorkflowDefinitionSource,
    WorkflowEdge,
)
from morrow.core.workflows.planning import NodePlanningMetadata

LEAF_CONSTRAINT = (
    "Execute only this assigned node. Do not delegate outside the graph, start a workflow, "
    "or modify its plan. Report a bounded replan request when blocked."
)


def selection_key(entry):
    identity = entry.version.source.definition_id
    role = PRESET_ROLE_BY_ID.get(identity)
    return f"preset:{role}" if role else f"custom:{identity}"


def normalize(spec, task, binding_id, entries, *, constraints=()):
    catalog = {selection_key(e): e for e in entries}
    nodes, edges, required, metadata = [], [], [], {}
    merged = tuple(dict.fromkeys((*task.constraints, *constraints, LEAF_CONSTRAINT)))
    # TaskContract.constraints is max_length=32 and each node then appends one
    # "Completion criteria: ..." entry, so merged must leave that slot free.
    if len(merged) >= 32:
        raise ValueError("constraint_capacity")
    for item in spec.nodes:
        entry = catalog.get(item.agent)
        if entry is None:
            raise ValueError("agent_unavailable")
        responsibility = "review" if item.agent == "preset:review" else item.responsibility
        if item.agent == "preset:explore" and responsibility not in {"research", "synthesis"}:
            raise ValueError("agent_responsibility_mismatch")
        review = responsibility == "review"
        access = (
            "read"
            if review or item.agent == "preset:explore"
            else entry.version.source.access_mode_ceiling
        )
        outputs = [OutputContract(slot="result")]
        if review:
            outputs.append(OutputContract(slot="review", kind="ReviewReport"))
            required.append(NodeOutputRef(node_id=item.node_id, output_slot="review"))
        bindings = [
            WorkflowInputBinding(
                source="workflow_input",
                input_name="task",
                accepts=TaskContractRef(),
                workflow_input="task",
            )
        ]
        for parent in sorted(set(item.depends_on)):
            edges.append(WorkflowEdge(from_node_id=parent, to_node_id=item.node_id))
            bindings.append(
                NodeOutputBinding(
                    source="node_output",
                    input_name=f"input_{parent}",
                    accepts=ContractRef(kind="TextResult"),
                    node_output=NodeOutputRef(node_id=parent, output_slot="result"),
                )
            )
        nodes.append(
            AgentNodeSource(
                node_id=item.node_id,
                agent_definition_ref=entry.ref,
                task_contract=TaskContract(
                    objective=item.task,
                    scope=task.scope,
                    constraints=(*merged, "Completion criteria: " + "; ".join(item.completion)),
                    source_refs=task.source_refs,
                ),
                input_bindings=tuple(bindings),
                output_contracts=tuple(outputs),
                access_mode=access,
            )
        )
        metadata[item.node_id] = NodePlanningMetadata(
            title=item.title, responsibility=responsibility, agent_selection=item.agent
        )
    required.extend(
        NodeOutputRef(node_id=x, output_slot="result") for x in sorted(set(spec.deliverables))
    )
    source = WorkflowDefinitionSource(
        workflow_definition_id="task_" + sha256_digest(canonical_json_bytes(binding_id))[:32],
        name=task.objective[:128],
        nodes=tuple(nodes),
        edges=tuple(edges),
        required_outputs=tuple(required),
    )
    return source, metadata


def pin_past(base, candidate, past_ids):
    """Keep admitted/completed/inherited nodes from base; future comes from candidate."""

    past_ids = set(past_ids)
    past_nodes = tuple(node for node in base.nodes if node.node_id in past_ids)
    future_nodes = tuple(node for node in candidate.nodes if node.node_id not in past_ids)
    seen, edges = set(), []
    for edge in (*base.edges, *candidate.edges):
        key = (edge.from_node_id, edge.to_node_id)
        if key in seen:
            continue
        if edge.to_node_id in past_ids:
            if edge.from_node_id in past_ids and any(
                item.from_node_id == edge.from_node_id and item.to_node_id == edge.to_node_id
                for item in base.edges
            ):
                seen.add(key)
                edges.append(edge)
            continue
        seen.add(key)
        edges.append(edge)
    required = tuple(item for item in candidate.required_outputs if item.node_id not in past_ids)
    if not required:
        required = tuple(item for item in base.required_outputs if item.node_id in past_ids)
    return base.model_copy(
        update={
            "nodes": (*past_nodes, *future_nodes),
            "edges": tuple(edges),
            "required_outputs": required,
            "default_budget": base.default_budget,
        }
    )


def enforce_review(source, metadata):
    """A manual edit cannot remove the structural report gate while retaining its review node."""
    nodes, required = [], set(source.required_outputs)
    for node in source.nodes:
        info = metadata.get(node.node_id)
        review = (info and info.responsibility == "review") or (
            PRESET_ROLE_BY_ID.get(node.agent_definition_ref.definition_id) == "review"
        )
        if review:
            outputs = {o.slot: o for o in node.output_contracts}
            outputs["review"] = OutputContract(slot="review", kind="ReviewReport")
            node = node.model_copy(
                update={"output_contracts": tuple(outputs.values()), "access_mode": "read"}
            )
            required.add(NodeOutputRef(node_id=node.node_id, output_slot="review"))
        nodes.append(node)
    return WorkflowDefinitionSource.model_validate(
        {
            **source.model_dump(mode="json"),
            "nodes": nodes,
            "required_outputs": tuple(required),
        }
    )
