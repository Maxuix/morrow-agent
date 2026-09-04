"""Deterministic patch preview: structural diff plus C8 risk classification.

Both sides are pure compiled-graph comparisons — the frozen base Revision
against the compiled candidate — with no IO and no catalog access, so the CLI
and the Core API render identical previews. Classification follows the C8
dimensions in ``docs/decisions/stage-8-runtime-contracts.md``: any listed
elevation or anything not classifiable is not low risk.
"""

from __future__ import annotations

from dataclasses import dataclass

from morrow.core.workflows.contracts import NodeOutputBinding
from morrow.core.workflows.definitions import AgentNode, CompiledWorkflow

REPORT_KINDS = frozenset({"TestReport", "ReviewReport"})

RISK_LOW = "low"
RISK_ELEVATED = "elevated"


@dataclass(frozen=True)
class PatchDiffPreview:
    added_node_ids: tuple[str, ...]
    removed_node_ids: tuple[str, ...]
    changed_node_ids: tuple[str, ...]
    added_edges: tuple[str, ...]
    removed_edges: tuple[str, ...]
    required_outputs_changed: bool
    budget_changed: bool


@dataclass(frozen=True)
class PatchRiskPreview:
    level: str
    reasons: tuple[str, ...]


def _edge_label(edge) -> str:
    return f"{edge.from_node_id}->{edge.to_node_id}"


def _writers_in_order(workflow: CompiledWorkflow) -> tuple[str, ...]:
    """Relative writer order from the DAG's stable topological sort."""

    writers = {node.node_id for node in workflow.nodes if node.access_mode == "write"}
    incoming = {node.node_id: 0 for node in workflow.nodes}
    consumers: dict[str, list[str]] = {node.node_id: [] for node in workflow.nodes}
    for edge in workflow.edges:
        incoming[edge.to_node_id] += 1
        consumers[edge.from_node_id].append(edge.to_node_id)
    ready = sorted(node_id for node_id in incoming if incoming[node_id] == 0)
    order: list[str] = []
    while ready:
        node_id = ready.pop(0)
        order.append(node_id)
        for follower in sorted(consumers[node_id]):
            incoming[follower] -= 1
            if incoming[follower] == 0:
                ready.append(follower)
        ready.sort()
    return tuple(node_id for node_id in order if node_id in writers)


def _report_binding_sources(node: AgentNode) -> frozenset[tuple[str, str]]:
    """(node_id, slot) references this node consumes that carry report kinds."""

    return frozenset(
        (binding.node_output.node_id, binding.node_output.output_slot)
        for binding in node.input_bindings
        if isinstance(binding, NodeOutputBinding) and binding.accepts.kind in REPORT_KINDS
    )


def _tool_floor(node: AgentNode) -> frozenset[tuple[str, str]]:
    return frozenset((item.name, item.requirement) for item in node.resolved_tool_requirements)


def _cap_relaxed(old: int | float | None, new: int | float | None) -> bool:
    """An explicit cap/deadline may only shrink on its own; removal or growth
    is a C8 relaxation. Absent limits stay absent without flagging."""

    if old is None:
        return False
    return new is None or new > old


def diff_compiled(
    base: CompiledWorkflow, candidate: CompiledWorkflow
) -> PatchDiffPreview:
    base_nodes = {node.node_id: node for node in base.nodes}
    candidate_nodes = {node.node_id: node for node in candidate.nodes}
    base_edges = {_edge_label(edge) for edge in base.edges}
    candidate_edges = {_edge_label(edge) for edge in candidate.edges}
    return PatchDiffPreview(
        added_node_ids=tuple(sorted(candidate_nodes.keys() - base_nodes.keys())),
        removed_node_ids=tuple(sorted(base_nodes.keys() - candidate_nodes.keys())),
        changed_node_ids=tuple(
            sorted(
                node_id
                for node_id in base_nodes.keys() & candidate_nodes.keys()
                if base_nodes[node_id] != candidate_nodes[node_id]
            )
        ),
        added_edges=tuple(sorted(candidate_edges - base_edges)),
        removed_edges=tuple(sorted(base_edges - candidate_edges)),
        required_outputs_changed=base.required_outputs != candidate.required_outputs,
        budget_changed=base.budget != candidate.budget,
    )


