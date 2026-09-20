import { useEffect, useState } from 'react'
import { ApiError, type ApiClient } from '../api/client'
import type {
  RecoveryEligibilityWire,
  RerunResultWire,
  RunViewWire,
  TaskRunStatus,
  TaskRunWire,
  WorkflowRunWire,
  WorkflowStatus,
} from '../api/types'
import { commandId } from './lib/editor'

/**
 * Run-control action row for the selected run.
 *
 * Pure enablement logic lives in `runControlActions` so it can be tested
 * without rendering; the component only wires buttons to the client. Success
 * messages surface the command result (new budget root, execution nodes) —
 * the sync store's event stream refreshes the run view, so nothing is
 * refetched here.
 */

export type RunActionId = 'continue' | 'pause' | 'resume' | 'cancel' | 'retry' | 'rerun'

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
 * - continue: a failed run may take the restricted "从中断处继续" recovery;
 *   the server re-validates the eligibility inside the command, and a
 *   fetched assessment disables the button with its concrete reason;
 * - pause: running/draining without a pending pause request;
 * - resume: only a settled paused run (draining must settle first);
 * - cancel: running/draining; a paused run must be resumed first (server
 *   refuses cancelling it);
 * - retry: failed parent with the root task row loaded (partial rerun — a
 *   NEW child run, never a continuation of the failed one);
 * - rerun: any terminal parent (failed/cancelled/completed/superseded — a
 *   superseded run is still a terminal parent the runtime accepts) with the
 *   root task row loaded (full rerun).
 */
export function runControlActions(
  run: WorkflowRunWire,
  rootTaskStatus: TaskRunStatus | null,
  recovery: Pick<RecoveryEligibilityWire, 'eligible' | 'reasons'> | null = null,
): RunActionStates {
  const { status, pause_requested: pauseRequested } = run
  const rootLoaded = rootTaskStatus !== null

  const continueAction: RunActionState = (() => {
    if (status !== 'failed') {
      return disabled('仅失败的运行可从中断处继续')
    }
    if (recovery !== null) {
      if (recovery.eligible) return enabledState
      return disabled(recovery.reasons[0] ?? '该失败不满足受限恢复条件')
    }
    // The assessment has not loaded yet; the server owns the final verdict.
    return enabledState
  })()

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
    retry = disabled('仅运行失败后可重跑失败节点')
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

  return { continue: continueAction, pause, resume, cancel, retry, rerun }
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
  return result.full ? '已开始完整重跑。' : '已开始重跑失败节点。'
}

function ActionButton({
  action,
  label,
  pending,
  onClick,
  confirm = false,
  description,
}: {
  action: RunActionState
  label: string
  pending: boolean
  onClick: () => void
  confirm?: boolean
  description?: string
}) {
  if (!action.enabled) return null
  return (
    <button
      type="button"
      disabled={!action.enabled || pending}
      onClick={onClick}
      title={description}
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
  const [recovery, setRecovery] = useState<RecoveryEligibilityWire | null>(null)

  // A stale two-step cancel confirmation is confusing once the run moved on.
  useEffect(() => {
    setConfirmCancel(false)
  }, [run.status, run.row_version])

  // A failed run's "从中断处继续" availability comes from the same server
  // eligibility the resume command re-validates in its transaction.
  useEffect(() => {
    setRecovery(null)
    if (run.status !== 'failed') return
    let cancelled = false
    client
      .recoveryEligibility(run.workflow_run_id)
      .then((assessment) => {
        if (!cancelled) setRecovery(assessment)
      })
      .catch(() => {
        // Without an assessment the server-side verdict still guards the click.
        if (!cancelled) setRecovery(null)
      })
    return () => {
      cancelled = true
    }
  }, [client, run.status, run.workflow_run_id])

  const actions = runControlActions(run, rootTask?.status ?? null, recovery)
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

  // "从中断处继续" is the failed-run primary action: the same resume command
  // performs the restricted recovery first, then the ordinary resume flow.
  // A refused recovery surfaces the concrete reasons through the error banner.
  const handleContinue = () =>
    void perform('continue', () =>
      client
        .resumeRun(run.workflow_run_id, commandId('continue'))
        .then(() => '已从中断处继续；同一运行正在恢复执行'),
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
          action={actions.continue}
          label="从中断处继续"
          description="复用已有结果，从中断处继续"
          pending={busy}
          onClick={handleContinue}
        />
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
        {(actions.retry.enabled || actions.rerun.enabled) && <details><summary className="editor-button cursor-pointer">重跑</summary><div className="flex flex-col gap-2 p-2">
        <ActionButton
          action={actions.retry}
          label="重跑失败节点"
          description="重新执行失败步骤"
          pending={busy}
          onClick={() => createRerun(false)}
        />
        <ActionButton
          action={actions.rerun}
          label="完整重跑"
          description="重新执行全部步骤，不复用结果"
          pending={busy}
          onClick={() => createRerun(true)}
        />
        </div></details>}
        {onEditPending !== undefined && editPendingEnabled && (
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

      {run.status === 'failed' && !actions.continue.enabled && actions.continue.reason && <details className="mt-2 text-xs text-secondary"><summary>无法继续的原因</summary><p>{actions.continue.reason}</p></details>}
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
