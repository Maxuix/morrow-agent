import { describe, expect, it } from 'vitest'
import type { PreRunSummaryWire } from '../../api/types'
import { budgetDisplay, preRunSummaryLine } from './budget'

describe('budgetDisplay', () => {
  it('formats used / max and computes the remaining allowance', () => {
    expect(budgetDisplay(12, 200, 12)).toEqual({
      current: '12 / 200',
      lineage: null,
      remaining: 188,
    })
  })

  it('surfaces lineage totals only when they differ from the run count', () => {
    const display = budgetDisplay(12, 200, 45)
    expect(display.current).toBe('12 / 200')
    expect(display.lineage).toBe('谱系累计 45 / 200')
    expect(display.remaining).toBe(188)
  })

  it('handles an exhausted budget without inventing a floor', () => {
    expect(budgetDisplay(200, 200, 200).remaining).toBe(0)
    expect(budgetDisplay(201, 200, 201).remaining).toBe(-1)
  })

  it('shows request accounting without inventing a cap', () => {
    expect(budgetDisplay(27, null, 42)).toEqual({
      current: '27 / 无上限',
      lineage: '谱系累计 42 / 无上限',
      remaining: null,
    })
  })
})

function makeSummary(overrides: Partial<PreRunSummaryWire> = {}): PreRunSummaryWire {
  return {
    node_count: 4,
    models: ['a', 'b'],
    providers: ['p'],
    max_agent_generation_requests: 150,
    default_node_max_agent_generation_requests: null,
    admission_timeout_seconds: null,
    max_concurrency: 6,
    writer_node_ids: ['n1', 'n2'],
    ...overrides,
  }
}

describe('preRunSummaryLine', () => {
  it('formats the §14.1 cost facts with a finite cap and writer nodes', () => {
    expect(preRunSummaryLine(makeSummary())).toBe(
      '节点 4 · 模型 a, b · 上限 150 次请求 · 并行度 6 · 写入节点：n1, n2',
    )
  })

  it('states an absent cap and no writers explicitly', () => {
    expect(
      preRunSummaryLine(
        makeSummary({ max_agent_generation_requests: null, writer_node_ids: [] }),
      ),
    ).toBe('节点 4 · 模型 a, b · 上限 无上限 · 并行度 6 · 写入节点：无')
  })

  it('does not invent models when the summary has none', () => {
    expect(preRunSummaryLine(makeSummary({ models: [] }))).toContain('模型 —')
  })
})
