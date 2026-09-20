import { describe, expect, it } from 'vitest'
import { lineDiff } from './lineDiff'

describe('lineDiff', () => {
  it('marks added and removed lines with both line numbers', () => {
    const result = lineDiff('alpha\nbeta', 'alpha\ngamma')
    expect(result.tooLarge).toBe(false)
    expect(result.added).toBe(1)
    expect(result.removed).toBe(1)
    expect(result.lines.filter(line => line.kind !== 'equal')).toEqual([
      { kind: 'remove', text: 'beta', before: 2 },
      { kind: 'add', text: 'gamma', after: 2 },
    ])
  })

  it('reports a large file instead of pretending to diff it', () => {
    const big = Array.from({ length: 900 }, (_, index) => `line ${index}`).join('\n')
    const result = lineDiff(big, 'small')
    expect(result.tooLarge).toBe(true)
    expect(result.lines).toEqual([])
  })

  it('collapses long unchanged stretches into a gap marker', () => {
    const before = Array.from({ length: 40 }, (_, index) => `l${index}`).join('\n')
    const after = before.replace('l30', 'changed')
    const result = lineDiff(before, after)
    expect(result.lines.some(line => line.kind === 'gap')).toBe(true)
    expect(result.lines.every(line => line.kind !== 'equal' || Math.abs((line.before ?? 0) - 31) <= 4)).toBe(true)
  })
})
