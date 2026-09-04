import { useEffect, useState } from 'react'
import { ApiError, type ApiClient } from '../api/client'
import type {
  RerunResultWire,
  RunViewWire,
  TaskRunStatus,
  TaskRunWire,
  WorkflowRunWire,
  WorkflowStatus,
} from '../api/types'
import { commandId } from './lib/editor'
import { shortId } from './lib/labels'

/**
 * Run-control action row for the selected run.
 *
 * Pure enablement logic lives in `runControlActions` so it can be tested
 * without rendering; the component only wires buttons to the client. Success
 * messages surface the command result (new budget root, execution nodes) —
 * the sync store's event stream refreshes the run view, so nothing is
 * refetched here.
 */

export type RunActionId = 'pause' | 'resume' | 'cancel' | 'retry' | 'rerun'

export interface RunActionState {
  enabled: boolean
  /** Tooltip reason when disabled; null when enabled. */
  reason: string | null
}

export type RunActionStates = Record<RunActionId, RunActionState>

const disabled = (reason: string): RunActionState => ({ enabled: false, reason })
const enabledState: RunActionState = { enabled: true, reason: null }

const RERUN_TERMINAL_STATUSES: ReadonlySet<WorkflowStatus> = new Set([
  'failed',
  'cancelled',
  'completed',
  'superseded',
])

/**
 * Enablement matrix mirroring the server semantics:
 * - pause: running/draining without a pending pause request;
 * - resume: only a settled paused run (draining must settle first);
 * - cancel: running/draining; a paused run must be resumed first (server
 *   refuses cancelling it);
 * - retry: failed parent with the root task row loaded (partial rerun);
 * - rerun: any terminal parent (failed/cancelled/completed/superseded — a
 *   superseded run is still a terminal parent the runtime accepts) with the
 *   root task row loaded (full rerun).
 */
export function runControlActions(
  run: WorkflowRunWire,
  rootTaskStatus: TaskRunStatus | null,
): RunActionStates {
  const { status, pause_requested: pauseRequested } = run
  const rootLoaded = rootTaskStatus !== null

  let pause: RunActionState
  if (status !== 'running' && status !== 'draining') {
    pause = disabled('仅运行中或正在暂停时可请求暂停')
  } else if (pauseRequested) {
    pause = disabled('已请求暂停，等待落定')
  } else {
    pause = enabledState
  }

  const resume: RunActionState =
    status === 'paused' ? enabledState : disabled('仅已暂停的运行可恢复')

  let cancel: RunActionState
  if (status === 'paused') {
    cancel = disabled('已暂停的运行需先恢复才能取消')
  } else if (status !== 'running' && status !== 'draining') {
    cancel = disabled('仅运行中或正在暂停时可取消')
  } else {
    cancel = enabledState
  }

  let retry: RunActionState
  if (status !== 'failed') {
    retry = disabled('仅运行失败后可重试失败节点')
  } else if (!rootLoaded) {
    retry = disabled('根任务未加载，无法重试')
  } else {
    retry = enabledState
  }

  let rerun: RunActionState
  if (!RERUN_TERMINAL_STATUSES.has(status)) {
    rerun = disabled('运行结束（失败/取消/完成/被取代）后可完整重跑')
  } else if (!rootLoaded) {
    rerun = disabled('根任务未加载，无法完整重跑')
  } else {
    rerun = enabledState
  }

  return { pause, resume, cancel, retry, rerun }
}

/**
 * The runtime only creates a rerun child against an OPEN root task, so any
 * non-open root (failed/cancelled/ready_for_acceptance) takes the explicit
 * root-resume step first (roadmap §13, same as the CLI rerun flow).
 */
export function rootResumeRequired(rootTaskStatus: TaskRunStatus): boolean {
  // The resumable non-open statuses; accepted/abandoned roots are rejected by
  // the server with its own message.
  return (
    rootTaskStatus === 'failed' ||
    rootTaskStatus === 'cancelled' ||
    rootTaskStatus === 'ready_for_acceptance'
  )
}

/** Inline banner text: ApiError messages pass through as-is (e.g. OCC conflicts). */
export function actionErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return String(error)
}

/** Result message: new accounting root, never an inherited-output reuse. */
export function rerunResultMessage(result: RerunResultWire): string {
  const root = result.full
    ? '新预算根，不继承先前节点输出'
    : '新预算根，重跑失败节点'
  return `已创建子运行 ${shortId(result.child.workflow_run_id)}（${root}），执行 ${result.execution_node_ids.length} 个节点`
}

