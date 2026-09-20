// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { WorkflowDefinitionSourceWire } from '../../api/types'
import { ConnectionEditor } from './ConnectionEditor'
import { fixtureSource, THREE_STEP_SOURCE } from './fixtures'

afterEach(cleanup)

const source: WorkflowDefinitionSourceWire = fixtureSource(
  'connection_dom',
  '连接 DOM',
  [
    THREE_STEP_SOURCE.nodes[0]!,
    { ...THREE_STEP_SOURCE.nodes[2]!, input_bindings: [] },
  ],
  [],
  [{ node_id: 'review', output_slot: 'result' }],
)

describe('ConnectionEditor semantic DOM', () => {
  it('does not write on cancel and commits control plus an explicitly selected result once', () => {
    const onConfirm = vi.fn()
    render(<ConnectionEditor source={source} initialFromNodeId={null} initialToNodeId="review" disabled={false} nodeStatuses={{}} onConfirm={onConfirm} onCancel={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('上游步骤'), { target: { value: 'research' } })
    fireEvent.click(screen.getByRole('button', { name: '添加结果传递' }))
    expect((screen.getByLabelText('来源结果 1') as HTMLSelectElement).value).toBe('')
    fireEvent.change(screen.getByLabelText('目标输入名称 1'), { target: { value: 'research_context' } })
    fireEvent.change(screen.getByLabelText('来源结果 1'), { target: { value: 'result' } })
    fireEvent.click(screen.getByRole('button', { name: '确认连接修改' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
    const next = onConfirm.mock.calls[0]?.[0] as WorkflowDefinitionSourceWire
    expect(next.edges).toEqual([{ from_node_id: 'research', to_node_id: 'review' }])
    expect(next.nodes[1]?.input_bindings).toEqual([expect.objectContaining({
      input_name: 'research_context',
      node_output: { node_id: 'research', output_slot: 'result' },
    })])
  })

  it('shows existing data transfers as editable rows instead of silently selecting an output', () => {
    const withEdge = {
      ...source,
      edges: [{ from_node_id: 'research', to_node_id: 'review' }],
      nodes: source.nodes.map(node => node.node_id === 'review' ? {
        ...node,
        input_bindings: [{
          source: 'node_output' as const,
          input_name: 'research_context',
          accepts: { kind: 'TextResult' as const, version: 1 as const },
          node_output: { node_id: 'research', output_slot: 'result' },
        }],
      } : node),
    }
    render(<ConnectionEditor source={withEdge} initialFromNodeId="research" initialToNodeId="review" disabled={false} nodeStatuses={{}} onConfirm={vi.fn()} onCancel={vi.fn()} />)
    expect((screen.getByLabelText('来源结果 1') as HTMLSelectElement).value).toBe('result')
    expect(screen.getByText('取消')).toBeTruthy()
  })

  it('repairs an orphan binding only after the user explicitly restores its control edge', () => {
    const orphan = {
      ...source,
      nodes: source.nodes.map(node => node.node_id === 'review' ? {
        ...node,
        input_bindings: [{
          source: 'node_output' as const,
          input_name: 'research_context',
          accepts: { kind: 'TextResult' as const, version: 1 as const },
          node_output: { node_id: 'research', output_slot: 'result' },
        }],
      } : node),
    }
    const onConfirm = vi.fn()
    render(<ConnectionEditor source={orphan} initialFromNodeId="research" initialToNodeId="review" disabled={false} nodeStatuses={{}} onConfirm={onConfirm} onCancel={vi.fn()} />)
    expect((screen.getByRole('checkbox', { name: '完成后继续（控制依赖）' }) as HTMLInputElement).checked).toBe(false)
    fireEvent.click(screen.getByRole('checkbox', { name: '完成后继续（控制依赖）' }))
    fireEvent.click(screen.getByRole('button', { name: '确认连接修改' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
    expect((onConfirm.mock.calls[0]?.[0] as WorkflowDefinitionSourceWire).edges).toEqual([{ from_node_id: 'research', to_node_id: 'review' }])
  })
})
