import { describe, expect, it } from 'vitest'
import {
  chatPausePath,
  executionAcceptsInput,
  executionBusy,
  executionControlTarget,
  executionControls,
  executionLabel,
  executionOfOwner,
  executionStopTarget,
  executionStopping,
  executionTimingFrozen,
  guiControlRequestBody,
  guiPauseRequestBody,
  nodeExecutionBadge,
  nodeStatusText,
  pausedSendRoute,
  runningControlRoute,
} from './execution'
import { chatExecution, executionView as execution, planningExecution, workflowExecution } from '../../parallel-fixtures/execution'
import { isExplicitPauseCommand, isPureContinueCommand } from './taskPlan'

describe('execution state selection', () => {
  it('treats missing and idle projections as not busy', () => {
    expect(executionBusy(null)).toBe(false)
    expect(executionBusy(undefined)).toBe(false)
    expect(executionBusy(execution())).toBe(false)
    expect(executionLabel(null)).toBeNull()
    expect(executionStopTarget(null)).toBeNull()
  })

  it('labels each lifecycle state without color-only signaling', () => {
    expect(executionLabel(execution({ owner: 'workflow', state: 'running' }))).toBe('工作流执行中')
    expect(executionLabel(execution({ owner: 'planning', state: 'running' }))).toBe('正在生成计划')
    expect(executionLabel(execution({ owner: 'chat', state: 'running' }))).toBe('正在运行')
    expect(executionLabel(execution({ owner: 'workflow', state: 'stopping' }))).toBe('正在停止…')
    expect(executionLabel(execution({ owner: 'workflow', state: 'pausing' }))).toBe('正在暂停…')
    expect(executionLabel(execution({ owner: 'workflow', state: 'paused' }))).toBe('已暂停')
    expect(executionLabel(execution({ owner: 'chat', state: 'waiting_approval' }))).toBe('等待工具审批')
    expect(executionLabel(execution({ owner: 'workflow', state: 'needs_recovery' }))).toBe('执行待恢复')
  })

  it('counts parallel nodes as a set, never one compressed run', () => {
    const label = executionLabel(
      execution({ owner: 'workflow', state: 'running', node_run_ids: ['a', 'b'] }),
    )
    expect(label).toContain('2 个并行节点')
    expect(executionLabel(execution({ owner: 'workflow', state: 'running', node_run_ids: ['a'] }))).toBe(
      '工作流执行中',
    )
  })

  it('routes the stop control by execution ownership', () => {
    expect(
      executionStopTarget(execution({ owner: 'chat', state: 'running', allowed_actions: ['stop'] })),
    ).toEqual({ kind: 'chat' })
    expect(
      executionStopTarget(
        execution({
          owner: 'workflow',
          state: 'running',
          workflow_run_id: 'wrun_1',
          allowed_actions: ['stop', 'pause', 'change'],
        }),
      ),
    ).toEqual({ kind: 'workflow', workflowRunId: 'wrun_1' })
    expect(
      executionStopTarget(
        execution({
          owner: 'planning',
          state: 'running',
          planning_operation_id: 'wop_1',
          allowed_actions: ['stop'],
        }),
      ),
    ).toEqual({ kind: 'planning', operationId: 'wop_1' })
  })

  it('keeps the stop control off while stopping or recovering', () => {
    const stopping = execution({
      owner: 'workflow',
      state: 'stopping',
      workflow_run_id: 'wrun_1',
      allowed_actions: [],
    })
    expect(executionStopping(stopping)).toBe(true)
    expect(executionStopTarget(stopping)).toBeNull()
    expect(
      executionStopping(execution({ owner: 'workflow', state: 'pausing', allowed_actions: ['change'] })),
    ).toBe(true)
    expect(
      executionStopping(execution({ owner: 'workflow', state: 'running', allowed_actions: ['stop'] })),
    ).toBe(false)
    expect(
      executionStopTarget(
        execution({ owner: 'workflow', state: 'needs_recovery', allowed_actions: [] }),
      ),
    ).toBeNull()
  })

  it('marks isolated node sessions as execution detail without input', () => {
    const leaf = execution({
      owner: 'workflow',
      state: 'running',
      leaf: true,
      accepts_chat_input: false,
    })
    expect(executionAcceptsInput(leaf)).toBe(false)
    expect(executionAcceptsInput(execution({ owner: 'workflow', state: 'running' }))).toBe(true)
    expect(executionAcceptsInput(null)).toBe(true)
  })

  it('finds secondary executions by owner', () => {
    const view = execution({
      owner: 'workflow',
      state: 'running',
      executions: [
        execution({ owner: 'workflow', state: 'running' }),
        execution({ owner: 'chat', state: 'running', agent_run_id: 'arun_1' }),
      ],
    })
    expect(executionOfOwner(view, 'chat')?.agent_run_id).toBe('arun_1')
    expect(executionOfOwner(view, 'workflow')?.owner).toBe('workflow')
    expect(executionOfOwner(view, 'planning')).toBeNull()
  })
})

