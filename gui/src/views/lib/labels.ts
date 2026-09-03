/**
 * Status / enum → display label and dot styling. Pure and deterministic.
 *
 * UI copy follows the CLI's Chinese localization (审批/会话/工作流); technical
 * terms (run, node, revision id) stay in English. Color is never the only
 * signal: every status is rendered as dot + text label.
 */
import type { TaskRunStatus, WorkflowRunWire, WorkflowStatus } from '../../api/types'
import type { ConnectionState } from '../../state/sync'

export const WORKFLOW_STATUS_LABELS: Record<WorkflowStatus, string> = {
  queued: '排队中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  blocked: '受阻',
  draining: '正在暂停',
  paused: '已暂停',
  superseded: '已被取代',
}

/**
 * Tailwind classes for the status dot. `queued` is outline-only per the
 * design language; draining/paused share the warm gray; cancelled and
 * superseded use the muted outline treatment.
 */
export function statusDotClass(status: WorkflowStatus): string {
  switch (status) {
    case 'running':
      return 'bg-running'
    case 'completed':
      return 'bg-completed'
    case 'failed':
      return 'bg-failed'
    case 'blocked':
      return 'bg-blocked'
    case 'draining':
    case 'paused':
    case 'cancelled':
      return 'bg-paused'
    case 'queued':
    case 'superseded':
      return 'border-2 border-queued'
  }
}

export const TASK_STATUS_LABELS: Record<TaskRunStatus, string> = {
  open: '进行中',
  ready_for_acceptance: '待验收',
  accepted: '已验收',
  cancelled: '已取消',
  failed: '失败',
  abandoned: '已放弃',
}

export const RUN_RELATION_LABELS: Record<WorkflowRunWire['run_relation'], string> = {
  initial: '初始',
  continuation: '延续',
  rerun: '重跑',
}

export const CONNECTION_LABELS: Record<ConnectionState, string> = {
  connecting: '正在连接…',
  live: '已连接',
  reconnecting: '连接中断，正在重连…',
  offline: '已离线',
}

/** Compact id for display: keeps the type prefix, truncates the body. */
export function shortId(id: string): string {
  const separator = id.indexOf('_')
  if (separator < 0) return id.length <= 10 ? id : `${id.slice(0, 10)}…`
  const body = id.slice(separator + 1)
  return body.length <= 6 ? id : `${id.slice(0, separator + 1)}${body.slice(0, 6)}…`
}

/** ISO timestamp → `MM-DD HH:MM:SS` local time; returns the raw string if unparseable. */
export function formatTimestamp(iso: string | null): string {
  if (iso === null) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (value: number) => String(value).padStart(2, '0')
  return (
    `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  )
}
