import { describe, expect, it } from 'vitest'
import { taskOutcomeActions } from './TaskOutcomeActions'

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
