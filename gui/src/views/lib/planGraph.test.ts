import { describe, expect, it } from 'vitest'
import type {
  DraftVersionWire,
  NodePlanningMetadataWire,
  WorkflowDefinitionSourceWire,
  WorkflowDraftDiagnosticWire,
} from '../../api/types'
import {
  buildPlanGraph,
  buildPlanLayout,
  downstreamOf,
  nodeCompletionCriteria,
  planNodeFromTask,
  validateDependencyEdge,
} from './planGraph'

/** A→B, A→C, B→D, C→D diamond with D as the final delivery. */
function diamondSource(): WorkflowDefinitionSourceWire {
  const node = (nodeId: string, access: 'read' | 'write' = 'read') => ({
    node_id: nodeId,
    agent_definition_ref: { definition_id: 'builtin_general', version_id: 'v1', content_hash: 'a'.repeat(64) },
    task_contract: { objective: `objective for ${nodeId}`, scope: [], constraints: [], source_refs: [] },
    input_bindings: [],
    output_contracts: [{ kind: 'TextResult', version: 1, slot: 'result', required_for_node_completion: true }],
    access_mode: access,
    conversation_scope: 'isolated' as const,
    tool_requirements: null,
    max_agent_generation_requests: null,
  })
  return {
    workflow_definition_id: 'task_x',
    name: '分叉汇聚计划',
    description: '',
    tags: [],
    origin: 'user',
    input_contract: { kind: 'TaskContract', version: 1 },
    required_outputs: [{ node_id: 'd', output_slot: 'result' }],
    edges: [
      { from_node_id: 'a', to_node_id: 'b' },
      { from_node_id: 'a', to_node_id: 'c' },
      { from_node_id: 'b', to_node_id: 'd' },
      { from_node_id: 'c', to_node_id: 'd' },
    ],
    default_budget: { max_agent_generation_requests: null, default_node_max_agent_generation_requests: null, admission_timeout_seconds: null, max_concurrency: 1 },
    nodes: [node('a', 'write'), node('b'), node('c'), node('d')],
  }
}

function metadata(nodeId: string, agent = 'preset:general'): NodePlanningMetadataWire {
  return { title: `${nodeId} 标题`, responsibility: 'implementation', agent_selection: agent, model_choice: null, generation_choice: null, x: null, y: null }
}

function version(): DraftVersionWire {
  return {
    draft_id: 'wd_1',
    version: 3,
    node_metadata: { a: metadata('a'), b: metadata('b', 'preset:explore'), c: metadata('c'), d: metadata('d', 'preset:review') },
    execution_selections: { b: { model: { provider_id: 'openai', model_id: 'gpt-5' }, generation: null, model_source: 'session', generation_source: 'session', agent_ref: { definition_id: 'builtin_general', version_id: 'v1', content_hash: 'a'.repeat(64) } } },
    created_from: 'llm_generate',
    command_id: 'cmd',
    summary: '',
    validation_digest: 'b'.repeat(64),
    created_at: '',
  }
}

describe('buildPlanGraph', () => {
  it('maps draft topology with titles, agents, resolved models and delivery flags', () => {
    const graph = buildPlanGraph(diamondSource(), version(), [])
    expect(graph.name).toBe('分叉汇聚计划')
    expect(graph.nodes.map((node) => node.node_id)).toEqual(['a', 'b', 'c', 'd'])
    const b = graph.nodes.find((node) => node.node_id === 'b')!
    expect(b.title).toBe('b 标题')
    expect(b.agent).toBe('preset:explore')
    expect(b.model).toBe('openai/gpt-5')
    expect(b.parents).toEqual(['a'])
    const d = graph.nodes.find((node) => node.node_id === 'd')!
    expect(d.finalDelivery).toBe(true)
    expect(graph.nodes.find((node) => node.node_id === 'a')!.finalDelivery).toBe(false)
    expect(graph.edges).toHaveLength(4)
    expect(graph.danglingEdgeCount).toBe(0)
  })

  it('attaches error diagnostics per node and keeps dangling edges out of the canvas', () => {
    const diagnostics: WorkflowDraftDiagnosticWire[] = [
      { severity: 'error', code: 'invalid_dependency', message: '依赖缺失', node_id: 'b', edge_id: null },
      { severity: 'error', code: 'invalid_dependency', message: '依赖缺失', node_id: 'b', edge_id: null },
      { severity: 'warning', code: 'something', message: '提示', node_id: 'b', edge_id: null },
      { severity: 'error', code: 'dangling_edge', message: '端点缺失', node_id: null, edge_id: 'a->ghost' },
    ]
    const source = diamondSource()
    source.edges = [...source.edges, { from_node_id: 'a', to_node_id: 'ghost' }]
    const graph = buildPlanGraph(source, version(), diagnostics)
    expect(graph.nodes.find((node) => node.node_id === 'b')!.errorCount).toBe(2)
    expect(graph.edges).toHaveLength(4)
    expect(graph.danglingEdgeCount).toBe(1)
  })

  it('tolerates a missing version record (legacy projections)', () => {
    const graph = buildPlanGraph(diamondSource(), null, [])
    const a = graph.nodes.find((node) => node.node_id === 'a')!
    expect(a.agent).toBe('preset:general')
    expect(a.model).toBeNull()
    expect(a.title).toBe('objective for a'.slice(0, 40))
  })
})

