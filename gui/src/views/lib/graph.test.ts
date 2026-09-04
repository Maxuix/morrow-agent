import { describe, expect, it } from 'vitest'
import type { NodeViewWire, WorkflowStatus } from '../../api/types'
import { buildGraphLayout, isDirectRun, parseRevision } from './graph'

function revisionDump(nodes: unknown[], edges: unknown[] = []): Record<string, unknown> {
  return { name: 'test-workflow', nodes, edges }
}

function agentNode(nodeId: string, provider = 'anthropic', model = 'claude-sonnet-4') {
  return {
    node_id: nodeId,
    resolved_model_ref: { provider_id: provider, model_id: model },
    task_contract: { objective: `objective for ${nodeId}`, scope: [], constraints: [] },
    access_mode: 'read',
  }
}

function nodeView(nodeId: string, status: WorkflowStatus, attempt = 1): NodeViewWire {
  return {
    node: {
      node_run_id: `nrun_${nodeId}`,
      workflow_run_id: 'wfrun_x',
      node_id: nodeId,
      status,
      attempt,
      row_version: 1,
      started_at: null,
      completed_at: null,
      conversation_session_id: null,
      leaf_task_run_id: null,
      agent_run_id: null,
      effective_node_generation_request_cap: null,
    },
    output_bindings: [],
    artifacts: [],
    approval_pending: false,
    agent_generation_request_count: 0,
  }
}

describe('parseRevision', () => {
  it('extracts name, nodes (model/objective/access_mode) and edges from the flat dump', () => {
    const graph = parseRevision(
      revisionDump(
        [agentNode('b'), agentNode('a', 'openai', 'gpt-5')],
        [{ from_node_id: 'a', to_node_id: 'b' }],
      ),
    )
    expect(graph.name).toBe('test-workflow')
    // Nodes are sorted by node_id for deterministic rendering.
    expect(graph.nodes.map((node) => node.node_id)).toEqual(['a', 'b'])
    expect(graph.nodes[0]).toEqual({
      node_id: 'a',
      model: 'openai/gpt-5',
      objective: 'objective for a',
      access_mode: 'read',
    })
    expect(graph.edges).toEqual([{ from_node_id: 'a', to_node_id: 'b' }])
  })

  it('tolerates missing or malformed fields', () => {
    const graph = parseRevision({ nodes: [{ node_id: 7 }, 'junk', null], edges: [{}] })
    expect(graph.name).toBeNull()
    expect(graph.nodes).toEqual([])
    expect(graph.edges).toEqual([])
  })
})

describe('isDirectRun', () => {
  it('detects the single-node Direct shape', () => {
    expect(isDirectRun(parseRevision(revisionDump([agentNode('solo')])))).toBe(true)
  })

  it('rejects multi-node and empty revisions', () => {
    expect(isDirectRun(parseRevision(revisionDump([agentNode('a'), agentNode('b')])))).toBe(false)
    expect(isDirectRun(parseRevision({}))).toBe(false)
  })
})

describe('buildGraphLayout', () => {
  // Diamond: a → b, a → c, b → d, c → d.
  const diamond = parseRevision(
    revisionDump(
      [agentNode('a'), agentNode('b'), agentNode('c'), agentNode('d')],
      [
        { from_node_id: 'a', to_node_id: 'b' },
        { from_node_id: 'a', to_node_id: 'c' },
        { from_node_id: 'b', to_node_id: 'd' },
        { from_node_id: 'c', to_node_id: 'd' },
      ],
    ),
  )

  it('maps the 3-layer diamond to deterministic layered positions', () => {
    const layout = buildGraphLayout(diamond, [
      nodeView('a', 'completed'),
      nodeView('b', 'running'),
      nodeView('d', 'queued'),
    ])
    const byId = new Map(layout.nodes.map((node) => [node.id, node]))

    // Layers: a at depth 0, b/c at depth 1, d at depth 2.
    expect(byId.get('a')).toMatchObject({ x: 0, y: 0 })
    expect(byId.get('b')?.x).toBe(byId.get('c')?.x)
    expect(byId.get('b')?.x).toBeGreaterThan(byId.get('a')?.x ?? 0)
    expect(byId.get('d')?.x).toBeGreaterThan(byId.get('b')?.x ?? 0)
    // Siblings within a layer stack vertically, ordered by node_id.
    expect(byId.get('b')?.y).toBeLessThan(byId.get('c')?.y ?? 0)

    // Node state joins by node_id; a node with no NodeRun defaults to queued.
    expect(byId.get('a')?.data.status).toBe('completed')
    expect(byId.get('b')?.data.status).toBe('running')
    expect(byId.get('c')?.data.status).toBe('queued')
    expect(byId.get('c')?.data.attempt).toBe(0)

    expect(layout.edges).toHaveLength(4)
    expect(layout.edges).toContainEqual({ id: 'a->b', source: 'a', target: 'b' })
    expect(layout.edges).toContainEqual({ id: 'c->d', source: 'c', target: 'd' })
  })

  it('surfaces approval_pending and attempt from the node view', () => {
    const view = nodeView('a', 'blocked', 3)
    view.approval_pending = true
    const layout = buildGraphLayout(diamond, [view])
    const a = layout.nodes.find((node) => node.id === 'a')
    expect(a?.data.approval_pending).toBe(true)
    expect(a?.data.attempt).toBe(3)
  })
})
