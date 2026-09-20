import { describe, expect, it } from 'vitest'
import type { TimelineItem } from '../../api/chat'
import { EDIT_FORK_DISABLED_HINT, editForkState } from './messageActions'

function item(fields: Partial<TimelineItem> = {}): TimelineItem {
  return {
    item_id: 'item-1',
    kind: 'user_message',
    workspace_id: 'ws-1',
    session_id: 'ses-root',
    order_key: [0, 0, 0, 'item-1'],
    revision: 1,
    source: { origin_session_id: 'ses-root', record_id: 'rec-1' },
    content: 'hello',
    content_ref: null,
    ...fields,
  }
}

describe('editForkState', () => {
  it('enables the entry when the server projects edit_fork available', () => {
    const state = editForkState(
      item({ actions: { edit_fork: { available: true, reason_code: null } } }),
    )
    expect(state).toEqual({ enabled: true, hint: null })
  })

  it('disables with the workflow-leaf hint when the server marks it unavailable', () => {
    const state = editForkState(
      item({
        source: { origin_session_id: 'ses-leaf', record_id: 'rec-leaf' },
        actions: { edit_fork: { available: false, reason_code: 'workflow_leaf' } },
      }),
    )
    expect(state.enabled).toBe(false)
    expect(state.hint).toBe(EDIT_FORK_DISABLED_HINT)
  })

  it('disables the entry when the server does not project an action', () => {
    expect(editForkState(item())).toEqual({ enabled: false, hint: EDIT_FORK_DISABLED_HINT })
  })
})