describe('unified pause/continue controls (P09.1, D01)', () => {
  it('offers pause only when the server projection allows it', () => {
    expect(executionControls(null)).toEqual({primary: null, cancel: {available: false, settling: false}})
    expect(executionControls(execution({state: 'idle'})).primary).toBeNull()
    // Running without a projected pause action: no primary control.
    expect(executionControls(chatExecution()).primary).toBeNull()
    // The pause action comes from allowed_actions, never a local guess.
    expect(executionControls(workflowExecution()).primary).toEqual({
      kind: 'pause', scope: 'run', label: '暂停', settling: false,
    })
    expect(executionControls(planningExecution()).primary).toEqual({
      kind: 'pause', scope: 'run', label: '暂停', settling: false,
    })
  })

  it('shows settling pause while pausing and keeps cancel independent', () => {
    const pausing = workflowExecution({state: 'pausing', allowed_actions: ['stop', 'change']})
    const controls = executionControls(pausing)
    expect(controls.primary).toEqual({kind: 'pause', scope: 'run', label: '正在暂停…', settling: true})
    expect(controls.cancel).toEqual({available: true, settling: false})
    const stopping = workflowExecution({state: 'stopping', allowed_actions: []})
    expect(executionControls(stopping).primary).toBeNull()
    expect(executionControls(stopping).cancel).toEqual({available: false, settling: true})
  })

  it('derives resume only for a settled paused execution', () => {
    const pausedWorkflow = workflowExecution({state: 'paused', allowed_actions: ['resume', 'change']})
    expect(executionControls(pausedWorkflow).primary).toEqual({kind: 'resume', scope: 'run', label: '继续', settling: false})
    expect(executionControls(pausedWorkflow).cancel.available).toBe(false)
    // Paused without a projected resume action: no local invention.
    expect(executionControls(workflowExecution({state: 'paused', allowed_actions: ['change'] })).primary).toBeNull()
    // Chat queue pause projects resume.
    expect(
      executionControls(chatExecution({state: 'paused', allowed_actions: ['resume']})).primary,
    ).toEqual({kind: 'resume', scope: 'run', label: '继续', settling: false})
  })

  it('follows the projection exactly, including recovery and approval states', () => {
    // needs_recovery normally projects no actions; if the server projects
    // pause, the control appears — the client never overrides the server.
    expect(
      executionControls(workflowExecution({state: 'needs_recovery', allowed_actions: []})).primary,
    ).toBeNull()
    expect(
      executionControls(workflowExecution({state: 'needs_recovery', allowed_actions: ['pause']})).primary,
    ).toEqual({kind: 'pause', scope: 'run', label: '暂停', settling: false})
    const waiting = chatExecution({state: 'waiting_approval', allowed_actions: ['stop', 'pause']})
    expect(executionControls(waiting).primary).toEqual({kind: 'pause', scope: 'run', label: '暂停', settling: false})
    expect(executionControls(waiting).cancel.available).toBe(true)
  })

  it('routes pause/resume by execution ownership', () => {
    const running = workflowExecution()
    expect(executionControlTarget(running, 'pause')).toEqual({kind: 'workflow', workflowRunId: 'wfr_01J9PARQ1'})
    expect(executionControlTarget(running, 'resume')).toBeNull()
    expect(
      executionControlTarget(planningExecution(), 'pause'),
    ).toEqual({kind: 'planning', operationId: 'plop_01J9PARQ1'})
    const pausedChat = chatExecution({state: 'paused', allowed_actions: ['resume']})
    expect(executionControlTarget(pausedChat, 'resume')).toEqual({kind: 'chat'})
    // The action must come from the projection.
    expect(executionControlTarget(chatExecution(), 'pause')).toBeNull()
    expect(executionControlTarget(null, 'pause')).toBeNull()
  })
})