def classify_patch_risk(
    base: CompiledWorkflow, candidate: CompiledWorkflow
) -> PatchRiskPreview:
    """C8 risk dimensions; unknown change classes never classify as low."""

    reasons: list[str] = []
    base_nodes = {node.node_id: node for node in base.nodes}
    candidate_nodes = {node.node_id: node for node in candidate.nodes}
    removed = base_nodes.keys() - candidate_nodes.keys()
    if removed:
        reasons.append("node_removed")
        for node_id in sorted(removed):
            kinds = {contract.kind for contract in base_nodes[node_id].output_contracts}
            if kinds & REPORT_KINDS:
                reasons.append("review_or_test_gate_removed")
                break
    if base.required_outputs != candidate.required_outputs:
        reasons.append("required_outputs_retargeted")

    base_edges = {(edge.from_node_id, edge.to_node_id) for edge in base.edges}
    candidate_edges = {(edge.from_node_id, edge.to_node_id) for edge in candidate.edges}
    removed_edges = base_edges - candidate_edges
    if removed_edges:
        candidate_bindings = {
            (binding.node_output.node_id, node.node_id)
            for node in candidate.nodes
            for binding in node.input_bindings
            if isinstance(binding, NodeOutputBinding)
        }
        if any(edge not in candidate_bindings for edge in removed_edges):
            reasons.append("control_edge_removed")

    for node_id in sorted(base_nodes.keys() & candidate_nodes.keys()):
        old = base_nodes[node_id]
        new = candidate_nodes[node_id]
        if old == new:
            continue
        if old.agent_definition_ref != new.agent_definition_ref:
            reasons.append("role_replaced")
        if old.resolved_model_ref != new.resolved_model_ref:
            reasons.append("provider_model_boundary_changed")
        if old.conversation_scope != new.conversation_scope:
            reasons.append("conversation_scope_changed")
        if old.access_mode != new.access_mode and new.access_mode == "write":
            reasons.append("permission_widened")
        if not _tool_floor(new) <= _tool_floor(old):
            reasons.append("permission_widened")
        if old.output_contracts != new.output_contracts:
            reasons.append("output_contract_relaxed")
        if _report_binding_sources(old) - _report_binding_sources(new):
            reasons.append("report_dependency_removed")
        if _cap_relaxed(
            old.max_agent_generation_requests, new.max_agent_generation_requests
        ) or _cap_relaxed(
            old.declared_node_max_agent_generation_requests,
            new.declared_node_max_agent_generation_requests,
        ):
            reasons.append("cap_or_deadline_relaxed")

    base_providers = {node.resolved_model_ref.provider_id for node in base.nodes}
    if {node.resolved_model_ref.provider_id for node in candidate.nodes} - base_providers:
        reasons.append("provider_model_boundary_changed")

    retained = base_nodes.keys() & candidate_nodes.keys()
    base_writers = _writers_in_order(base)
    candidate_writers = _writers_in_order(candidate)
    if tuple(node_id for node_id in base_writers if node_id in retained) != tuple(
        node_id for node_id in candidate_writers if node_id in retained
    ):
        reasons.append("writer_order_changed")

    budget = base.budget
    candidate_budget = candidate.budget
    if (
        _cap_relaxed(
            budget.max_agent_generation_requests, candidate_budget.max_agent_generation_requests
        )
        or _cap_relaxed(
            budget.default_node_max_agent_generation_requests,
            candidate_budget.default_node_max_agent_generation_requests,
        )
        or _cap_relaxed(
            budget.admission_timeout_seconds, candidate_budget.admission_timeout_seconds
        )
    ):
        reasons.append("cap_or_deadline_relaxed")

    unique = tuple(dict.fromkeys(reasons))
    return PatchRiskPreview(
        level=RISK_ELEVATED if unique else RISK_LOW,
        reasons=unique,
    )
