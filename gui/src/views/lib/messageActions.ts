/**
 * Edit-to-new-chat (edit_fork) entry state for timeline user messages. Pure.
 *
 * The server projects `actions.edit_fork` per item: only the requesting
 * session and its fork ancestors are legal fork sources. Workflow leaf
 * records are visible in the unified timeline but can never be forked from
 * the root view, so the entry is disabled up front with an explanation
 * instead of failing inside the fork endpoint. Missing action projection is
 * treated as unavailable; the client never infers permission from source data.
 */
import type { TimelineItem } from '../../api/chat'

export const EDIT_FORK_DISABLED_HINT =
  '此条为工作流节点记录，暂不支持从此处分支；可回到根任务输入或新建对话。'

export interface EditForkState {
  enabled: boolean
  /** Visible explanation when disabled; null while the entry is usable. */
  hint: string | null
}

export function editForkState(item: TimelineItem): EditForkState {
  const action = item.actions?.edit_fork
  return action?.available
    ? { enabled: true, hint: null }
    : { enabled: false, hint: EDIT_FORK_DISABLED_HINT }
}
