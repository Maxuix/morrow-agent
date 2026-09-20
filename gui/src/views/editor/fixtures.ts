import type {
  AgentDefinitionViewWire,
  AgentDefinitionVersionWire,
  ArtifactContractCatalogWire,
  WorkflowDefinitionSourceWire,
  WorkflowDraftViewWire,
} from '../../api/types'

const NOW = '2026-09-12T00:00:00Z'
const HASH = 'a'.repeat(64)

export function fixtureAgent(
  definitionId: string,
  name: string,
  options: { accessMode?: 'read' | 'write'; version?: number; published?: boolean } = {},
): AgentDefinitionViewWire {
  const accessMode = options.accessMode ?? 'read'
  const versionNumber = options.version ?? 1
  const version: AgentDefinitionVersionWire = {
    version_id: `adev_${definitionId}_${versionNumber}`,
    workspace_id: 'ws_fixture',
    version: versionNumber,
    source: {
      definition_id: definitionId,
      name,
      description: `${name} fixture definition`,
      role_prompt: `Complete the ${name} fixture task.`,
      skill_version_ids: [],
      tool_requirements: [],
      access_mode_ceiling: accessMode,
      max_agent_generation_requests: null,
      model_selection: { provider_id: 'fixture', model_id: 'deterministic' },
      derived_from_version_id: null,
      derived_from_definition_id: null,
      derived_from_source_hash: null,
    },
    content_hash: HASH,
    origin: 'user',
    source_revision: versionNumber,
    created_at: NOW,
  }
  return {
    definition_id: definitionId,
    origin: 'user',
    source_revision: versionNumber,
    source_hash: HASH,
    revoked: false,
    desired_ahead_of_published: false,
    source: version.source,
    head: options.published === false ? null : {
      workspace_id: 'ws_fixture',
      definition_id: definitionId,
      version_id: version.version_id,
      source_revision: versionNumber,
      source_hash: HASH,
      enabled: true,
      row_version: versionNumber,
    },
    published_version: options.published === false ? null : version,
  }
}

function node(
  nodeId: string,
  agent: AgentDefinitionViewWire,
  objective: string,
  options: {
    outputs?: Array<{ slot: string; kind?: string; version?: number; required?: boolean }>
    inputFrom?: Array<{ inputName: string; nodeId: string; slot?: string; kind?: string; version?: number }>
    accessMode?: 'read' | 'write'
  } = {},
) {
  const outputs = options.outputs ?? [{ slot: 'result' }]
  return {
    node_id: nodeId,
    agent_definition_ref: {
      definition_id: agent.definition_id,
      version_id: agent.published_version?.version_id ?? `missing_${agent.definition_id}`,
      content_hash: agent.published_version?.content_hash ?? HASH,
    },
    task_contract: { objective, scope: [], constraints: [], source_refs: [] },
    input_bindings: (options.inputFrom ?? []).map(input => ({
      source: 'node_output' as const,
      input_name: input.inputName,
      accepts: { kind: input.kind ?? 'TextResult', version: input.version ?? 1 },
      node_output: { node_id: input.nodeId, output_slot: input.slot ?? 'result' },
    })),
    output_contracts: outputs.map(output => ({
      kind: output.kind ?? 'TextResult',
      version: output.version ?? 1,
      slot: output.slot,
      required_for_node_completion: output.required ?? true,
    })),
    access_mode: options.accessMode ?? agent.source?.access_mode_ceiling ?? 'read',
    conversation_scope: 'isolated' as const,
    tool_requirements: null,
    max_agent_generation_requests: null,
  }
}

export function fixtureSource(
  id: string,
  name: string,
  nodes: WorkflowDefinitionSourceWire['nodes'],
  edges: WorkflowDefinitionSourceWire['edges'],
  requiredOutputs: WorkflowDefinitionSourceWire['required_outputs'],
): WorkflowDefinitionSourceWire {
  return {
    workflow_definition_id: id,
    name,
    description: `${name} deterministic fixture`,
    tags: ['fixture'],
    origin: 'user',
    input_contract: { kind: 'TaskContract', version: 1 },
    required_outputs: requiredOutputs,
    edges,
    default_budget: {
      max_agent_generation_requests: null,
      default_node_max_agent_generation_requests: null,
      admission_timeout_seconds: null,
      max_concurrency: 1,
    },
    nodes,
  }
}