describe('buildPlanLayout', () => {
  it('positions every node as planned on the layered diamond', () => {
    const layout = buildPlanLayout(buildPlanGraph(diamondSource(), version(), []))
    const byId = new Map(layout.nodes.map((node) => [node.id, node]))
    expect(byId.get('a')?.data.status).toBe('planned')
    expect(byId.get('a')?.data.title).toBe('a 标题')
    expect(byId.get('a')?.data.agentLabel).toBe('General · 通用执行')
    expect(byId.get('d')?.data.finalDelivery).toBe(true)
    expect(byId.get('a')?.x).toBeLessThan(byId.get('b')?.x ?? 0)
    expect(byId.get('b')?.y).toBeLessThan(byId.get('c')?.y ?? 0)
    expect(layout.edges).toContainEqual({ id: 'c->d', source: 'c', target: 'd' })
  })
})

describe('nodeCompletionCriteria', () => {
  it('parses the completion constraint into lines', () => {
    const source = diamondSource()
    source.nodes[0]!.task_contract.constraints = ['Completion criteria: 测试通过; 报告已提交']
    expect(nodeCompletionCriteria(source, 'a')).toEqual(['测试通过', '报告已提交'])
    expect(nodeCompletionCriteria(source, 'missing')).toEqual([])
  })
})

describe('validateDependencyEdge', () => {
  const ids = new Set(['a', 'b', 'c', 'd'])
  const edges = [
    { from_node_id: 'a', to_node_id: 'b' },
    { from_node_id: 'b', to_node_id: 'd' },
  ]

  it('accepts a fresh acyclic edge', () => {
    expect(validateDependencyEdge(edges, ids, 'a', 'c')).toBe('ok')
    expect(validateDependencyEdge(edges, ids, 'c', 'd')).toBe('ok')
  })

  it('blocks self loops, duplicates and cycles before they reach the server', () => {
    expect(validateDependencyEdge(edges, ids, 'a', 'a')).toBe('self')
    expect(validateDependencyEdge(edges, ids, 'a', 'b')).toBe('duplicate')
    expect(validateDependencyEdge(edges, ids, 'd', 'a')).toBe('cycle')
    expect(validateDependencyEdge(edges, ids, 'ghost', 'a')).toBe('missing_source')
    expect(validateDependencyEdge(edges, ids, 'a', 'ghost')).toBe('missing_target')
  })
})

describe('downstreamOf', () => {
  it('collects direct and transitive dependents', () => {
    const edges = buildPlanGraph(diamondSource(), version(), []).edges
    expect(downstreamOf(edges, 'a')).toEqual(['b', 'c', 'd'])
    expect(downstreamOf(edges, 'd')).toEqual([])
  })
})

describe('planNodeFromTask', () => {
  it('builds a bounded new-node identity with the chosen dependencies', () => {
    const node = planNodeFromTask('执行回归测试\n补充说明', 'preset:review', ['a', 'a'], ['a', 'b'])
    expect(node.node_id).toBe('node_3')
    expect(node.agent).toBe('preset:review')
    expect(node.responsibility).toBe('review')
    expect(node.depends_on).toEqual(['a'])
    expect(node.completion).toHaveLength(1)
  })

  it('skips taken node ids', () => {
    const node = planNodeFromTask('任务', 'preset:general', [], ['node_1'])
    expect(node.node_id).toBe('node_2')
  })
})
