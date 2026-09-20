import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import type { WorkflowDraftDiagnosticWire } from '../api/types'
import { buildPlanGraph } from './lib/planGraph'
import {
  bufferDiffersFromServer,
  EXECUTOR_OPTIONS,
  PlanNodeInspector,
  type NodeEditBuffer,
  type NodeServerValues,
} from './PlanNodeInspector'
import { DeleteImpactPreview, PlanReviewFooter } from './PlanReviewWorkspace'

function source() {
  // a → b → d, a → c → d diamond with d as final delivery (same shape as the
  // lib tests; built here through the mapping to stay consistent).
  const node = (nodeId: string) => ({
    node_id: nodeId,
    agent_definition_ref: { definition_id: 'builtin_general', version_id: 'v1', content_hash: 'a'.repeat(64) },
    task_contract: { objective: `执行 ${nodeId}`, scope: [], constraints: [], source_refs: [] },
    input_bindings: [],
    output_contracts: [{ kind: 'TextResult', version: 1, slot: 'result', required_for_node_completion: true }],
    access_mode: 'read' as const,
    conversation_scope: 'isolated' as const,
    tool_requirements: null,
    max_agent_generation_requests: null,
  })
  return {
    workflow_definition_id: 'task_x',
    name: '分叉汇聚',
    description: '',
    tags: [],
    origin: 'user' as const,
    input_contract: { kind: 'TaskContract' as const, version: 1 as const },
    required_outputs: [{ node_id: 'd', output_slot: 'result' }],
    edges: [
      { from_node_id: 'a', to_node_id: 'b' },
      { from_node_id: 'a', to_node_id: 'c' },
      { from_node_id: 'b', to_node_id: 'd' },
      { from_node_id: 'c', to_node_id: 'd' },
    ],
    default_budget: { max_agent_generation_requests: null, default_node_max_agent_generation_requests: null, admission_timeout_seconds: null, max_concurrency: 1 },
    nodes: [node('a'), node('b'), node('c'), node('d')],
  }
}

function info(nodeId: string) {
  const graph = buildPlanGraph(source(), null, [])
  const found = graph.nodes.find((node) => node.node_id === nodeId)
  if (found === undefined) throw new Error(`missing node ${nodeId}`)
  return found
}

const server: NodeServerValues = { task: '执行 b', agent: 'preset:general', parents: ['a'], completion: '完成即算' }
const cleanBuffer: NodeEditBuffer = { task: '执行 b', agent: 'preset:general', depends: ['a'], completion: '完成即算', baseVersion: 2 }

function html(children: React.ReactNode): string {
  return renderToStaticMarkup(children)
}

describe('PlanReviewFooter', () => {
  it('keeps the explicit start enabled only for a clean, ready, connected plan', () => {
    const markup = html(
      <PlanReviewFooter phase="ready" busy={false} refreshing={false} unsavedCount={0} connected blockers={[]} onStart={() => {}} />,
    )
    expect(markup).toContain('开始执行')
    expect(markup).not.toContain('disabled')
    expect(markup).not.toContain('将按当前显示的版本提交执行')
  })

  it('names the unsaved-edit and refresh blockers for the start action', () => {
    const markup = html(
      <PlanReviewFooter phase="ready" busy={false} refreshing={true} unsavedCount={2} connected blockers={[]} onStart={() => {}} />,
    )
    expect(markup).toContain('有 2 个节点的未保存修改；请先保存后再开始。')
    expect(markup).toContain('正在刷新计划状态…')
    expect(markup).toContain('disabled')
  })

  it('shows invalid-draft blockers and connection loss as text, never color alone', () => {
    const markup = html(
      <PlanReviewFooter phase="invalid" busy={false} refreshing={false} unsavedCount={0} connected={false} blockers={['invalid_draft']} onStart={() => {}} />,
    )
    expect(markup).toContain('当前草稿未通过完整校验')
    expect(markup).toContain('连接中断，暂不能开始或保存。')
  })
})

describe('DeleteImpactPreview', () => {
  it('explains reconnect, downstream and delivery impact from server semantics', () => {
    const planGraph = buildPlanGraph(source(), null, [])
    const singleParent = html(<DeleteImpactPreview info={info('c')} planGraph={planGraph} />)
    // c has exactly one outgoing edge, so d is re-connected to a.
    expect(singleParent).toContain('唯一下游 d 将改由 a 直接提供输入')
    expect(singleParent).toContain('受影响的下游节点：d')

    const multiOut = html(<DeleteImpactPreview info={info('a')} planGraph={planGraph} />)
    expect(multiOut).toContain('指向 b、c 的连线将被移除')
    expect(multiOut).toContain('受影响的下游节点：b、c、d')

    const delivery = html(<DeleteImpactPreview info={info('d')} planGraph={planGraph} />)
    expect(delivery).toContain('该节点的输出是最终交付')
  })
})

