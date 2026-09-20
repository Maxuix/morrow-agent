// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { WorkflowDefinitionSourceWire } from '../api/types'
import { WorkflowEditor } from './WorkflowEditor'
import { fixtureAgent, fixtureSource, THREE_STEP_SOURCE, WORKFLOW_EDITOR_FIXTURE } from './editor/fixtures'

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver

afterEach(cleanup)

function renderEditor(source: WorkflowDefinitionSourceWire = THREE_STEP_SOURCE, onChange = vi.fn()) {
  render(
    <WorkflowEditor
      source={source}
      diagnostics={[]}
      agents={[...WORKFLOW_EDITOR_FIXTURE.agents]}
      contracts={[...WORKFLOW_EDITOR_FIXTURE.contracts]}
      disabled={false}
      onChange={onChange}
      scope={{ workspaceId: 'ws_dom', draftId: source.workflow_definition_id }}
    />,
  )
  return onChange
}

describe('WorkflowEditor semantic DOM', () => {
  it('renders the canvas-first layout and preserves hidden Source fields while editing', () => {
    const onChange = renderEditor()
    expect(screen.getByRole('region', { name: '流程画布' })).toBeTruthy()
    expect(screen.getByTestId('rf__node-research').querySelector('.workflow-node-card')).toBeTruthy()
    expect(screen.getByLabelText('工作流名称')).toBeTruthy()
    expect(screen.getByLabelText('任务目标')).toBeTruthy()

    const original = THREE_STEP_SOURCE.nodes[0]!
    fireEvent.change(screen.getByLabelText('任务目标'), { target: { value: '新的首行目标\n完整上下文仍由用户编辑' } })
    const next = onChange.mock.lastCall?.[0] as WorkflowDefinitionSourceWire
    expect(next.nodes[0]?.task_contract.objective).toBe('新的首行目标\n完整上下文仍由用户编辑')
    expect(next.nodes[0]?.task_contract.scope).toEqual(original.task_contract.scope)
    expect(next.nodes[0]?.task_contract.source_refs).toEqual(original.task_contract.source_refs)
    expect(next.nodes[0]?.input_bindings).toEqual(original.input_bindings)
    expect(next.nodes[0]?.output_contracts).toEqual(original.output_contracts)
  })

  it('keeps invalid numeric input local and exposes the advanced settings semantics', () => {
    const onChange = renderEditor()
    expect(screen.getAllByText('使用默认设置').length).toBeGreaterThan(0)
    fireEvent.change(screen.getByLabelText('整个流程的模型调用次数上限'), { target: { value: '0' } })
    expect(screen.getByRole('alert').textContent).toContain('大于 0')
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.queryByText('启动后允许继续发起执行的窗口，不是单条请求超时或强制取消。')).toBeNull()
    fireEvent.focus(screen.getByLabelText('准入时限（秒）'))
    expect(screen.getByText('启动后允许继续发起执行的窗口，不是单条请求超时或强制取消。')).toBeTruthy()
  })

  it('requires an explicit context conversion before adding a second step', () => {
    const first = structuredClone(THREE_STEP_SOURCE.nodes[0]!)
    first.conversation_scope = 'invoking_session'
    const source = fixtureSource('single_invoking', '单节点', [first], [], [{ node_id: first.node_id, output_slot: 'result' }])
    const onChange = renderEditor(source)
    fireEvent.change(screen.getByLabelText('添加步骤助手'), { target: { value: fixtureAgent('writer', '实现助手', { accessMode: 'write' }).definition_id } })
    fireEvent.click(screen.getByRole('button', { name: '添加步骤' }))
    expect(screen.getByText('需要转换会话范围')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(onChange).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '添加步骤' }))
    fireEvent.click(screen.getByRole('button', { name: '转为独立对话并添加' }))
    expect(onChange).toHaveBeenCalledTimes(1)
    const next = onChange.mock.calls[0]?.[0] as WorkflowDefinitionSourceWire
    expect(next.nodes).toHaveLength(2)
    expect(next.nodes[0]?.conversation_scope).toBe('isolated')
    expect(next.nodes[1]?.access_mode).toBe('write')
    expect(next.edges).toEqual([])
  })

  it('keeps every required delivery output visible and edits one item without collapsing the list', () => {
    const source = structuredClone(WORKFLOW_EDITOR_FIXTURE.sources.multiOutput)
    source.nodes[0]?.output_contracts.push({ kind: 'TextResult', version: 1, slot: 'archive', required_for_node_completion: true })
    const onChange = renderEditor(source)
    expect(screen.getByLabelText('最终输出 1')).toBeTruthy()
    expect(screen.getByLabelText('最终输出 2')).toBeTruthy()
    expect(screen.getAllByText('produce.summary').length).toBeGreaterThan(0)
    expect(screen.getAllByText('produce.evidence').length).toBeGreaterThan(0)
    fireEvent.change(screen.getByLabelText('最终输出 2'), { target: { value: 'produce.archive' } })
    const next = onChange.mock.lastCall?.[0] as WorkflowDefinitionSourceWire
    expect(next.required_outputs).toEqual([
      { node_id: 'produce', output_slot: 'summary' },
      { node_id: 'produce', output_slot: 'archive' },
    ])
  })

  it('previews edge deletion and commits control plus selected data cleanup once', () => {
    const onChange = renderEditor()
    fireEvent.click(screen.getByRole('button', { name: '删除连接 research → implement' }))
    expect(screen.getByText('确认删除控制依赖')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(onChange).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '删除连接 research → implement' }))
    fireEvent.click(screen.getByLabelText('同时移除这些数据传递'))
    fireEvent.click(screen.getByRole('button', { name: '确认删除依赖' }))
    expect(onChange).toHaveBeenCalledTimes(1)
    const next = onChange.mock.lastCall?.[0] as WorkflowDefinitionSourceWire
    expect(next.edges).toEqual([{ from_node_id: 'implement', to_node_id: 'review' }])
    expect(next.nodes.find(node => node.node_id === 'implement')?.input_bindings).toEqual([])
  })

  it('shows node deletion impact before removing a non-final step', () => {
    const onChange = renderEditor()
    fireEvent.click(screen.getByRole('button', { name: '删除步骤' }))
    expect(screen.getByText('确认删除步骤')).toBeTruthy()
    expect(screen.getByText('受影响的控制依赖：research->implement')).toBeTruthy()
    expect(onChange).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(onChange).not.toHaveBeenCalled()
  })

  it('requires a replacement before deleting the only selected final output', () => {
    const source = fixtureSource(
      'delete_output',
      '删除结果',
      [THREE_STEP_SOURCE.nodes[0]!, THREE_STEP_SOURCE.nodes[1]!],
      [{ from_node_id: 'research', to_node_id: 'implement' }],
      [{ node_id: 'research', output_slot: 'result' }],
    )
    const onChange = renderEditor(source)
    fireEvent.click(screen.getByRole('button', { name: '删除步骤' }))
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    expect(screen.getByRole('alert').textContent).toContain('选择不同的替代结果')
    expect(onChange).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('替代结果 research.result'), { target: { value: 'implement.result' } })
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    expect(onChange).toHaveBeenCalledTimes(1)
    const next = onChange.mock.lastCall?.[0] as WorkflowDefinitionSourceWire
    expect(next.nodes.map(node => node.node_id)).toEqual(['implement'])
    expect(next.required_outputs).toEqual([{ node_id: 'implement', output_slot: 'result' }])
  })

  it('renames exact output references and keeps consumer contract metadata for explicit repair', () => {
    const onChange = renderEditor()
    fireEvent.change(screen.getByLabelText('结果名称 result'), { target: { value: 'background' } })
    const renamed = onChange.mock.lastCall?.[0] as WorkflowDefinitionSourceWire
    expect(renamed.nodes[0]?.output_contracts[0]?.slot).toBe('background')
    expect(renamed.nodes[1]?.input_bindings[0]).toMatchObject({
      accepts: { kind: 'TextResult', version: 1 },
      node_output: { node_id: 'research', output_slot: 'background' },
    })

    cleanup()
    const typedChange = vi.fn()
    renderEditor(THREE_STEP_SOURCE, typedChange)
    fireEvent.change(screen.getByLabelText('结果类型 result'), { target: { value: 'ImplementationPatch' } })
    const typed = typedChange.mock.lastCall?.[0] as WorkflowDefinitionSourceWire
    expect(typed.nodes[0]?.output_contracts[0]?.kind).toBe('ImplementationPatch')
    expect(typed.nodes[1]?.input_bindings[0]).toMatchObject({ accepts: { kind: 'TextResult', version: 1 } })
  })
})
