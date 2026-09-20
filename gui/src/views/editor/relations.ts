import type {
  AgentNodeSourceWire,
  InputBindingWire,
  NodeOutputRefWire,
  OutputContractWire,
  WorkflowDefinitionSourceWire,
} from '../../api/types'

export interface OutputChoice extends NodeOutputRefWire {
  kind: string
  version: number
}

export interface EdgeImpact {
  edgeId: string
  bindings: Array<{ node_id: string; input_name: string; output_slot: string }>
  hasAlternativePath: boolean
}

export interface NodeImpact {
  nodeId: string
  edgeIds: string[]
  bindings: Array<{ node_id: string; input_name: string; output_slot: string }>
  requiredOutputs: NodeOutputRefWire[]
}

export interface ConnectionEdit {
  fromNodeId: string
  toNodeId: string
  keepControlEdge: boolean
  bindings: Array<{ inputName: string; outputSlot: string }>
}

export interface ConnectionEditResult {
  source: WorkflowDefinitionSourceWire | null
  blockers: string[]
}

export function edgeId(fromNodeId: string, toNodeId: string): string {
  return `${fromNodeId}->${toNodeId}`
}

export function outputId(ref: NodeOutputRefWire): string {
  return `${ref.node_id}.${ref.output_slot}`
}

export function hasControlEdge(
  source: WorkflowDefinitionSourceWire,
  fromNodeId: string,
  toNodeId: string,
): boolean {
  return source.edges.some(edge => edge.from_node_id === fromNodeId && edge.to_node_id === toNodeId)
}

export function outputContract(
  source: WorkflowDefinitionSourceWire,
  ref: NodeOutputRefWire,
): OutputContractWire | null {
  return source.nodes
    .find(node => node.node_id === ref.node_id)
    ?.output_contracts.find(output => output.slot === ref.output_slot) ?? null
}

export function completionOutputs(
  source: WorkflowDefinitionSourceWire,
  producerId?: string,
): OutputChoice[] {
  return source.nodes
    .filter(node => producerId === undefined || node.node_id === producerId)
    .flatMap(node => node.output_contracts
      .filter(output => output.required_for_node_completion)
      .map(output => ({
        node_id: node.node_id,
        output_slot: output.slot,
        kind: output.kind,
        version: output.version,
      })))
}

/** Return the nodes reachable by valid control edges, without following cycles forever. */
export function reachableNodes(
  source: WorkflowDefinitionSourceWire,
  fromNodeId: string,
): Set<string> {
  const nodeIds = new Set(source.nodes.map(node => node.node_id))
  const outgoing = new Map<string, string[]>()
  for (const nodeId of nodeIds) outgoing.set(nodeId, [])
  for (const edge of source.edges) {
    if (nodeIds.has(edge.from_node_id) && nodeIds.has(edge.to_node_id) && edge.from_node_id !== edge.to_node_id) {
      outgoing.get(edge.from_node_id)?.push(edge.to_node_id)
    }
  }
  const visited = new Set<string>()
  const pending = [fromNodeId]
  while (pending.length > 0) {
    const current = pending.shift()!
    if (visited.has(current) || !nodeIds.has(current)) continue
    visited.add(current)
    for (const next of outgoing.get(current) ?? []) pending.push(next)
  }
  return visited
}

export function wouldCreateCycle(
  source: WorkflowDefinitionSourceWire,
  fromNodeId: string,
  toNodeId: string,
): boolean {
  return fromNodeId === toNodeId || reachableNodes(source, toNodeId).has(fromNodeId)
}

export function nodeRemovalImpact(
  source: WorkflowDefinitionSourceWire,
  nodeId: string,
): NodeImpact {
  const edgeIds = source.edges
    .filter(edge => edge.from_node_id === nodeId || edge.to_node_id === nodeId)
    .map(edge => edgeId(edge.from_node_id, edge.to_node_id))
  const bindings = source.nodes.flatMap(node => node.input_bindings.flatMap(binding => {
    if (binding.source !== 'node_output' || binding.node_output.node_id !== nodeId) return []
    return [{ node_id: node.node_id, input_name: binding.input_name, output_slot: binding.node_output.output_slot }]
  }))
  return {
    nodeId,
    edgeIds,
    bindings,
    requiredOutputs: source.required_outputs.filter(output => output.node_id === nodeId),
  }
}

export function edgeRemovalImpact(
  source: WorkflowDefinitionSourceWire,
  fromNodeId: string,
  toNodeId: string,
): EdgeImpact {
  const bindings = source.nodes.flatMap(node => node.input_bindings.flatMap(binding => {
    if (
      binding.source !== 'node_output' ||
      binding.node_output.node_id !== fromNodeId ||
      node.node_id !== toNodeId
    ) return []
    return [{ node_id: node.node_id, input_name: binding.input_name, output_slot: binding.node_output.output_slot }]
  }))
  const withoutEdge = {
    ...source,
    edges: source.edges.filter(edge => !(edge.from_node_id === fromNodeId && edge.to_node_id === toNodeId)),
  }
  return {
    edgeId: edgeId(fromNodeId, toNodeId),
    bindings,
    hasAlternativePath: reachableNodes(withoutEdge, fromNodeId).has(toNodeId),
  }
}

