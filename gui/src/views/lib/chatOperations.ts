import type { InspectorKind } from '../../state/inspector'

/**
 * Slash commands for task and asset operations in the current conversation.
 */
export type ChatOperation =
  | { kind: 'artifacts'; inspector: InspectorKind }
  | { kind: 'workflow'; inspector: InspectorKind }
  | { kind: 'context'; inspector: InspectorKind }
  | { kind: 'compact' }
  | { kind: 'new-task' }
  | { kind: 'accept-task' }
  | { kind: 'cancel-task' }
  | { kind: 'abandon-task' }
  | { kind: 'resume-task' }

type ChatOperationEntry = ChatOperation & { name: string; label: string }

const entries: ChatOperationEntry[] = [
  { name: '/task', label: '查看当前任务与产物', kind: 'artifacts', inspector: 'artifacts' },
  { name: '/task show', label: '查看当前任务与产物', kind: 'artifacts', inspector: 'artifacts' },
  { name: '/task new', label: '创建当前对话任务', kind: 'new-task' },
  { name: '/task accept', label: '接受当前任务结果', kind: 'accept-task' },
  { name: '/task cancel', label: '取消当前任务', kind: 'cancel-task' },
  { name: '/task abandon', label: '放弃当前任务', kind: 'abandon-task' },
  { name: '/task resume', label: '继续当前任务', kind: 'resume-task' },
  { name: '/artifact', label: '打开任务与产物', kind: 'artifacts', inspector: 'artifacts' },
  { name: '/agent-run', label: '查看当前运行上下文', kind: 'context', inspector: 'context' },
  { name: '/compact', label: '压缩当前对话上下文', kind: 'compact' },
]

export const chatOperationEntries = entries.map(({ name, label }) => ({ name, label }))

const byName = new Map(entries.map(entry => [entry.name, entry]))

export function chatOperation(name: string): ChatOperationEntry | undefined {
  return byName.get(name)
}
