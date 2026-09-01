"""Pure deterministic Workflow compilation: normalize, validate and hash without IO.

The compiler owns every execution-affecting decision that turns a desired
WorkflowDefinitionSource into one canonical CompiledWorkflow candidate: graph
shape, exact reference resolution, publish-time model freeze, tool-requirement
merge under fixed precedence and per-node budget freeze. It never touches YAML,
SQLite, clocks, networks or processes, and it never allocates identity; the sole
publication service consumes the candidate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import ValidationError

from morrow.core.agent_definitions import AgentDefinitionVersion, ToolRequirement
from morrow.core.models import ModelRef
from morrow.core.workflows.definitions import (
    AgentNode,
    CompiledWorkflow,
    WorkflowDefinitionSource,
    compiled_content_hash,
)

if TYPE_CHECKING:
    from morrow.application.agent_definitions.publication import DefinitionCatalog

COMPILER_VERSION = "stage7-v1"


class DiagnosticSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class CompileDiagnostic:
    severity: DiagnosticSeverity
    code: str
    message: str


@dataclass(frozen=True)
class CompilationResult:
    """One typed compile outcome; a None candidate means errors are present."""

    candidate: CompiledWorkflow | None
    diagnostics: tuple[CompileDiagnostic, ...]

    @property
    def errors(self) -> tuple[CompileDiagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.severity is DiagnosticSeverity.ERROR)

    @property
    def content_hash(self) -> str | None:
        return compiled_content_hash(self.candidate) if self.candidate is not None else None


class WorkflowCompilationError(ValueError):
    """Actionable compile rejection carrying the full typed diagnostic set."""

    def __init__(self, diagnostics: tuple[CompileDiagnostic, ...]):
        self.diagnostics = tuple(d for d in diagnostics if d.severity is DiagnosticSeverity.ERROR)
        super().__init__("; ".join(f"{d.code}: {d.message}" for d in self.diagnostics))


def compile_workflow(
    source: WorkflowDefinitionSource,
    *,
    agent_versions: dict[str, AgentDefinitionVersion | None],
    catalog: DefinitionCatalog,
    active_model: ModelRef | None,
    compiler_version: str = COMPILER_VERSION,
) -> CompilationResult:
    """Compile one desired source into a canonical candidate using only typed inputs."""
    diagnostics: list[CompileDiagnostic] = []
    entry_nodes, terminal_nodes = _check_graph(source, diagnostics)
    compiled: list[AgentNode] = []
    for node in source.nodes:
        version = agent_versions.get(node.agent_definition_ref.version_id)
        ref = node.agent_definition_ref
        if (
            version is None
            or version.source.definition_id != ref.definition_id
            or version.content_hash != ref.content_hash
        ):
            diagnostics.append(
                CompileDiagnostic(
                    DiagnosticSeverity.ERROR,
                    "agent_version_unresolved",
                    f"node {node.node_id}: exact AgentDefinitionVersion {ref.version_id} is missing"
                    " or does not match its published identity; publish the definition and"
                    " reference the exact version",
                )
            )
            continue
        model = _resolve_model(version, node.node_id, active_model, diagnostics)
        tools = _merge_tool_requirements(version, node, catalog, diagnostics)
        if model is None or tools is None:
            continue
        ceiling = version.source.max_agent_generation_requests
        requested = (
            node.max_agent_generation_requests
            or source.default_budget.default_node_max_agent_generation_requests
        )
        declared = min(requested, ceiling) if ceiling is not None else requested
        compiled.append(
            AgentNode(
                **node.model_dump(),
                resolved_tool_requirements=tools,
                resolved_model_ref=model,
                declared_node_max_agent_generation_requests=declared,
            )
        )
    if any(d.severity is DiagnosticSeverity.ERROR for d in diagnostics):
        return CompilationResult(None, tuple(diagnostics))
    try:
        candidate = CompiledWorkflow(
            **source.model_dump(exclude={"nodes", "default_budget"}),
            nodes=tuple(compiled),
            entry_nodes=entry_nodes,
            terminal_nodes=terminal_nodes,
            budget=source.default_budget,
            compiler_version=compiler_version,
        )
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()[:3]
        )
        diagnostics.append(
            CompileDiagnostic(
                DiagnosticSeverity.ERROR,
                "structure_invalid",
                f"compiled graph violates its structural contract: {details}",
            )
        )
        return CompilationResult(None, tuple(diagnostics))
    return CompilationResult(candidate, tuple(diagnostics))


def _check_graph(source: WorkflowDefinitionSource, diagnostics: list[CompileDiagnostic]):
    node_ids = {node.node_id for node in source.nodes}
    edges = [(edge.from_node_id, edge.to_node_id) for edge in source.edges]
    for from_id, to_id in edges:
        if from_id not in node_ids or to_id not in node_ids or from_id == to_id:
            diagnostics.append(
                CompileDiagnostic(
                    DiagnosticSeverity.ERROR,
                    "edge_endpoint_invalid",
                    f"edge {from_id} -> {to_id} references an unknown or identical node",
                )
            )
    # Kahn's topological walk; any remainder is on a cycle.
    incoming_count = {node_id: 0 for node_id in node_ids}
    consumers: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for from_id, to_id in edges:
        if from_id in node_ids and to_id in node_ids and from_id != to_id:
            incoming_count[to_id] += 1
            consumers[from_id].append(to_id)
    ready = sorted(node_id for node_id, count in incoming_count.items() if count == 0)
    visited = 0
    while ready:
        current = ready.pop(0)
        visited += 1
        for target in sorted(consumers[current]):
            incoming_count[target] -= 1
            if incoming_count[target] == 0:
                ready.append(target)
    if visited < len(node_ids):
        cycle = sorted(node_id for node_id, count in incoming_count.items() if count > 0)
        diagnostics.append(
            CompileDiagnostic(
                DiagnosticSeverity.ERROR,
                "graph_cycle",
                f"declared edges form a cycle involving {', '.join(cycle)}; remove one edge"
                " so the graph is a DAG",
            )
        )
    if len(node_ids) > 1:
        parent = {node_id: node_id for node_id in node_ids}

        def find(node_id: str) -> str:
            while parent[node_id] != node_id:
                parent[node_id] = parent[parent[node_id]]
                node_id = parent[node_id]
            return node_id

        for from_id, to_id in edges:
            if from_id in node_ids and to_id in node_ids:
                parent[find(from_id)] = find(to_id)
        members: dict[str, list[str]] = {}
        for node_id in sorted(node_ids):
            members.setdefault(find(node_id), []).append(node_id)
        # Largest component wins; ties break lexicographically so the named
        # unattached set never depends on set iteration order.
        components = sorted(members.values(), key=lambda group: (-len(group), group))
        if len(components) > 1:
            unattached = sorted(n for group in components[1:] for n in group)
            diagnostics.append(
                CompileDiagnostic(
                    DiagnosticSeverity.ERROR,
                    "graph_disconnected",
                    f"nodes {', '.join(unattached)} are disconnected from the main component;"
                    " add an explicit control edge or remove them",
                )
            )
    consumed = {(ref.node_id, ref.output_slot) for ref in source.required_outputs}
    for node in source.nodes:
        for binding in node.input_bindings:
            if binding.source == "node_output":
                consumed.add((binding.node_output.node_id, binding.node_output.output_slot))
    for node in source.nodes:
        required_slots = {s.slot for s in node.output_contracts if s.required_for_node_completion}
        if required_slots and not any((node.node_id, slot) in consumed for slot in required_slots):
            diagnostics.append(
                CompileDiagnostic(
                    DiagnosticSeverity.WARNING,
                    "unconsumed_outputs",
                    f"node {node.node_id} outputs are neither bound downstream nor exported;"
                    " the node still executes and its failure fails the Workflow",
                )
            )
    writers = sorted(node.node_id for node in source.nodes if node.access_mode == "write")
    if len(writers) > 1:
        diagnostics.append(
            CompileDiagnostic(
                DiagnosticSeverity.WARNING,
                "independent_writers",
                f"nodes {', '.join(writers)} all hold write access; the Stage 7 Scheduler"
                " serializes them, but consider splitting or ordering write work explicitly",
            )
        )
    entry_nodes = tuple(sorted(node_ids - {to_id for _, to_id in edges}))
    terminal_nodes = tuple(sorted(node_ids - {from_id for from_id, _ in edges}))
    return entry_nodes, terminal_nodes


def _resolve_model(
    version: AgentDefinitionVersion,
    node_id: str,
    active_model: ModelRef | None,
    diagnostics: list[CompileDiagnostic],
) -> ModelRef | None:
    selection = version.source.model_selection
    if isinstance(selection, ModelRef):
        return selection
    if active_model is None:
        diagnostics.append(
            CompileDiagnostic(
                DiagnosticSeverity.ERROR,
                "model_unavailable",
                f"node {node_id}: model_selection=invoking_active cannot resolve because no"
                " active model is configured; configure one before publishing",
            )
        )
        return None
    return active_model


def _merge_tool_requirements(
    version: AgentDefinitionVersion,
    node,
    catalog: DefinitionCatalog,
    diagnostics: list[CompileDiagnostic],
) -> tuple[ToolRequirement, ...] | None:
    """Merge Definition and node declarations under the fixed Stage 7 precedence."""
    declared = {item.name: item.requirement for item in version.source.tool_requirements}
    overlay = {item.name: item.requirement for item in node.tool_requirements or ()}
    ok = True
    for name in sorted(set(overlay) - set(declared)):
        diagnostics.append(
            CompileDiagnostic(
                DiagnosticSeverity.ERROR,
                "tool_not_declared",
                f"node {node.node_id}: tool {name} is outside the declared set of Agent"
                f" definition {version.source.definition_id}; a node may only narrow its"
                " definition's declared tools",
            )
        )
        ok = False
    if node.access_mode == "write" and version.source.access_mode_ceiling == "read":
        diagnostics.append(
            CompileDiagnostic(
                DiagnosticSeverity.ERROR,
                "access_mode_escalation",
                f"node {node.node_id}: access_mode=write exceeds the read ceiling of Agent"
                f" definition {version.source.definition_id}",
            )
        )
        ok = False
    merged: dict[str, str] = {}
    for name in sorted(declared):
        base = declared[name]
        over = overlay.get(name)
        if over is None:
            merged[name] = base
            continue
        if {base, over} == {"required", "forbidden"}:
            diagnostics.append(
                CompileDiagnostic(
                    DiagnosticSeverity.ERROR,
                    "tool_requirement_conflict",
                    f"node {node.node_id}: tool {name} is both required and forbidden between"
                    " the definition and the node overlay; forbidden always wins, so a"
                    " required-plus-forbidden conflict is rejected",
                )
            )
            ok = False
            continue
        if base == "forbidden" or over == "forbidden":
            merged[name] = "forbidden"
        elif base == "required" or over == "required":
            merged[name] = "required"
        else:
            merged[name] = "optional"
    if not ok:
        return None
    ceiling = (
        "read" if "read" in {node.access_mode, version.source.access_mode_ceiling} else "write"
    )
    frozen: list[ToolRequirement] = []
    for name in sorted(merged):
        requirement = merged[name]
        if requirement == "forbidden":
            frozen.append(ToolRequirement(name=name, requirement="forbidden"))
            continue
        access = catalog.tool_access.get(name)
        permitted = (
            name in catalog.allowed_tools
            and access is not None
            and (ceiling == "write" or access == "read")
        )
        if permitted:
            frozen.append(ToolRequirement(name=name, requirement=requirement))
        elif requirement == "required":
            diagnostics.append(
                CompileDiagnostic(
                    DiagnosticSeverity.ERROR,
                    "required_tool_denied" if access is not None else "required_tool_absent",
                    f"node {node.node_id}: required tool {name} is "
                    + (
                        "denied by task policy or the access_mode ceiling"
                        if access is not None
                        else "absent from the configured tool catalogs"
                    ),
                )
            )
            ok = False
        else:
            diagnostics.append(
                CompileDiagnostic(
                    DiagnosticSeverity.WARNING,
                    "optional_removed",
                    f"node {node.node_id}: optional tool {name} is absent or denied and was"
                    " removed from the frozen evidence",
                )
            )
    return tuple(frozen) if ok else None