describe('paused send routing (P09.2, D07)', () => {
  it('continues a paused chat queue only for pure continue text', () => {
    const pausedQueue = chatExecution({state: 'paused', allowed_actions: ['resume']})
    expect(pausedSendRoute(pausedQueue, '继续', true, isPureContinueCommand)).toBe('queue_continue')
    expect(pausedSendRoute(pausedQueue, '继续修复登录页', true, isPureContinueCommand)).toBeNull()
    expect(pausedSendRoute(pausedQueue, '继续', false, isPureContinueCommand)).toBeNull()
  })

  it('classifies paused planning/workflow text as a control regardless of workflowOn', () => {
    const pausedWorkflow = workflowExecution({state: 'paused', allowed_actions: ['resume', 'change']})
    expect(pausedSendRoute(pausedWorkflow, '继续', undefined, isPureContinueCommand)).toBe('control')
    expect(pausedSendRoute(pausedWorkflow, '把节点 2 的输出改成英文', undefined, isPureContinueCommand)).toBe('control')
    expect(pausedSendRoute(pausedWorkflow, '  ', undefined, isPureContinueCommand)).toBeNull()
    const pausedPlanning = planningExecution({state: 'paused', allowed_actions: ['resume']})
    expect(pausedSendRoute(pausedPlanning, '先不执行，保持现状', undefined, isPureContinueCommand)).toBe('control')
  })

  it('keeps ordinary routing while not paused', () => {
    expect(pausedSendRoute(workflowExecution(), '继续', true, isPureContinueCommand)).toBeNull()
    expect(pausedSendRoute(null, '继续', true, isPureContinueCommand)).toBeNull()
  })
})

describe('running pause routing (BUG-GUI-001)', () => {
  const explicitPause = (value: string) => isExplicitPauseCommand(value)

  it('routes an explicit pause word on a live planning/workflow execution to control', () => {
    expect(runningControlRoute(workflowExecution(), '暂停', explicitPause)).toBe('control')
    expect(runningControlRoute(planningExecution(), '暂停', explicitPause)).toBe('control')
    // 'pausing' covers the idempotent repeat pause while the first settles.
    expect(
      runningControlRoute(workflowExecution({state: 'pausing'}), '暂停', explicitPause),
    ).toBe('control')
    expect(
      runningControlRoute(workflowExecution({state: 'waiting_approval'}), 'hold', explicitPause),
    ).toBe('control')
  })

  it('never routes ordinary, non-pause, chat-owned or empty input', () => {
    expect(runningControlRoute(workflowExecution(), '暂停后修改计划', explicitPause)).toBeNull()
    expect(runningControlRoute(workflowExecution(), '能暂停吗', explicitPause)).toBeNull()
    expect(runningControlRoute(workflowExecution(), '继续', explicitPause)).toBeNull()
    expect(runningControlRoute(workflowExecution(), '  ', explicitPause)).toBeNull()
    expect(runningControlRoute(chatExecution(), '暂停', explicitPause)).toBeNull()
    expect(runningControlRoute(workflowExecution({state: 'paused'}), '暂停', explicitPause)).toBeNull()
    expect(runningControlRoute(workflowExecution({state: 'idle'}), '暂停', explicitPause)).toBeNull()
    expect(runningControlRoute(null, '暂停', explicitPause)).toBeNull()
  })
})

describe('frozen wire request builders (contract §7)', () => {
  it('builds GuiControlRequest with the exact frozen fields', () => {
    const body = guiControlRequestBody({
      commandId: 'cmd_ctrl_01J9PARQ1',
      sessionId: 'ses_01J9ROOT0',
      text: '继续',
      clientMessageId: 'cmid_01J9PARQ1',
    })
    expect(Object.keys(body).sort()).toEqual(['client_message_id', 'command_id', 'session_id', 'text'])
    expect(body).toEqual({
      command_id: 'cmd_ctrl_01J9PARQ1',
      session_id: 'ses_01J9ROOT0',
      text: '继续',
      client_message_id: 'cmid_01J9PARQ1',
    })
    expect(guiControlRequestBody({commandId: 'c', sessionId: 's', text: 't'}).client_message_id).toBeNull()
  })

  it('builds GuiPauseRequest with the exact frozen fields', () => {
    const body = guiPauseRequestBody({commandId: 'cmd_pause_01J9PARQ1', sessionId: 'ses_01J9ROOT0', expectedRunRowVersion: 11})
    expect(Object.keys(body).sort()).toEqual(['command_id', 'expected_run_row_version', 'session_id'])
    expect(body).toEqual({
      command_id: 'cmd_pause_01J9PARQ1',
      session_id: 'ses_01J9ROOT0',
      expected_run_row_version: 11,
    })
    expect(guiPauseRequestBody({commandId: 'c', sessionId: 's'}).expected_run_row_version).toBeNull()
  })

  it('keeps the chat pause path session-scoped and explicit', () => {
    expect(chatPausePath('ws_1', 'ses_1')).toBe('/v1/workspaces/ws_1/sessions/ses_1/pause')
  })
})

