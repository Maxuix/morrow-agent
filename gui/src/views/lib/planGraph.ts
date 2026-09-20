/**
 * Plan draft → graph mapping. Pure and deterministic (node-env tested).
 *
 * The planning-phase review reads topology from the persisted
 * `TaskPlanViewWire.draft.draft.source`; titles/agents/models join from
 * `view.version`. Diagnostics are attached per node so an INVALID draft still
 * renders as a reviewable graph — the graph never fabricates run state: every
 * node displays the presentation-only `planned` status ("尚未执行").
 */
import type {
  DraftVersionWire,
  PlanNodeWire,
  WorkflowDefinitionSourceWire,
  WorkflowDraftDiagnosticWire,
} from '../../api/types'
import { agentSelectionLabel } from './taskPlan'
import {
  layeredPositions,
  GRAPH_GRID_X,
  GRAPH_GRID_Y,
  type GraphLayout,
  type RevisionEdge,
} from './graph'

export interface PlanNodeInfo {
  node_id: string
  title: string | null
  /** Raw agent selection value, e.g. `preset:general` or `custom:<id>`. */
  agent: string
  /** Resolved `provider/model` label, or null when the server has none. */
  model: string | null
  objective: string
  access_mode: 'read' | 'write' | null
  /** This node's `result` output is the workflow's final delivery. */
  finalDelivery: boolean
  /** Error-severity diagnostics attached to this node. */
  errorCount: number
  parents: string[]
}

export interface PlanGraph {
  name: string
  nodes: PlanNodeInfo[]
  edges: RevisionEdge[]
  /** Edges referencing missing endpoints: kept out of the canvas, surfaced
   *  through the diagnostics list (never silently ignored). */
  danglingEdgeCount: number
}

export function nodeCompletionCriteria(
  source: WorkflowDefinitionSourceWire,
  nodeId: string,
): string[] {
  const node = source.nodes.find((item) => item.node_id === nodeId)
  if (node === undefined) return []
  const constraint = node.task_contract.constraints.find((item) =>
    item.startsWith('Completion criteria: '),
  )
  return constraint === undefined
    ? []
    : constraint.slice('Completion criteria: '.length).split('; ').filter(Boolean)
}

/** Dependency parents of one node from the draft source edges. */
export function planNodeParents(source: WorkflowDefinitionSourceWire, nodeId: string): string[] {
  return source.edges.filter((edge) => edge.to_node_id === nodeId).map((edge) => edge.from_node_id)
}

export function buildPlanGraph(
  source: WorkflowDefinitionSourceWire,
  version: DraftVersionWire | null,
  diagnostics: WorkflowDraftDiagnosticWire[],
): PlanGraph {
  const metadata = version?.node_metadata ?? {}
  const selections = version?.execution_selections ?? {}
  const nodeIds = new Set(source.nodes.map((node) => node.node_id))

  const nodes: PlanNodeInfo[] = source.nodes.map((node) => {
    const info = metadata[node.node_id]
    const selection = selections[node.node_id]
    const objective = node.task_contract.objective
    return {
      node_id: node.node_id,
      title: info?.title ?? objective.slice(0, 40),
      agent: info?.agent_selection ?? 'preset:general',
      model:
        selection === undefined
          ? null
          : `${selection.model.provider_id}/${selection.model.model_id}`,
      objective,
      access_mode: node.access_mode,
      finalDelivery: source.required_outputs.some(
        (output) => output.node_id === node.node_id && output.output_slot === 'result',
      ),
      errorCount: diagnostics.filter(
        (item) => item.node_id === node.node_id && item.severity === 'error',
      ).length,
      parents: source.edges
        .filter((edge) => edge.to_node_id === node.node_id)
        .map((edge) => edge.from_node_id),
    }
  })
  nodes.sort((a, b) => a.node_id.localeCompare(b.node_id))

  const edges: RevisionEdge[] = []
  let danglingEdgeCount = 0
  for (const edge of source.edges) {
    if (!nodeIds.has(edge.from_node_id) || !nodeIds.has(edge.to_node_id)) {
      danglingEdgeCount += 1
      continue
    }
    edges.push({ from_node_id: edge.from_node_id, to_node_id: edge.to_node_id })
  }

  return { name: source.name, nodes, edges, danglingEdgeCount }
}

