import { ApiError } from '../../api/client'
import type {
  AgentDefinitionSourceWire,
  AgentDefinitionViewWire,
  AgentDefinitionVersionWire,
  FutureGraphPatchWire,
  PatchApplyResultWire,
  PatchDiffWire,
  PatchValidationWire,
  WorkflowStatus,
  WorkflowDefinitionSourceWire,
  WorkflowRunWire,
} from '../../api/types'
import { shortId } from './labels'

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

export function patchId(): string {
  return `wpatch_${crypto.randomUUID().replaceAll('-', '_')}`
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

// Pending-graph (patch) flow helpers -------------------------------------------
// Pure and deterministic so the PatchEditor UI logic stays testable in a node
// environment; the component only wires these to the ApiClient.

/**
 * Assemble the wire patch for a paused parent run from its editable source.
 * `requested_by` defaults to the GUI user; `request_kind` is omitted unless
 * explicitly given (the UI only produces `user_exact`-style edits today).
 */
export function buildPatchDraft(parts: {
  workflow_patch_id: string
  workspace_id: string
  run: WorkflowRunWire
  source: WorkflowDefinitionSourceWire
  requested_by?: string
  request_kind?: FutureGraphPatchWire['request_kind']
}): FutureGraphPatchWire {
  const patch: FutureGraphPatchWire = {
    workflow_patch_id: parts.workflow_patch_id,
    workspace_id: parts.workspace_id,
    parent_run_id: parts.run.workflow_run_id,
    base_workflow_revision_id: parts.run.workflow_revision_id,
    expected_parent_row_version: parts.run.row_version,
    source: parts.source,
    requested_by: parts.requested_by ?? 'gui_user',
  }
  if (parts.request_kind !== undefined) patch.request_kind = parts.request_kind
  return patch
}

/**
 * Apply enablement: a valid preview is required; an elevated-risk patch
 * additionally needs the explicit user acknowledgement.
 */
export function canApplyPatch(
  preview: PatchValidationWire | null,
  riskAcknowledged: boolean,
): boolean {
  if (preview === null || !preview.valid) return false
  return preview.risk?.level === 'elevated' ? riskAcknowledged : true
}

export interface DiffSummaryLine {
  /** Chinese label; boolean-only lines carry no value. */
  label: string
  /** Comma-joined ids rendered in mono, or null for boolean lines. */
  value: string | null
}

/** Human-readable diff summary lines, in a stable order. */
export function diffSummaryLines(diff: PatchDiffWire | null): DiffSummaryLine[] {
  if (diff === null) return []
  const lines: DiffSummaryLine[] = []
  const add = (label: string, ids: string[]) => {
    if (ids.length > 0) lines.push({ label, value: ids.join(', ') })
  }
  add('新增节点', diff.added_node_ids)
  add('删除节点', diff.removed_node_ids)
  add('修改节点', diff.changed_node_ids)
  add('新增边', diff.added_edges)
  add('删除边', diff.removed_edges)
  if (diff.required_outputs_changed) lines.push({ label: '结果输出已变更', value: null })
  if (diff.budget_changed) lines.push({ label: '预算/并发/超时已变更', value: null })
  return lines
}

/** Outcome copy for a successful patch apply. */
export function patchResultMessage(result: PatchApplyResultWire): string {
  const childLine =
    result.child_run === null
      ? '执行集为空，父运行已直接收尾'
      : `子运行 ${shortId(result.child_run.workflow_run_id)} 已创建并从 running 继续`
  return `${childLine}；父运行已终止为 superseded`
}

/**
 * Inline error copy: ApiError messages pass through as-is (compile errors 400
 * carry their diagnostics separately); an OCC conflict (409) means the parent
 * row version moved and the flow must be reopened.
 */
export function patchApplyErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) {
      return `${error.message}；父运行 row version 已变化，请关闭并重新打开此流程。`
    }
    return error.message
  }
  if (error instanceof Error) return error.message
  return String(error)
}