/** Rename one producer slot and preserve every exact reference to that slot. */
export function renameOutputReferences(
  source: WorkflowDefinitionSourceWire,
  nodeId: string,
  oldSlot: string,
  newSlot: string,
): WorkflowDefinitionSourceWire {
  if (oldSlot === newSlot) return source
  const rename = (ref: NodeOutputRefWire): NodeOutputRefWire =>
    ref.node_id === nodeId && ref.output_slot === oldSlot ? { ...ref, output_slot: newSlot } : ref
  return {
    ...source,
    required_outputs: source.required_outputs.map(rename),
    nodes: source.nodes.map(node => ({
      ...node,
      input_bindings: node.input_bindings.map(binding =>
        binding.source === 'node_output'
          ? { ...binding, node_output: rename(binding.node_output) }
          : binding),
    })),
  }
}

function nodeById(source: WorkflowDefinitionSourceWire, nodeId: string): AgentNodeSourceWire | null {
  return source.nodes.find(node => node.node_id === nodeId) ?? null
}

function inputBinding(
  target: AgentNodeSourceWire,
  inputName: string,
): InputBindingWire | null {
  return target.input_bindings.find(binding => binding.input_name === inputName) ?? null
}

function updateControlEdge(
  edges: WorkflowDefinitionSourceWire['edges'],
  fromNodeId: string,
  toNodeId: string,
  keep: boolean,
): WorkflowDefinitionSourceWire['edges'] {
  const next: WorkflowDefinitionSourceWire['edges'] = []
  let found = false
  for (const edge of edges) {
    const matches = edge.from_node_id === fromNodeId && edge.to_node_id === toNodeId
    if (!matches) {
      next.push(edge)
    } else if (keep && !found) {
      next.push(edge)
      found = true
    }
  }
  if (keep && !found) next.push({ from_node_id: fromNodeId, to_node_id: toNodeId })
  return next
}

/**
 * Apply a complete connection edit. Validation and mutation deliberately live
 * together so the preview and the commit path cannot drift.
 */
export function applyConnectionEdit(
  source: WorkflowDefinitionSourceWire,
  edit: ConnectionEdit,
): ConnectionEditResult {
  const blockers: string[] = []
  const from = nodeById(source, edit.fromNodeId)
  const to = nodeById(source, edit.toNodeId)
  if (from === null || to === null) blockers.push('连接端点不存在；请从现有步骤重新选择。')
  if (edit.fromNodeId === edit.toNodeId) blockers.push('不能把步骤连接到自身。')
  const alreadyConnected = hasControlEdge(source, edit.fromNodeId, edit.toNodeId)
  if (edit.keepControlEdge && !alreadyConnected && wouldCreateCycle(source, edit.fromNodeId, edit.toNodeId)) {
    blockers.push('该方向会形成环；请选择上游步骤作为来源。')
  }
  const names = new Set<string>()
  for (const binding of edit.bindings) {
    const name = binding.inputName.trim()
    if (name === '') blockers.push('每个结果传递都需要输入名称。')
    if (names.has(name)) blockers.push(`输入名称 ${name} 重复。`)
    names.add(name)
    const contract = outputContract(source, { node_id: edit.fromNodeId, output_slot: binding.outputSlot })
    if (contract === null || !contract.required_for_node_completion) {
      blockers.push(`结果 ${edit.fromNodeId}.${binding.outputSlot} 不存在或不是完成所需结果。`)
    }
    const existing = to === null ? null : inputBinding(to, name)
    if (existing !== null && (existing.source !== 'node_output' || existing.node_output.node_id !== edit.fromNodeId)) {
      blockers.push(`输入名称 ${name} 已被该步骤的其他来源使用。`)
    }
  }
  if (!edit.keepControlEdge && edit.bindings.length > 0) {
    blockers.push('传递结果必须同时保留“完成后继续”的控制依赖。')
  }
  if (blockers.length > 0 || from === null || to === null) return { source: null, blockers }

  const bindingByName = new Map(edit.bindings.map(binding => [binding.inputName.trim(), binding]))
  const nextBindings = to.input_bindings
    .filter(binding => binding.source !== 'node_output' || binding.node_output.node_id !== edit.fromNodeId)
    .concat([...bindingByName.values()].map(binding => {
      const contract = outputContract(source, { node_id: edit.fromNodeId, output_slot: binding.outputSlot })!
      return {
        source: 'node_output' as const,
        input_name: binding.inputName.trim(),
        accepts: { kind: contract.kind, version: contract.version },
        node_output: { node_id: edit.fromNodeId, output_slot: binding.outputSlot },
      }
    }))
  // A damaged draft can contain the same control edge more than once. A
  // confirmed edit is also a repair opportunity, so retain one canonical edge.
  const nextEdges = updateControlEdge(source.edges, edit.fromNodeId, edit.toNodeId, edit.keepControlEdge)
  return {
    source: {
      ...source,
      edges: nextEdges,
      nodes: source.nodes.map(node => node.node_id === to.node_id ? { ...node, input_bindings: nextBindings } : node),
    },
    blockers: [],
  }
}