/** Deterministic layered layout for a plan graph; every node reads `planned`. */
export function buildPlanLayout(graph: PlanGraph): GraphLayout {
  const positions = layeredPositions(
    graph.nodes.map((node) => node.node_id),
    graph.edges,
  )
  const nodes = graph.nodes.map((info) => {
    const position = positions.get(info.node_id) ?? { layer: 0, row: 0 }
    return {
      id: info.node_id,
      x: position.layer * GRAPH_GRID_X,
      y: position.row * GRAPH_GRID_Y,
      data: {
        node_id: info.node_id,
        status: 'planned' as const,
        approval_pending: false,
        attempt: 0,
        model: info.model,
        objective: info.objective,
        access_mode: info.access_mode,
        title: info.title,
        agentLabel: agentSelectionLabel(info.agent),
        finalDelivery: info.finalDelivery,
        errorCount: info.errorCount,
      },
    }
  })
  const edges = graph.edges.map((edge) => ({
    id: `${edge.from_node_id}->${edge.to_node_id}`,
    source: edge.from_node_id,
    target: edge.to_node_id,
  }))
  return { nodes, edges }
}

export type DependencyValidation =
  | 'ok'
  | 'self'
  | 'duplicate'
  | 'cycle'
  | 'missing_source'
  | 'missing_target'

/**
 * Client-side pre-check for a would-be dependency edge `from → to` (meaning
 * `to` depends on `from`). Obvious mistakes are blocked early; the server
 * remains the final validator.
 */
export function validateDependencyEdge(
  edges: ReadonlyArray<{ from_node_id: string; to_node_id: string }>,
  nodeIds: ReadonlySet<string>,
  from: string,
  to: string,
): DependencyValidation {
  if (from === to) return 'self'
  if (!nodeIds.has(from)) return 'missing_source'
  if (!nodeIds.has(to)) return 'missing_target'
  if (edges.some((edge) => edge.from_node_id === from && edge.to_node_id === to)) {
    return 'duplicate'
  }
  // A cycle appears when `to` can already reach `from` following edges.
  const outgoing = new Map<string, string[]>()
  for (const edge of edges) {
    outgoing.set(edge.from_node_id, [...(outgoing.get(edge.from_node_id) ?? []), edge.to_node_id])
  }
  const seen = new Set<string>([to])
  const queue = [to]
  while (queue.length > 0) {
    const current = queue.shift() as string
    if (current === from) return 'cycle'
    for (const next of outgoing.get(current) ?? []) {
      if (!seen.has(next)) {
        seen.add(next)
        queue.push(next)
      }
    }
  }
  return 'ok'
}

/** Direct + transitive dependents of a node — the delete-impact preview. */
export function downstreamOf(
  edges: ReadonlyArray<{ from_node_id: string; to_node_id: string }>,
  nodeId: string,
): string[] {
  const outgoing = new Map<string, string[]>()
  for (const edge of edges) {
    outgoing.set(edge.from_node_id, [...(outgoing.get(edge.from_node_id) ?? []), edge.to_node_id])
  }
  const seen = new Set<string>()
  const queue = [...(outgoing.get(nodeId) ?? [])]
  while (queue.length > 0) {
    const current = queue.shift() as string
    if (seen.has(current)) continue
    seen.add(current)
    queue.push(...(outgoing.get(current) ?? []))
  }
  return [...seen].sort()
}

/** Bounded `^[a-z][a-z0-9_-]{0,63}$` node identity for new nodes. */
export function nextPlanNodeId(existing: string[]): string {
  let ordinal = existing.length + 1
  let candidate = `node_${ordinal}`
  while (existing.includes(candidate)) {
    ordinal += 1
    candidate = `node_${ordinal}`
  }
  return candidate
}

const NEW_NODE_RESPONSIBILITY: Record<string, 'implementation' | 'research' | 'review'> = {
  'preset:general': 'implementation',
  'preset:explore': 'research',
  'preset:review': 'review',
}

export function planNodeFromTask(
  task: string,
  agent: string,
  dependsOn: string[],
  existing: string[],
): PlanNodeWire {
  const firstLine = task.trim().split('\n', 1)[0] ?? ''
  return {
    node_id: nextPlanNodeId(existing),
    title: firstLine.slice(0, 24) || '新节点',
    task: task.trim(),
    agent,
    responsibility: NEW_NODE_RESPONSIBILITY[agent] ?? 'implementation',
    depends_on: [...new Set(dependsOn)],
    completion: [`${firstLine.slice(0, 80) || '本节点任务'}已完成并有可核对结果`],
  }
}
