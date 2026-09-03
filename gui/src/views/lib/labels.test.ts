import { describe, expect, it } from 'vitest'
import { WORKFLOW_STATUSES } from '../../api/types'
import { RUN_RELATION_LABELS, statusDotClass, WORKFLOW_STATUS_LABELS } from './labels'

describe('WORKFLOW_STATUS_LABELS', () => {
  it('covers all 9 workflow statuses with non-empty labels', () => {
    expect(WORKFLOW_STATUSES).toHaveLength(9)
    for (const status of WORKFLOW_STATUSES) {
      expect(WORKFLOW_STATUS_LABELS[status]).toBeTruthy()
    }
    expect(Object.keys(WORKFLOW_STATUS_LABELS).sort()).toEqual([...WORKFLOW_STATUSES].sort())
  })

  it('has a dot class for every status (queued/superseded are outline-only)', () => {
    for (const status of WORKFLOW_STATUSES) {
      const dot = statusDotClass(status)
      expect(dot.length).toBeGreaterThan(0)
      if (status === 'queued' || status === 'superseded') {
        expect(dot).toContain('border')
      } else {
        expect(dot).toContain('bg-')
      }
    }
  })

  it('labels run relations', () => {
    expect(RUN_RELATION_LABELS).toEqual({ initial: '初始', continuation: '延续', rerun: '重跑' })
  })
})
