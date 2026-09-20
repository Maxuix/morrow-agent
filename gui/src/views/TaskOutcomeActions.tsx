import { useState } from 'react'
import { ApiError, type ApiClient } from '../api/client'
import type { TaskRunStatus, TaskRunWire } from '../api/types'
import { commandId } from './lib/editor'

export interface TaskOutcomeAction {
  kind: 'accept' | 'resume'
  label: string
  hint: string
}

/** Actions offered for a finished task outcome: accept it, or send it back for correction. */
export function taskOutcomeActions(status: TaskRunStatus): TaskOutcomeAction[] {
  if (status !== 'ready_for_acceptance') return []
  return [
    {
      kind: 'accept',
      label: '接受结果',
      hint: '接受结果并结束任务',
    },
    {
      kind: 'resume',
      label: '退回修正',
      hint: '退回修正',
    },
  ]
}

type ActionTask = Pick<TaskRunWire, 'task_run_id' | 'row_version' | 'status'>

/** Shared task-result action surface used by the Inspector. */
export function TaskOutcomeActions({
  client,
  task,
  onChanged,
}: {
  client: ApiClient
  task: ActionTask | null
  onChanged?: (task: TaskRunWire) => void
}) {
  const [pendingAction, setPendingAction] = useState<TaskOutcomeAction['kind'] | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  if (task === null) return null
  const actions = taskOutcomeActions(task.status)
  if (actions.length === 0) return null

  const run = async (kind: TaskOutcomeAction['kind']) => {
    setPendingAction(kind)
    setActionError(null)
    try {
      const updated = kind === 'accept'
        ? await client.acceptTask(task.task_run_id, task.row_version, commandId('task_accept'))
        : await client.resumeTask(task.task_run_id, task.row_version, commandId('task_resume'))
      onChanged?.(updated)
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : '操作失败，请重试。')
    } finally {
      setPendingAction(null)
    }
  }

  return (
    <div className="mt-3 rounded-[10px] border border-subtle bg-raised p-4" aria-label="结果处理">
      <h3 className="text-xs font-medium tracking-wide text-secondary">结果处理</h3>

      <div className="mt-3 flex flex-wrap gap-2">
        {actions.map((action) => (
          <button
            key={action.kind}
            type="button"
            disabled={pendingAction !== null}
            title={action.hint}
            onClick={() => void run(action.kind)}
            className={`rounded-[8px] border px-3 py-1.5 text-sm transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-55 ${
              action.kind === 'accept'
                ? 'border-accent text-accent hover:bg-base'
                : 'border-subtle text-primary hover:border-accent'
            }`}
          >
            {pendingAction === action.kind ? '处理中…' : action.label}
          </button>
        ))}
      </div>
      {actionError !== null && <p role="alert" className="mt-2 text-xs text-failed">{actionError}</p>}
    </div>
  )
}
