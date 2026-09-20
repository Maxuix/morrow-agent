import { TASK_STATUS_LABELS } from './labels'

/** Translate only service-generated fallback summaries, never real result text. */
export function outcomeSummary(summary: string, status: string): string {
  const label = TASK_STATUS_LABELS[status as keyof typeof TASK_STATUS_LABELS] ?? '任务已结束'
  if (/^TaskRun [\w-]+ is [\w-]+\.$/.test(summary)) return label
  const completed = /^Workflow '[\s\S]*' completed with result ([\w-]+)\.$/.exec(summary)
  if (completed) {
    if (completed[1] === 'needs_revision') return '需要修改'
    if (completed[1] === 'succeeded') return label
    return `${label}：${completed[1]}`
  }
  if (/^Workflow '[\s\S]*' requires revision\.$/.test(summary)) return '需要修改'
  if (/^Workflow '[\s\S]*' was abandoned while blocked\.$/.test(summary)) return '任务已放弃'
  const closed = /^Workflow '[\s\S]*' closed: ([\w-]+)\.$/.exec(summary)
  if (closed) {
    const reasons: Record<string, string> = {
      node_failed: '步骤执行失败',
      cancelled: '任务已取消',
      user_cancelled: '任务已取消',
      request_cap_exceeded: '已达模型请求上限',
    }
    return reasons[closed[1]] ?? `${label}：${closed[1]}`
  }
  return summary || label
}