describe('per-node execution projection derivation (BUG-GUI-002, P02)', () => {
  const paused = {
    state: 'paused' as const,
    segment_id: 'seg_1',
    control_generation: 1,
    reason: 'user_interrupt',
    settled_at: '2026-09-10T10:00:00Z',
  }

  it('labels paused/pausing/needs_recovery and hides plain running', () => {
    expect(nodeExecutionBadge(paused)).toBe('已暂停')
    expect(nodeExecutionBadge({ ...paused, state: 'pausing', settled_at: null })).toBe('正在暂停')
    expect(nodeExecutionBadge({ ...paused, state: 'needs_recovery' })).toBe('待恢复')
    expect(nodeExecutionBadge({ ...paused, state: 'running' })).toBeNull()
  })

  it('never fabricates a pause when the server projects nothing', () => {
    expect(nodeExecutionBadge(undefined)).toBeNull()
    expect(nodeExecutionBadge(null)).toBeNull()
    expect(nodeStatusText('运行中', undefined)).toBe('运行中')
    expect(nodeStatusText('运行中', null)).toBe('运行中')
  })

  it('pairs business and execution labels for the dual display', () => {
    expect(nodeStatusText('运行中', paused)).toBe('运行中 · 已暂停')
    expect(nodeStatusText('运行中', { ...paused, state: 'pausing' })).toBe('运行中 · 正在暂停')
    expect(nodeStatusText('已完成', null)).toBe('已完成')
  })

  it('freezes region clocks while pausing, paused or recovering — never while stopping', () => {
    expect(executionTimingFrozen(execution({ state: 'pausing' }))).toBe(true)
    expect(executionTimingFrozen(execution({ state: 'paused' }))).toBe(true)
    expect(executionTimingFrozen(execution({ state: 'needs_recovery' }))).toBe(true)
    expect(executionTimingFrozen(execution({ state: 'stopping' }))).toBe(false)
    expect(executionTimingFrozen(execution({ state: 'running' }))).toBe(false)
    expect(executionTimingFrozen(null)).toBe(false)
    expect(executionTimingFrozen(undefined)).toBe(false)
  })
})

describe('generation-scoped planning controls (B coordination, A09)', () => {
  it('derives 暂停生成/继续生成 from the generation action literals', () => {
    const generating = planningExecution({allowed_actions: ['stop', 'pause_generation']})
    expect(executionControls(generating).primary).toEqual({
      kind: 'pause', scope: 'generation', label: '暂停生成', settling: false,
    })
    const pausedGeneration = planningExecution({state: 'paused', allowed_actions: ['resume_generation']})
    expect(executionControls(pausedGeneration).primary).toEqual({
      kind: 'resume', scope: 'generation', label: '继续生成', settling: false,
    })
  })

  it('routes generation presses to the planning_generation target, never the run endpoints', () => {
    const generating = planningExecution({allowed_actions: ['stop', 'pause_generation']})
    expect(executionControlTarget(generating, 'pause')).toEqual({
      kind: 'planning_generation', operationId: 'plop_01J9PARQ1',
    })
    const pausedGeneration = planningExecution({state: 'paused', allowed_actions: ['resume_generation']})
    expect(executionControlTarget(pausedGeneration, 'resume')).toEqual({
      kind: 'planning_generation', operationId: 'plop_01J9PARQ1',
    })
    // Run-scoped presses stay run-scoped and never absorb generation literals.
    expect(executionControlTarget(planningExecution({allowed_actions: ['stop', 'pause']}), 'pause')).toEqual({
      kind: 'planning', operationId: 'plop_01J9PARQ1',
    })
    expect(executionControlTarget(generating, 'resume')).toBeNull()
  })
})
