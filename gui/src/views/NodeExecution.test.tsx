import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import type { ApiClient } from '../api/client'
import type { NodeExecutionWire, NodeViewWire } from '../api/types'
import { buildGraphLayout } from './lib/graph'
import { DirectNodeCard } from './DirectNodeCard'
import { NodeDetail } from './NodeDetail'
import { RunStatusCard } from './TaskPlanPanel'
import type { RevisionNodeInfo } from './lib/graph'

const revisionNode: RevisionNodeInfo = {
  node_id: 'alpha',
  model: 'fake-provider/m1',
  objective: 'Survey the area',
  access_mode: 'read',
}

function nodeView(execution?: NodeExecutionWire | null): NodeViewWire {
  return {
    node: {
      node_run_id: 'nrun_aaa111',
      workflow_run_id: 'wrun_aaa111',
      node_id: 'alpha',
      status: 'running',
      attempt: 1,
      row_version: 2,
      started_at: '2026-09-10T10:00:00Z',
      completed_at: null,
      conversation_session_id: null,
      leaf_task_run_id: null,
      agent_run_id: null,
      effective_node_generation_request_cap: 3,
    },
    output_bindings: [],
    artifacts: [],
    approval_pending: false,
    agent_generation_request_count: 1,
    ...(execution === undefined ? {} : { execution }),
  }
}

const paused: NodeExecutionWire = {
  state: 'paused',
  segment_id: 'seg_aaa111',
  control_generation: 1,
  reason: 'user_interrupt',
  settled_at: '2026-09-10T10:01:00Z',
}

const client = {} as unknown as ApiClient

describe('per-node execution projection display (BUG-GUI-002, P02)', () => {
  it('DirectNodeCard shows business + paused execution as a dual label', () => {
    const html = renderToStaticMarkup(
      <DirectNodeCard nodeView={nodeView(paused)} revisionNode={revisionNode} selected={false} onSelect={() => {}} />,
    )
    expect(html).toContain('运行中')
    expect(html).toContain('已暂停')
  })

  it('DirectNodeCard shows pausing while the drain is open', () => {
    const html = renderToStaticMarkup(
      <DirectNodeCard
        nodeView={nodeView({ state: 'pausing', segment_id: null, control_generation: 1, reason: 'user_interrupt', settled_at: null })}
        revisionNode={revisionNode}
        selected={false}
        onSelect={() => {}}
      />,
    )
    expect(html).toContain('运行中')
    expect(html).toContain('正在暂停')
  })

  it('DirectNodeCard never fabricates a pause on pre-P02 servers (no execution field)', () => {
    const html = renderToStaticMarkup(
      <DirectNodeCard nodeView={nodeView(undefined)} revisionNode={revisionNode} selected={false} onSelect={() => {}} />,
    )
    expect(html).toContain('运行中')
    expect(html).not.toContain('已暂停')
    expect(html).not.toContain('正在暂停')
    expect(html).not.toContain('待恢复')
  })

  it('NodeDetail renders the read-only execution facts next to the business status', () => {
    const html = renderToStaticMarkup(
      <NodeDetail nodeView={nodeView(paused)} revisionNode={revisionNode} client={client} />,
    )
    expect(html).toContain('运行中')
    expect(html).toContain('已暂停')
    expect(html).toContain('执行状态')
    expect(html).toContain('paused')
    expect(html).toContain('控制代次')
    expect(html).toContain('暂停原因')
    expect(html).toContain('user_interrupt')
    expect(html).toContain('落定时间')
    // segment id renders in short form (already short here, shown verbatim)
    expect(html).toContain('seg_aaa111')
  })

  it('NodeDetail omits execution rows when the server projects nothing', () => {
    const html = renderToStaticMarkup(
      <NodeDetail nodeView={nodeView(undefined)} revisionNode={revisionNode} client={client} />,
    )
    expect(html).toContain('运行中')
    expect(html).not.toContain('执行状态')
    expect(html).not.toContain('已暂停')
  })

  it('RunStatusCard node rows pair the business label with the execution badge', () => {
    const view = {
      binding: null,
      draft: null,
      version: null,
      operations: [],
      execution: {
        allowed: false,
        digest: null,
        draft_id: null,
        draft_version: null,
        blockers: [],
        frozen_selections: [],
      },
      run: {
        workflow_run_id: 'wrun_aaa111',
        status: 'paused',
        result_status: null,
        pause_requested: true,
        row_version: 3,
        workflow_revision_id: 'wrev_aaa',
        lineage_root_run_id: null,
        origin: 'initial',
        inherited_node_ids: [],
        active_node_ids: ['alpha'],
        nodes: [
          { node_id: 'alpha', status: 'running', execution: paused },
          { node_id: 'beta', status: 'queued', execution: null },
        ],
        agent_generation_request_count: 1,
        lineage_agent_generation_request_count: 1,
        outcome: null,
      },
      candidate: null,
      allowed_actions: [],
      control: null,
      ownership: { role: 'root', issues: [], workflow_run_id: null },
      plan: { source: null, node_metadata: null, diagnostics: [] },
    }
    const html = renderToStaticMarkup(
      <RunStatusCard view={view as unknown as import('../api/types').TaskPlanViewWire} phase="paused" />,
    )
    expect(html).toContain('alpha · 运行中 · 已暂停')
    expect(html).toContain('beta · 排队中')
  })

  it('graph layout carries the execution projection through to the node model', () => {
    const layout = buildGraphLayout(
      { name: null, nodes: [revisionNode], edges: [] },
      [nodeView(paused)],
    )
    expect(layout.nodes[0].data.status).toBe('running')
    expect(layout.nodes[0].data.execution).toEqual(paused)
    const legacy = buildGraphLayout(
      { name: null, nodes: [revisionNode], edges: [] },
      [nodeView(undefined)],
    )
    expect(legacy.nodes[0].data.execution).toBeNull()
  })
})
