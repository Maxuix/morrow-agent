import { describe, expect, it } from 'vitest'
import { budgetDisplay } from './budget'

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