describe('PlanNodeInspector', () => {
  const errors: WorkflowDraftDiagnosticWire[] = [
    { severity: 'error', code: 'empty_task', message: '任务不能为空', node_id: 'b', edge_id: null },
  ]

  it('renders the controlled buffer and enables save only for dirty input', () => {
    const dirtyMarkup = html(
      <PlanNodeInspector
        info={info('b')}
        responsibility="implementation"
        modelLine="继承会话设置"
        errors={[]}
        otherNodes={['a', 'c', 'd']}
        value={{ ...cleanBuffer, task: '执行 b 并补充测试' }}
        server={server}
        serverVersion={2}
        locked={false}
        past={false}
        removable={true}
        removeArmed={false}
        conflict={false}
        expanded={true}
        onChange={() => {}}
        onExpandedChange={() => {}}
        onSave={() => {}}
        onDiscard={() => {}}
        onArmRemove={() => {}}
        onRemove={() => {}}
      />,
    )
    expect(dirtyMarkup).toContain('执行 b 并补充测试')
    expect(dirtyMarkup).not.toContain('disabled>保存节点') // save stays enabled
    expect(dirtyMarkup).not.toContain('服务端已保存新版本')

    const cleanMarkup = html(
      <PlanNodeInspector
        info={info('b')}
        responsibility="implementation"
        modelLine="继承会话设置"
        errors={[]}
        otherNodes={['a', 'c', 'd']}
        value={cleanBuffer}
        server={server}
        serverVersion={2}
        locked={false}
        past={false}
        removable={true}
        removeArmed={false}
        conflict={false}
        expanded={true}
        onChange={() => {}}
        onExpandedChange={() => {}}
        onSave={() => {}}
        onDiscard={() => {}}
        onArmRemove={() => {}}
        onRemove={() => {}}
      />,
    )
    expect(cleanMarkup).toContain('disabled')
  })

  it('keeps user input on conflict and shows the server version for comparison', () => {
    const markup = html(
      <PlanNodeInspector
        info={info('b')}
        responsibility="implementation"
        modelLine="继承会话设置"
        errors={[]}
        otherNodes={['a', 'c', 'd']}
        value={{ ...cleanBuffer, task: '我的修改' }}
        server={{ ...server, task: '执行 b' }}
        serverVersion={5}
        locked={false}
        past={false}
        removable={true}
        removeArmed={false}
        conflict={true}
        expanded={false}
        onChange={() => {}}
        onExpandedChange={() => {}}
        onSave={() => {}}
        onDiscard={() => {}}
        onArmRemove={() => {}}
        onRemove={() => {}}
      />,
    )
    expect(markup).toContain('服务端已保存新版本 v5')
    expect(markup).toContain('服务端任务：执行 b')
    expect(markup).toContain('按当前服务端版本保存我的修改')
    expect(markup).toContain('放弃我的修改')
  })

  it('marks executed nodes as locked and surfaces node diagnostics', () => {
    const markup = html(
      <PlanNodeInspector
        info={info('b')}
        responsibility="implementation"
        modelLine="继承会话设置"
        errors={errors}
        otherNodes={['a', 'c', 'd']}
        value={cleanBuffer}
        server={server}
        serverVersion={2}
        locked={true}
        past={true}
        removable={false}
        removeArmed={false}
        conflict={false}
        expanded={true}
        onChange={() => {}}
        onExpandedChange={() => {}}
        onSave={() => {}}
        onDiscard={() => {}}
        onArmRemove={() => {}}
        onRemove={() => {}}
      />,
    )
    expect(markup).toContain('已执行，不可改写')
    expect(markup).toContain('任务不能为空')
    expect(markup).toContain('disabled')
  })
})

describe('bufferDiffersFromServer', () => {
  it('detects task, agent, dependency and completion edits', () => {
    expect(bufferDiffersFromServer(cleanBuffer, server)).toBe(false)
    expect(bufferDiffersFromServer({ ...cleanBuffer, agent: 'preset:review' }, server)).toBe(true)
    expect(bufferDiffersFromServer({ ...cleanBuffer, depends: [] }, server)).toBe(true)
    expect(bufferDiffersFromServer({ ...cleanBuffer, completion: 'x' }, server)).toBe(true)
    expect(bufferDiffersFromServer({ ...cleanBuffer, task: 'x' }, server)).toBe(true)
  })
})

describe('EXECUTOR_OPTIONS', () => {
  it('keeps the preset catalog aligned with the planning service roles', () => {
    expect(EXECUTOR_OPTIONS.map((option) => option.value)).toEqual(['preset:general', 'preset:explore', 'preset:review'])
  })
})
