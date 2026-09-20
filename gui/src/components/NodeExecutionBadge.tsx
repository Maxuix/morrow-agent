import type { NodeExecutionWire } from '../api/types'
import { nodeExecutionBadge } from '../views/lib/execution'

/**
 * Per-node execution badge (BUG-GUI-002, P02): "已暂停" / "正在暂停" / "待恢复"
 * next to the business status. Renders nothing when the server projects no
 * execution state (terminal/queued nodes) — the client never
 * fabricates a pause on its own.
 */
export function NodeExecutionBadge({
  execution,
  className = '',
}: {
  execution: NodeExecutionWire | null | undefined
  className?: string
}) {
  const label = nodeExecutionBadge(execution)
  if (label === null || execution === undefined || execution === null) return null
  const tone =
    execution.state === 'needs_recovery' ? 'border-blocked text-blocked' : 'border-paused text-paused'
  return (
    <span className={`rounded-[8px] border px-1.5 py-0.5 text-xs ${tone} ${className}`}>
      {label}
    </span>
  )
}
