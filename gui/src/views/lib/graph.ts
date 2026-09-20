/**
 * Revision → graph mapping. Pure and deterministic (node-env tested).
 *
 * `RunViewWire.revision` is the full `WorkflowRevision` model dump — flat, not
 * nested under `metadata` (see `run_view_wire` in
 * `src/morrow/server/projections.py`). The GUI treats it as opaque detail
 * data: we extract only the display fields and tolerate missing ones.
 *
 * Layout is a simple layered scheme (Kahn layering by longest dependency
 * depth, ties broken by node_id) so the mapping stays testable without a DOM.
 * `layeredPositions` depends only on node ids and edges: label text never
 * re-arranges the graph, and positions only move when the topology changes.
 */
import type { NodeExecutionWire, NodeViewWire, WorkflowStatus } from '../../api/types'

export interface RevisionEdge {
  from_node_id: string
  to_node_id: string
}

export interface RevisionNodeInfo {
  node_id: string
  /** `provider_id/model_id`, or null when the revision omits the model ref. */
  model: string | null
  /** Truncation is the view's job; this is the full objective text. */
  objective: string | null
  access_mode: 'read' | 'write' | null
}

export interface RevisionGraph {
  name: string | null
  nodes: RevisionNodeInfo[]
  edges: RevisionEdge[]
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function asString(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null
}

/** Extract the display-relevant slice of an opaque revision dump. */
export function parseRevision(revision: Record<string, unknown>): RevisionGraph {
  const nodes: RevisionNodeInfo[] = []
  if (Array.isArray(revision.nodes)) {
    for (const raw of revision.nodes) {
      const node = asRecord(raw)
      const nodeId = node === null ? null : asString(node.node_id)
      if (node === null || nodeId === null) continue
      const modelRef = asRecord(node.resolved_model_ref)
      const provider = modelRef === null ? null : asString(modelRef.provider_id)
      const modelId = modelRef === null ? null : asString(modelRef.model_id)
      const contract = asRecord(node.task_contract)
      const accessMode = asString(node.access_mode)
      nodes.push({
        node_id: nodeId,
        model: provider !== null && modelId !== null ? `${provider}/${modelId}` : null,
        objective: contract === null ? null : asString(contract.objective),
        access_mode: accessMode === 'read' || accessMode === 'write' ? accessMode : null,
      })
    }
  }
  nodes.sort((a, b) => a.node_id.localeCompare(b.node_id))

  const edges: RevisionEdge[] = []
  if (Array.isArray(revision.edges)) {
    for (const raw of revision.edges) {
      const edge = asRecord(raw)
      if (edge === null) continue
      const from = asString(edge.from_node_id)
      const to = asString(edge.to_node_id)
      if (from === null || to === null) continue
      edges.push({ from_node_id: from, to_node_id: to })
    }
  }

  return { name: asString(revision.name), nodes, edges }
}

/** A single-node revision is a Direct run: render a linear card, no canvas. */
export function isDirectRun(graph: RevisionGraph): boolean {
  return graph.nodes.length === 1
}

/**
 * Display status on the canvas. `planned` is a presentation-only marker for
 * plan-review nodes ("尚未执行") — it is deliberately NOT part of the backend
 * `WorkflowStatus` enum and never fakes a run state.
 */
export type NodeDisplayStatus = WorkflowStatus | 'planned'

export interface GraphNodeModel extends Record<string, unknown> {
  node_id: string
  status: NodeDisplayStatus
  approval_pending: boolean
  attempt: number
  model: string | null
  objective: string | null
  access_mode: 'read' | 'write' | null
  /** Per-node execution projection (P02); undefined on pre-P02 servers. */
  execution?: NodeExecutionWire | null
  /** Plan-review extras; run graphs leave them undefined. */
  title?: string | null
  agentLabel?: string | null
  finalDelivery?: boolean
  errorCount?: number
}

export interface PositionedNode {
  id: string
  x: number
  y: number
  data: GraphNodeModel
}

export interface GraphLayout {
  nodes: PositionedNode[]
  edges: { id: string; source: string; target: string }[]
}

export const GRAPH_GRID_X = 240
export const GRAPH_GRID_Y = 112

export interface LayeredPosition {
  layer: number
  row: number
}

/**
 * Longest-path layering over node ids and edges only. Deterministic
 * (node_id-sorted Kahn); cycles fall back to layer 0. Positions stay stable
 * when only node text changes because text is not an input.
 */
export function layeredPositions(
  nodeIds: string[],
  edges: ReadonlyArray<{ from_node_id: string; to_node_id: string }>,
): Map<string, LayeredPosition> {
  const depth = new Map<string, number>()
  const ids = [...new Set(nodeIds)]
  const incoming = new Map<string, number>()
  const outgoing = new Map<string, string[]>()
  for (const id of ids) {
    incoming.set(id, 0)
    outgoing.set(id, [])
  }
  for (const edge of edges) {
    if (!incoming.has(edge.from_node_id) || !incoming.has(edge.to_node_id)) continue
    incoming.set(edge.to_node_id, (incoming.get(edge.to_node_id) ?? 0) + 1)
    outgoing.get(edge.from_node_id)?.push(edge.to_node_id)
  }

  // Kahn's algorithm over node_id-sorted ids keeps the layering deterministic.
  const ready = ids.filter((id) => incoming.get(id) === 0).sort()
  for (const id of ready) depth.set(id, 0)
  while (ready.length > 0) {
    const id = ready.shift() as string
    const next = (depth.get(id) ?? 0) + 1
    for (const target of outgoing.get(id) ?? []) {
      depth.set(target, Math.max(depth.get(target) ?? 0, next))
      const remaining = (incoming.get(target) ?? 1) - 1
      incoming.set(target, remaining)
      if (remaining === 0) ready.push(target)
    }
  }
  // Unreached nodes (only possible via a cycle) land at layer 0.
  for (const id of ids) {
    if (!depth.has(id)) depth.set(id, 0)
  }

  const layerSizes = new Map<number, number>()
  const positions = new Map<string, LayeredPosition>()
  for (const id of [...ids].sort()) {
    const layer = depth.get(id) ?? 0
    const row = layerSizes.get(layer) ?? 0
    layerSizes.set(layer, row + 1)
    positions.set(id, { layer, row })
  }
  return positions
}

export interface BuildGraphLayoutOptions {
  /**
   * Status for nodes with no run state. Run graphs default to `queued`
   * (a revision node without a NodeRun is queued to run); plan review passes
   * `planned` so an unexecuted draft node never reads as "queued to run".
   */
  unexecutedStatus?: NodeDisplayStatus
}

/**
 * Map revision nodes/edges to positioned graph nodes, joining run state by
 * `node_id`. A revision node with no NodeRun renders as `unexecutedStatus`.
 */
export function buildGraphLayout(
  graph: RevisionGraph,
  nodeViews: NodeViewWire[],
  options: BuildGraphLayoutOptions = {},
): GraphLayout {
  const stateByNodeId = new Map(nodeViews.map((view) => [view.node.node_id, view]))
  const unexecutedStatus = options.unexecutedStatus ?? 'queued'
  const positions = layeredPositions(
    graph.nodes.map((node) => node.node_id),
    graph.edges,
  )

  const nodes: PositionedNode[] = graph.nodes.map((info) => {
    const position = positions.get(info.node_id) ?? { layer: 0, row: 0 }
    const view = stateByNodeId.get(info.node_id)
    return {
      id: info.node_id,
      x: position.layer * GRAPH_GRID_X,
      y: position.row * GRAPH_GRID_Y,
      data: {
        node_id: info.node_id,
        status: view?.node.status ?? unexecutedStatus,
        approval_pending: view?.approval_pending ?? false,
        attempt: view?.node.attempt ?? 0,
        model: info.model,
        objective: info.objective,
        access_mode: info.access_mode,
        execution: view?.execution ?? null,
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
