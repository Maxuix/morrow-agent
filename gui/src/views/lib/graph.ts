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
 */
import type { NodeViewWire, WorkflowStatus } from '../../api/types'

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

export interface GraphNodeModel extends Record<string, unknown> {
  node_id: string
  status: WorkflowStatus
  approval_pending: boolean
  attempt: number
  model: string | null
  objective: string | null
  access_mode: 'read' | 'write' | null
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

/**
 * Map revision nodes/edges to positioned graph nodes, joining run state by
 * `node_id`. A revision node with no NodeRun yet renders as `queued`.
 */
export function buildGraphLayout(graph: RevisionGraph, nodeViews: NodeViewWire[]): GraphLayout {
  const stateByNodeId = new Map(nodeViews.map((view) => [view.node.node_id, view]))
  const depth = computeDepths(graph)
  const layerSizes = new Map<number, number>()

  const nodes: PositionedNode[] = graph.nodes.map((info) => {
    const layer = depth.get(info.node_id) ?? 0
    const row = layerSizes.get(layer) ?? 0
    layerSizes.set(layer, row + 1)
    const view = stateByNodeId.get(info.node_id)
    return {
      id: info.node_id,
      x: layer * GRAPH_GRID_X,
      y: row * GRAPH_GRID_Y,
      data: {
        node_id: info.node_id,
        status: view?.node.status ?? 'queued',
        approval_pending: view?.approval_pending ?? false,
        attempt: view?.node.attempt ?? 0,
        model: info.model,
        objective: info.objective,
        access_mode: info.access_mode,
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

/** Longest-path layering; cycles fall back to layer 0 deterministically. */
function computeDepths(graph: RevisionGraph): Map<string, number> {
  const depth = new Map<string, number>()
  const nodeIds = new Set(graph.nodes.map((node) => node.node_id))
  const incoming = new Map<string, number>()
  const outgoing = new Map<string, string[]>()
  for (const id of nodeIds) {
    incoming.set(id, 0)
    outgoing.set(id, [])
  }
  for (const edge of graph.edges) {
    if (!nodeIds.has(edge.from_node_id) || !nodeIds.has(edge.to_node_id)) continue
    incoming.set(edge.to_node_id, (incoming.get(edge.to_node_id) ?? 0) + 1)
    outgoing.get(edge.from_node_id)?.push(edge.to_node_id)
  }

  // Kahn's algorithm over node_id-sorted ids keeps the layering deterministic.
  const ready = [...nodeIds].filter((id) => incoming.get(id) === 0).sort()
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
  for (const id of nodeIds) {
    if (!depth.has(id)) depth.set(id, 0)
  }
  return depth
}
