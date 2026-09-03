import type { WorkflowStatus } from '../api/types'
import { statusDotClass, WORKFLOW_STATUS_LABELS } from '../views/lib/labels'

/**
 * Status is always dot + text label — color is never the only signal
 * (roadmap §16.5, design decision "运行状态色低饱和化").
 */
export function StatusDot({ status, className = '' }: { status: WorkflowStatus; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <span
        aria-hidden="true"
        className={`inline-block size-2.5 shrink-0 rounded-full ${statusDotClass(status)}`}
      />
      <span>{WORKFLOW_STATUS_LABELS[status]}</span>
    </span>
  )
}