export function fixtureDraft(
  source: WorkflowDefinitionSourceWire,
  options: Partial<WorkflowDraftViewWire['draft']> = {},
): WorkflowDraftViewWire {
  const draft = {
    draft_id: `wdraft_${source.workflow_definition_id}`,
    workspace_id: 'ws_fixture',
    source,
    source_hash: HASH,
    base_workflow_revision_id: null,
    base_head_row_version: 0,
    base_source_revision: 0,
    base_definition_source_hash: null,
    status: 'valid' as const,
    diagnostics: [],
    frozen_workflow_revision_id: null,
    row_version: 1,
    created_at: NOW,
    updated_at: NOW,
    planner: null,
    ...options,
  }
  return { draft, stale_reasons: [] }
}

const researcher = fixtureAgent('researcher', '研究助手')
const writer = fixtureAgent('writer', '实现助手', { accessMode: 'write' })
const reviewer = fixtureAgent('reviewer', '检查助手')

export const THREE_STEP_SOURCE = fixtureSource(
  'three_step',
  '三步交付流程',
  [
    node('research', researcher, '收集与整理任务所需的背景信息'),
    node('implement', writer, '根据背景信息完成实现工作', { inputFrom: [{ inputName: 'research_result', nodeId: 'research' }], accessMode: 'write' }),
    node('review', reviewer, '检查实现结果并给出交付结论', { inputFrom: [{ inputName: 'implementation', nodeId: 'implement' }] }),
  ],
  [
    { from_node_id: 'research', to_node_id: 'implement' },
    { from_node_id: 'implement', to_node_id: 'review' },
  ],
  [{ node_id: 'review', output_slot: 'result' }],
)

export const BRANCH_MERGE_SOURCE = fixtureSource(
  'branch_merge',
  '分支汇合流程',
  [
    node('start', researcher, '准备共同输入'),
    node('left', reviewer, '从一个角度检查输入', { inputFrom: [{ inputName: 'start_result', nodeId: 'start' }] }),
    node('right', reviewer, '从另一个角度检查输入', { inputFrom: [{ inputName: 'start_result', nodeId: 'start' }] }),
    node('merge', writer, '汇合两条分支并整理交付结果', {
      inputFrom: [
        { inputName: 'left_result', nodeId: 'left' },
        { inputName: 'right_result', nodeId: 'right' },
      ],
      accessMode: 'write',
    }),
  ],
  [
    { from_node_id: 'start', to_node_id: 'left' },
    { from_node_id: 'start', to_node_id: 'right' },
    { from_node_id: 'left', to_node_id: 'merge' },
    { from_node_id: 'right', to_node_id: 'merge' },
  ],
  [{ node_id: 'merge', output_slot: 'result' }],
)

export const MULTI_OUTPUT_SOURCE = fixtureSource(
  'multi_output',
  '多结果流程',
  [node('produce', researcher, '产生可分别交付的结果', {
    outputs: [
      { slot: 'summary' },
      { slot: 'evidence' },
      { slot: 'debug', required: false },
    ],
  })],
  [],
  [
    { node_id: 'produce', output_slot: 'summary' },
    { node_id: 'produce', output_slot: 'evidence' },
  ],
)

export const BROKEN_REFERENCE_SOURCE = fixtureSource(
  'broken_reference',
  '待修复引用',
  [node('surviving', researcher, '保留的步骤')],
  [{ from_node_id: 'missing', to_node_id: 'surviving' }],
  [{ node_id: 'missing', output_slot: 'result' }],
)

export const WORKFLOW_EDITOR_FIXTURE = {
  workspaceId: 'ws_fixture',
  agents: [researcher, writer, reviewer],
  contracts: [
    { kind: 'TextResult', version: 1, schema: { type: 'string' } },
    { kind: 'ImplementationPatch', version: 1, schema: { type: 'object' } },
  ] satisfies ArtifactContractCatalogWire[],
  sources: {
    threeStep: THREE_STEP_SOURCE,
    branchMerge: BRANCH_MERGE_SOURCE,
    multiOutput: MULTI_OUTPUT_SOURCE,
    brokenReference: BROKEN_REFERENCE_SOURCE,
  },
  drafts: {
    normal: fixtureDraft(THREE_STEP_SOURCE),
    frozen: fixtureDraft(THREE_STEP_SOURCE, { status: 'frozen', frozen_workflow_revision_id: 'wrev_frozen' }),
    rejected: fixtureDraft(THREE_STEP_SOURCE, { status: 'rejected' }),
    missingAgent: fixtureDraft(BROKEN_REFERENCE_SOURCE, {
      diagnostics: [{ severity: 'error', code: 'agent_version_unresolved', message: 'exact Agent version is missing', node_id: 'surviving', edge_id: null }],
      status: 'invalid',
    }),
    conflict: fixtureDraft(THREE_STEP_SOURCE, { row_version: 4, updated_at: '2026-09-12T00:04:00Z' }),
  },
} as const

export type WorkflowEditorFixture = typeof WORKFLOW_EDITOR_FIXTURE