function ActionButton({
  action,
  label,
  pending,
  onClick,
  confirm = false,
}: {
  action: RunActionState
  label: string
  pending: boolean
  onClick: () => void
  confirm?: boolean
}) {
  return (
    <button
      type="button"
      disabled={!action.enabled || pending}
      onClick={onClick}
      title={action.enabled ? undefined : (action.reason ?? undefined)}
      className={`rounded-[8px] border px-2.5 py-1 text-xs transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-45 ${
        confirm
          ? 'border-failed bg-raised text-failed'
          : 'border-subtle bg-base text-primary hover:border-accent'
      }`}
    >
      {label}
    </button>
  )
}

export function RunControls({
  run,
  runView,
  client,
  rootTask,
  onEditPending,
}: {
  run: WorkflowRunWire
  runView: RunViewWire | null
  client: ApiClient
  rootTask: TaskRunWire | null
  onEditPending?: () => void
}) {
  const [pendingAction, setPendingAction] = useState<RunActionId | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [confirmCancel, setConfirmCancel] = useState(false)

  // A stale two-step cancel confirmation is confusing once the run moved on.
  useEffect(() => {
    setConfirmCancel(false)
  }, [run.status, run.row_version])

  const actions = runControlActions(run, rootTask?.status ?? null)
  const busy = pendingAction !== null

  async function perform(action: RunActionId, work: () => Promise<string>): Promise<void> {
    setPendingAction(action)
    setError(null)
    setSuccess(null)
    try {
      setSuccess(await work())
    } catch (err) {
      setError(actionErrorMessage(err))
    } finally {
      setPendingAction(null)
    }
  }

  const handlePause = () =>
    void perform('pause', () =>
      client
        .pauseRun(run.workflow_run_id, commandId('pause'))
        .then(() => '已请求暂停，正在落定'),
    )

  const handleResume = () =>
    void perform('resume', () =>
      client
        .resumeRun(run.workflow_run_id, commandId('resume'))
        .then(() => '已恢复运行'),
    )

  const handleCancel = () => {
    if (!confirmCancel) {
      setConfirmCancel(true)
      setSuccess(null)
      return
    }
    void perform('cancel', () =>
      client
        .cancelRun(run.workflow_run_id, commandId('cancel'))
        .then(() => '已请求取消'),
    )
  }

  // Retry (partial) and full rerun share the explicit root-resume pre-step.
  const createRerun = (full: boolean) => {
    const action: RunActionId = full ? 'rerun' : 'retry'
    void perform(action, async () => {
      if (rootTask !== null && rootResumeRequired(rootTask.status)) {
        await client.resumeTask(
          rootTask.task_run_id,
          rootTask.row_version,
          commandId('task_resume'),
        )
      }
      const result = await client.rerunRun(run.workflow_run_id, full, commandId('rerun'))
      return rerunResultMessage(result)
    })
  }

  const editPendingEnabled = run.status === 'paused' && runView !== null

  return (
    <div className="mt-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <ActionButton
          action={actions.pause}
          label="暂停"
          pending={busy}
          onClick={handlePause}
        />
        <ActionButton
          action={actions.resume}
          label="恢复"
          pending={busy}
          onClick={handleResume}
        />
        <ActionButton
          action={actions.cancel}
          label={confirmCancel ? '确认取消？' : '取消'}
          pending={busy}
          onClick={handleCancel}
          confirm={confirmCancel}
        />
        <ActionButton
          action={actions.retry}
          label="重试失败节点"
          pending={busy}
          onClick={() => createRerun(false)}
        />
        <ActionButton
          action={actions.rerun}
          label="完整重跑"
          pending={busy}
          onClick={() => createRerun(true)}
        />
        {onEditPending !== undefined && (
          <button
            type="button"
            disabled={!editPendingEnabled || busy}
            onClick={onEditPending}
            title={
              run.status !== 'paused'
                ? '仅暂停后可编辑待定节点'
                : runView === null
                  ? '运行详情未加载'
                  : undefined
            }
            className="rounded-[8px] border border-subtle bg-base px-2.5 py-1 text-xs text-accent transition-colors duration-150 hover:border-accent disabled:cursor-not-allowed disabled:opacity-45"
          >
            编辑待定节点
          </button>
        )}
      </div>
      {error !== null && (
        <div role="alert" className="mt-2 rounded-[8px] border border-blocked px-2 py-1 text-xs text-failed">
          {error}
        </div>
      )}
      {success !== null && (
        <p className="mt-2 font-mono text-xs text-secondary">{success}</p>
      )}
    </div>
  )
}
