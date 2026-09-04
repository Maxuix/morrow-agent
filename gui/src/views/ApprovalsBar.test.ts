import { describe, expect, it } from 'vitest'
import { filterResolvedApprovals } from './ApprovalsBar'
import { taskOutcomeActions } from './TaskWorkspace'

describe('filterResolvedApprovals', () => {
  it('drops approvals this client just resolved, keeping order', () => {
    const approvals = [
      { approval_id: 'ap_1' },
      { approval_id: 'ap_2' },
      { approval_id: 'ap_3' },
    ]
    expect(filterResolvedApprovals(approvals, new Set(['ap_2']))).toEqual([
      { approval_id: 'ap_1' },
      { approval_id: 'ap_3' },
    ])
  })

  it('returns the same list when nothing was resolved and never mutates', () => {
    const approvals = [{ approval_id: 'ap_1' }, { approval_id: 'ap_2' }]
    const filtered = filterResolvedApprovals(approvals, new Set())
    expect(filtered).toEqual(approvals)
    expect(approvals).toHaveLength(2)
  })
})

describe('taskOutcomeActions', () => {
  it('offers accept and resume exactly when the outcome is ready for acceptance', () => {
    const actions = taskOutcomeActions('ready_for_acceptance')
    expect(actions.map((action) => action.kind)).toEqual(['accept', 'resume'])
    expect(actions.map((action) => action.label)).toEqual(['接受结果', '退回修正'])
    for (const action of actions) {
      expect(action.hint).toBeTruthy()
    }
  })

  it('offers nothing for any other task status', () => {
    for (const status of ['open', 'accepted', 'cancelled', 'failed', 'abandoned'] as const) {
      expect(taskOutcomeActions(status)).toEqual([])
    }
  })
})
