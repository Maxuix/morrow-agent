import type {
  AgentDefinitionSourceWire,
  AgentDefinitionViewWire,
  AgentDefinitionVersionWire,
  WorkflowStatus,
  WorkflowDefinitionSourceWire,
} from '../../api/types'

export interface DiffLine {
  path: string
  before: string
  after: string
}

export function nodeIsLocked(status: WorkflowStatus | undefined): boolean {
  return status !== undefined && status !== 'queued'
}

export function draftStalenessBlocksFreeze(staleReasons: string[]): boolean {
  return staleReasons.some(
    (reason) => reason === 'workflow_head_changed' || reason === 'workflow_source_changed',
  )
}

export function agentCopyProvenance(
  definition: AgentDefinitionViewWire,
): Pick<
  AgentDefinitionSourceWire,
  'derived_from_version_id' | 'derived_from_definition_id' | 'derived_from_source_hash'
> {
  const sourceHash = definition.source_hash
  return {
    derived_from_version_id:
      sourceHash !== null && definition.published_version?.content_hash === sourceHash
        ? definition.published_version.version_id
        : null,
    derived_from_definition_id: sourceHash === null ? null : definition.definition_id,
    derived_from_source_hash: sourceHash,
  }
}

export function preserveCanvasPositions<T extends { id: string; position: { x: number; y: number } }>(
  projected: T[],
  current: T[],
): T[] {
  const positions = new Map(current.map((node) => [node.id, node.position]))
  return projected.map((node) => ({
    ...node,
    position: positions.get(node.id) ?? node.position,
  }))
}

export function commandId(scope: string): string {
  return `cmd_${scope}_${crypto.randomUUID().replaceAll('-', '_')}`
}

export function draftId(): string {
  return `wdraft_${crypto.randomUUID().replaceAll('-', '_')}`
}

export function newSingleNodeWorkflow(
  definitionId: string,
  name: string,
  agent: AgentDefinitionVersionWire,
): WorkflowDefinitionSourceWire {
  return {
    workflow_definition_id: definitionId,
    name,
    description: '',
    tags: [],
    origin: 'user',
    input_contract: { kind: 'TaskContract', version: 1 },
    required_outputs: [{ node_id: 'agent', output_slot: 'result' }],
    edges: [],
    default_budget: {
      max_agent_generation_requests: null,
      default_node_max_agent_generation_requests: null,
      admission_timeout_seconds: null,
      max_concurrency: 1,
    },
    nodes: [
      {
        node_id: 'agent',
        agent_definition_ref: {
          definition_id: agent.source.definition_id,
          version_id: agent.version_id,
          content_hash: agent.content_hash,
        },
        task_contract: {
          objective: 'Describe the work this node should complete.',
          scope: [],
          constraints: [],
          source_refs: [],
        },
        input_bindings: [
          {
            source: 'workflow_input',
            input_name: 'task',
            accepts: { kind: 'TaskContract', version: 1 },
            workflow_input: 'task',
          },
        ],
        output_contracts: [
          {
            kind: 'TextResult',
            version: 1,
            slot: 'result',
            required_for_node_completion: true,
          },
        ],
        access_mode: agent.source.access_mode_ceiling,
        conversation_scope: 'invoking_session',
        tool_requirements: null,
        max_agent_generation_requests: null,
      },
    ],
  }
}

export function cloneWorkflowSource(
  source: WorkflowDefinitionSourceWire,
  definitionId: string,
  name: string,
): WorkflowDefinitionSourceWire {
  return structuredClone({
    ...source,
    workflow_definition_id: definitionId,
    name,
    origin: 'user' as const,
  })
}

export function sourceFromRevision(revision: Record<string, unknown>): WorkflowDefinitionSourceWire | null {
  if (!Array.isArray(revision.nodes) || !Array.isArray(revision.edges)) return null
  const nodes = revision.nodes.map((value) => {
    const node = { ...(value as Record<string, unknown>) }
    delete node.resolved_model_ref
    delete node.resolved_tool_requirements
    delete node.declared_node_max_agent_generation_requests
    return node
  })
  const source = {
    workflow_definition_id: revision.workflow_definition_id,
    name: revision.name,
    description: revision.description,
    tags: revision.tags,
    origin: revision.origin,
    input_contract: revision.input_contract,
    required_outputs: revision.required_outputs,
    edges: revision.edges,
    default_budget: revision.budget,
    nodes,
  }
  return source as unknown as WorkflowDefinitionSourceWire
}

export function structuralDiff(before: unknown, after: unknown, path = '$'): DiffLine[] {
  if (Object.is(before, after)) return []
  if (Array.isArray(before) && Array.isArray(after)) {
    const result: DiffLine[] = []
    for (let index = 0; index < Math.max(before.length, after.length); index += 1) {
      result.push(...structuralDiff(before[index], after[index], `${path}[${index}]`))
    }
    return result
  }
  if (isRecord(before) && isRecord(after)) {
    const result: DiffLine[] = []
    const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])].sort()
    for (const key of keys) result.push(...structuralDiff(before[key], after[key], `${path}.${key}`))
    return result
  }
  return [{ path, before: display(before), after: display(after) }]
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function display(value: unknown): string {
  if (value === undefined) return '∅'
  const text = JSON.stringify(value)
  return text === undefined ? String(value) : text
}
