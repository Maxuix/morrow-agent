import { describe, expect, it } from 'vitest'
import { ApiError } from '../api/client'
import {
  WORKFLOW_STATUSES,
  type RerunResultWire,
  type TaskRunStatus,
  type WorkflowRunWire,
} from '../api/types'
import {
  actionErrorMessage,
  rerunResultMessage,
  rootResumeRequired,
  runControlActions,
} from './RunControls'

function makeRun(overrides: Partial<WorkflowRunWire> = {}): WorkflowRunWire {
  return {
    workflow_run_id: 'wrun_aaa111',
    workflow_revision_id: 'wrev_aaa',
    root_task_run_id: 'task_root',
    status: 'running',
    row_version: 1,
    started_at: null,
    completed_at: null,
    budget_snapshot: {
      max_agent_generation_requests: null,
      default_node_max_agent_generation_requests: null,
      admission_timeout_seconds: null,
      max_concurrency: 1,
    },
    admission_deadline_at: null,
    input_artifacts: [],
    result_status: null,
    pending_terminal_intent: null,
    pause_requested: false,
    run_relation: 'initial',
    lineage_budget_root_run_id: null,
    parent_run_id: null,
    superseded_reason: null,
    ...overrides,
  }
}

const ROOT_LOADED: TaskRunStatus = 'accepted'

describe('runControlActions', () => {
  it('pause: only running/draining without a pending pause request', () => {
    for (const status of WORKFLOW_STATUSES) {
      const state = runControlActions(makeRun({ status, pause_requested: false }), ROOT_LOADED)
      expect(state.pause.enabled).toBe(status === 'running' || status === 'draining')
      if (state.pause.enabled) {
        expect(state.pause.reason).toBeNull()
      } else {
        expect(state.pause.reason).toBeTruthy()
      }
    }
    // A durable pause request already in flight disables a second pause.
    expect(
      runControlActions(makeRun({ status: 'running', pause_requested: true }), ROOT_LOADED).pause,
    ).toEqual({ enabled: false, reason: '已请求暂停，等待落定' })
    expect(
      runControlActions(makeRun({ status: 'draining', pause_requested: true }), ROOT_LOADED).pause
        .enabled,
    ).toBe(false)
    // Paused settles the request, but the resume button owns that state.
    expect(
      runControlActions(makeRun({ status: 'paused', pause_requested: true }), ROOT_LOADED).pause
        .enabled,
    ).toBe(false)
  })

  it('resume: only a settled paused run; draining must settle first', () => {
    for (const status of WORKFLOW_STATUSES) {
      const state = runControlActions(makeRun({ status }), ROOT_LOADED)
      expect(state.resume.enabled).toBe(status === 'paused')
    }
    expect(
      runControlActions(makeRun({ status: 'draining', pause_requested: true }), ROOT_LOADED).resume
        .enabled,
    ).toBe(false)
  })

  it('cancel: running/draining only — a paused run must be resumed first', () => {
    for (const status of WORKFLOW_STATUSES) {
      const state = runControlActions(makeRun({ status }), ROOT_LOADED)
      expect(state.cancel.enabled).toBe(status === 'running' || status === 'draining')
    }
    const paused = runControlActions(makeRun({ status: 'paused' }), ROOT_LOADED)
    expect(paused.cancel.enabled).toBe(false)
    expect(paused.cancel.reason).toContain('恢复')
  })

  it('retry: requires a failed parent and a loaded root task row', () => {
    for (const status of WORKFLOW_STATUSES) {
      const state = runControlActions(makeRun({ status }), ROOT_LOADED)
      expect(state.retry.enabled).toBe(status === 'failed')
    }
    expect(runControlActions(makeRun({ status: 'failed' }), null).retry.enabled).toBe(false)
    expect(runControlActions(makeRun({ status: 'failed' }), null).retry.reason).toContain(
      '根任务未加载',
    )
    const ready = runControlActions(makeRun({ status: 'failed' }), 'ready_for_acceptance')
    expect(ready.retry.enabled).toBe(true)
    expect(ready.retry.reason).toBeNull()
  })

  it('full rerun: every terminal status (failed/cancelled/completed/superseded)', () => {
    for (const status of WORKFLOW_STATUSES) {
      const state = runControlActions(makeRun({ status }), ROOT_LOADED)
      expect(state.rerun.enabled).toBe(
        status === 'failed' || status === 'cancelled' || status === 'completed' || status === 'superseded',
      )
    }
    expect(runControlActions(makeRun({ status: 'completed' }), null).rerun.enabled).toBe(false)
    expect(runControlActions(makeRun({ status: 'completed' }), null).rerun.reason).toContain(
      '根任务未加载',
    )
  })
})

describe('rootResumeRequired', () => {
  it('reopens the root task from every resumable non-open status', () => {
    // The runtime only accepts a rerun child against an OPEN root task.
    for (const status of ['failed', 'cancelled', 'ready_for_acceptance'] as const) {
      expect(rootResumeRequired(status)).toBe(true)
    }
    for (const status of ['open', 'accepted', 'abandoned'] as const) {
      expect(rootResumeRequired(status)).toBe(false)
    }
  })
})

describe('actionErrorMessage', () => {
  it('passes ApiError messages through as-is (e.g. OCC conflicts)', () => {
    const conflict = new ApiError(409, 'workflow_collision', 'workflow row version conflict')
    expect(actionErrorMessage(conflict)).toBe('workflow row version conflict')
  })

  it('handles generic errors and unknown values', () => {
    expect(actionErrorMessage(new Error('boom'))).toBe('boom')
    expect(actionErrorMessage('plain')).toBe('plain')
  })
})

describe('rerunResultMessage', () => {
  function makeResult(overrides: Partial<RerunResultWire> = {}): RerunResultWire {
    return {
      parent: makeRun(),
      child: makeRun({ workflow_run_id: 'wrun_bbb222' }),
      full: false,
      inherited_node_ids: ['ok_node'],
      execution_node_ids: ['fail_a', 'fail_b', 'fail_c'],
      driving: true,
      ...overrides,
    }
  }

  it('surfaces the new accounting root and the execution node count', () => {
    const message = rerunResultMessage(makeResult())
    expect(message).toContain('wrun_bbb')
    expect(message).toContain('新预算根')
    expect(message).toContain('执行 3 个节点')
  })

  it('labels a full rerun as not inheriting prior outputs', () => {
    expect(rerunResultMessage(makeResult({ full: true }))).toContain('不继承先前节点输出')
  })
})
