import { useRef, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { RecoveryCheck, RecoveryStatus } from '../api/chat'
import { commandId } from './lib/editor'

const STATE_LABELS: Record<RecoveryStatus['display_state'], string> = {
  idle: '无需恢复',
  checking: '正在核对恢复状态',
  reconnecting: '正在重新连接',
  paused: '执行已暂停',
  resumable: '可以继续上次执行',
  needs_review: '需要核对执行状态',
  unknown_side_effect: '副作用结果未知',
  quarantined: '执行已隔离',
  unsupported: '当前执行无法安全接续',
}

const ACTION_LABELS: Record<string, string> = {
  check: '检查恢复状态',
  review: '查看执行状态',
  resume: '继续上次对话',
  resume_workflow: '继续工作流',
  resume_generation: '继续生成计划',
  continue: '继续执行',
  acknowledge: '确认这项记录',
  abort: '结束原执行',
  quarantine: '隔离原执行',
  new_session: '新建对话',
}

const RESOLUTION_ACTIONS = new Set(['acknowledge', 'abort', 'quarantine'])

export interface RecoveryBannerProps {
  client: ApiClient
  workspace: string
  session: string
  status: RecoveryStatus | null
  onChanged: () => Promise<void> | void
  onNewSession: () => Promise<void> | void
  onReview?: () => void
}

/**
 * Inline recovery controls. The banner is deliberately owner-aware: every
 * mutation goes through the Chat, planning-generation, or Workflow endpoint
 * selected by the server projection. It never calls discover and never sends
 * a second resume after a report-level resolve.
 */
export function RecoveryBanner({
  client,
  workspace,
  session,
  status,
  onChanged,
  onNewSession,
  onReview,
}: RecoveryBannerProps) {
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [hiddenRevision, setHiddenRevision] = useState<number | null>(null)
  const command = useRef<{ key: string; id: string } | null>(null)

  if (status === null || status.display_state === 'idle') return null

  if (hiddenRevision === status.revision) {
    return (
      <section className="chat-recovery" aria-label="执行恢复提示">
        <button type="button" className="editor-button" onClick={() => setHiddenRevision(null)}>
          显示恢复提示
        </button>
      </section>
    )
  }

  const commandFor = (key: string) => {
    if (command.current?.key === key) return command.current.id
    const id = commandId('recovery')
    command.current = { key, id }
    return id
  }

  const run = async (action: string, check?: RecoveryCheck) => {
    if (pending !== null) return
    if (action === 'new_session') {
      setError(null)
      setPending(action)
      try {
        await onNewSession()
      } catch (value) {
        setError(value instanceof Error ? value.message : '新对话创建失败，请重试')
      } finally {
        setPending(null)
      }
      return
    }
    if (action === 'review') {
      onReview?.()
      return
    }

    const target = status.opaque_target
    const checkTarget = check?.opaque_target ?? null
    const checkItem = check?.opaque_item ?? null
    const key = JSON.stringify({
      revision: status.revision,
      owner: status.owner,
      action,
      target,
      checkTarget,
      checkItem,
    })
    const id = commandFor(key)
    setError(null)
    setPending(action)
    try {
      if (status.owner === 'chat') {
        if (action === 'resume' && status.opaque_target_kind !== 'report') {
          if (!target) throw new Error('恢复目标尚未就绪，请重新检查状态')
          await client.chatRecovery(workspace, session, {
            command_id: id,
            action: 'resume',
            target_agent_run_id: target,
          })
        } else if (RESOLUTION_ACTIONS.has(action) || action === 'resume') {
          const reportTarget = checkTarget ?? target
          if (!reportTarget) throw new Error('恢复报告尚未就绪，请重新检查状态')
          await client.chatRecovery(workspace, session, {
            command_id: id,
            action: 'resolve',
            report_id: reportTarget,
            item_id: checkItem,
            resolution: action,
          })
        }
      } else if (status.owner === 'planning' && action === 'resume_generation') {
        if (!target) throw new Error('计划生成目标尚未就绪，请重新检查状态')
        await client.taskPlanGenerationResume(workspace, session, target, {
          command_id: id,
          session_id: session,
        })
      } else if (status.owner === 'workflow') {
        if (!target) throw new Error('工作流目标尚未就绪，请重新检查状态')
        if (action === 'continue') {
          await client.resumeRun(target, id)
        } else if (action === 'resume_workflow' || RESOLUTION_ACTIONS.has(action)) {
          await client.workflowRecovery(workspace, session, target, {
            command_id: id,
            resolution: action === 'resume_workflow' ? 'resume' : action as 'acknowledge' | 'abort' | 'quarantine',
            report_id: checkTarget,
            item_id: checkItem,
          })
        }
      }
      await onChanged()
    } catch (value) {
      setError(value instanceof Error ? value.message : '恢复操作失败；可按原请求重试')
    } finally {
      setPending(null)
    }
  }

  const allowed = new Set(status.allowed_actions)
  const button = (action: string, check?: RecoveryCheck) => {
    const key = check ? `${action}:${check.opaque_item ?? check.summary}` : action
    return (
      <button
        type="button"
        className="editor-button"
        key={key}
        disabled={pending !== null || !allowed.has(action)}
        onClick={() => void run(action, check)}
      >
        {ACTION_LABELS[action] ?? action}
      </button>
    )
  }

  return (
    <section
      className="chat-recovery"
      aria-label="执行恢复"
      data-recovery-state={status.display_state}
      data-recovery-owner={status.owner ?? 'none'}
    >
      <p role="status"><strong>{STATE_LABELS[status.display_state]}</strong>：{status.safe_summary}</p>
      {status.checks.map((check, index) => (
        <div className="chat-recovery-check" key={`${check.opaque_item ?? 'check'}-${index}`}>
          <p>{check.summary}</p>
          <div className="flex flex-wrap gap-2">
            {check.allowed_actions.filter(action => allowed.has(action)).map(action => button(action, check))}
          </div>
        </div>
      ))}
      <div className="flex flex-wrap gap-2">
        {status.allowed_actions
          .filter(action => !RESOLUTION_ACTIONS.has(action) && action !== 'check')
          .map(action => button(action))}
      </div>
      {status.owner === 'chat' && status.display_state === 'resumable' && (
        <p className="text-xs text-secondary">原任务将继续运行。</p>
      )}
      <button type="button" className="editor-button" onClick={() => setHiddenRevision(status.revision)}>
        暂时隐藏提示
      </button>
      {error && <p role="alert" className="text-failed">{error}</p>}
    </section>
  )
}
