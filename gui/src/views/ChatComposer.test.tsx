import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ChatComposer, charHintRemaining, commandMenuVisible, insertFileReference, resolveCommand, shouldSend, type ChatCommand } from './ChatComposer'
import {
  agentSelectionLabel,
  blockerText,
  canStartPlan,
  controlReceiptLabel,
  isPureContinueCommand,
  latestOperation,
  nodeParents,
  planningDiagnosticText,
  planningProgressText,
  planningRequestKey,
  runIsTerminal,
  taskPlanPhase,
  taskPlanStatusHint,
  workflowTarget,
} from './lib/taskPlan'
import type { TaskPlanControlState, TaskPlanControlWire, TaskPlanViewWire } from '../api/types'

const commands: ChatCommand[] = [
  { name: '/workflow', label: '生成当前任务计划' },
  { name: '/plan', label: '别名' },
  { name: '/accept', label: '接受', disabled: true },
]

describe('Chat command dispatch', () => {
  it('dispatches the first token and keeps the argument text intact', () => {
    expect(resolveCommand('/workflow 重构 API', commands)).toEqual({ command: commands[0], fullText: '/workflow 重构 API' })
    expect(resolveCommand('/workflow', commands)).toEqual({ command: commands[0], fullText: '/workflow' })
    expect(resolveCommand('/plan 实现计算器', commands)).toEqual({ command: commands[1], fullText: '/plan 实现计算器' })
    expect(resolveCommand('/workflow    多行\n继续', commands)?.fullText).toBe('/workflow    多行\n继续')
  })

  it('never triggers inside quotes, code blocks, paths or non-command text', () => {
    expect(resolveCommand('请在 "/workflow" 模式下继续', commands)).toBeNull()
    expect(resolveCommand('```bash\n/workflow\n```', commands)).toBeNull()
    expect(resolveCommand('/workflow/foo/bar 是路径', commands)).toBeNull()
    expect(resolveCommand('看看 /workflow 命令', commands)).toBeNull()
    expect(resolveCommand(' /workflow 缩进不算命令', commands)).toBeNull()
  })

  it('keeps disabled commands out of dispatch and IME composition unsent', () => {
    expect(resolveCommand('/accept', commands)).toBeNull()
    const event = { key: 'Enter', shiftKey: false, isComposing: false }
    expect(shouldSend(event, false)).toBe(true)
    expect(shouldSend({ ...event, isComposing: true }, false)).toBe(false)
    expect(shouldSend({ ...event, keyCode: 229 }, false)).toBe(false)
  })
})

describe('command completion menu visibility', () => {
  it('opens manually or on a leading slash and an outside click keeps it closed', () => {
    expect(commandMenuVisible(false, false, '描述任务')).toBe(false)
    expect(commandMenuVisible(true, false, '描述任务')).toBe(true)
    expect(commandMenuVisible(false, false, '/wor')).toBe(true)
    // Dismissal suppresses both the manual and the slash-driven menu.
    expect(commandMenuVisible(false, true, '/wor')).toBe(false)
    expect(commandMenuVisible(true, true, '/wor')).toBe(false)
    expect(commandMenuVisible(true, false, '')).toBe(true)
  })
})

describe('composer input helpers', () => {
  it('inserts the @ reference trigger with a word boundary and never duplicates it', () => {
    expect(insertFileReference('')).toBe('@')
    expect(insertFileReference('帮我看看')).toBe('帮我看看 @')
    expect(insertFileReference('结尾有空格 ')).toBe('结尾有空格 @')
    expect(insertFileReference('已经在引用 @src')).toBe('已经在引用 @src')
  })

  it('shows the character budget only near the limit', () => {
    expect(charHintRemaining(4096, 0)).toBeNull()
    expect(charHintRemaining(4096, 3000)).toBeNull()
    expect(charHintRemaining(4096, 3700)).toBe(396)
    expect(charHintRemaining(4096, 4096)).toBe(0)
    expect(charHintRemaining(4096, 4200)).toBe(0)
    expect(charHintRemaining(0, 100)).toBeNull()
  })
})

describe('workflow target parsing', () => {
  it('keeps the parameter text and the bare command keeps existing input', () => {
    expect(workflowTarget('/workflow 重构持久化模块')).toBe('重构持久化模块')
    expect(workflowTarget('/plan 调研恢复语义')).toBe('调研恢复语义')
    expect(workflowTarget('/workflow')).toBe('')
    expect(workflowTarget('/plan ')).toBe('')
  })
})

// ---------------------------------------------------------------------------
// P09.1 mount-level rendering (P09.4 labels). The suite runs in the node
// environment, so the composer is mounted with react-dom/server: hooks and
// the full markup render; browser-only effects stay unexercised here.
// ---------------------------------------------------------------------------

describe('ChatComposer primary control rendering', () => {
  const baseCapabilities = {
    interaction_protocol_version: 1,
    workspace_id: 'ws_1',
    features: {},
    limits: {text_chars: 8000},
    execution_ready: true,
  }
  const baseProps = {
    text: '',
    onText: () => {},
    onSend: () => {},
    active: false,
    pending: false,
    ready: true,
    capabilities: baseCapabilities as never,
    commands: [],
    onCommand: () => {},
    onStop: () => {},
  }

  it('renders the projected pause control with an accessible label', () => {
    const html = renderToStaticMarkup(
      <ChatComposer {...baseProps}
        running
        primaryControl={{kind: 'pause', label: '暂停', settling: false, disabledReason: null, onTrigger: () => {}}}/>,
    )
    expect(html).toContain('composer-pause')
    expect(html).toContain('aria-label="暂停"')
    // Cancel stays an independent secondary control (D01).
    expect(html).toContain('composer-stop')
    expect(html).toContain('aria-label="停止运行"')
  })

  it('disables the settling pause and names the pending transition', () => {
    const html = renderToStaticMarkup(
      <ChatComposer {...baseProps}
        running
        stopping
        primaryControl={{kind: 'pause', label: '正在暂停…', settling: true, disabledReason: null, onTrigger: () => {}}}/>,
    )
    expect(html).toContain('disabled')
    expect(html).toContain('正在暂停…')
    expect(html).toContain('aria-label="正在停止…"')
  })

  it('disables with the honest reason while the pause transport is unwired', () => {
    const html = renderToStaticMarkup(
      <ChatComposer {...baseProps}
        primaryControl={{kind: 'pause', label: '暂停', settling: false, disabledReason: '会话暂停通道尚未接线；暂停请求需要服务端暂停端点。', onTrigger: () => {}}}/>,
    )
    expect(html).toContain('disabled')
    expect(html).toContain('会话暂停通道尚未接线')
  })

  it('renders continue with its own label while paused', () => {
    const html = renderToStaticMarkup(
      <ChatComposer {...baseProps}
        primaryControl={{kind: 'resume', label: '继续', settling: false, disabledReason: null, onTrigger: () => {}}}/>,
    )
    expect(html).toContain('aria-label="继续"')
    expect(html).not.toContain('composer-stop')
  })

  it('renders no primary control without one', () => {
    const html = renderToStaticMarkup(<ChatComposer {...baseProps} running/>)
    expect(html).not.toContain('composer-pause')
    expect(html).toContain('composer-stop')
  })
})

const draftBase = {
  stale_reasons: [],
}

function currentControl(state: TaskPlanControlState = 'none'): TaskPlanControlWire {
  return {
    state,
    allowed_intents: [],
    hint: '',
    resume_available: false,
    cancel_available: false,
    start_available: false,
    repair_available: false,
    target: {
      workflow_run_id: null,
      run_status: null,
      run_row_version: null,
      pause_requested: false,
      planning_operation_id: null,
      binding_id: null,
      binding_mode: null,
      draft_id: null,
      draft_version: null,
      execution_digest: null,
    },
    allowed_actions: [],
  }
}

function view(overrides: Partial<TaskPlanViewWire> = {}): TaskPlanViewWire {
  return {
    binding: null,
    draft: null,
    version: null,
    operations: [],
    execution: { allowed: false, digest: null, draft_id: null, draft_version: null, blockers: ['plan_missing'], frozen_selections: [] },
    run: null,
    candidate: null,
    ownership: null,
    plan: null,
    allowed_actions: [],
    control: currentControl(),
    ...overrides,
  }
}

describe('chat queue continuation routing', () => {
  it('accepts only pure continue words', () => {
    for (const text of ['继续', 'continue', '继续执行', '接着吧', '  Resume ']) {
      expect(isPureContinueCommand(text)).toBe(true)
    }
    for (const text of ['不要继续', '继续但修改第二步', '继续吗？', '文档里写 continue', '如果完成再继续']) {
      expect(isPureContinueCommand(text)).toBe(false)
    }
  })
})

describe('control receipt labels', () => {
  it('names the server acceptance outcome without guessing', () => {
    expect(controlReceiptLabel({status: 'accepted'})).toContain('已接纳')
    expect(controlReceiptLabel({status: 'executed'})).toBe('已执行')
    expect(controlReceiptLabel({status: 'acknowledged', message: '当前运行正在执行，无需恢复。'})).toContain('无需恢复')
    expect(controlReceiptLabel({status: 'needs_choice', message: '修复计划已就绪'})).toContain('修复计划已就绪')
    expect(controlReceiptLabel({status: 'unresolved', message: '没有可恢复的运行'})).toContain('没有可恢复的运行')
    expect(controlReceiptLabel({status: 'failed', error_code: 'stale'})).toBe('处理失败 · stale')
  })
})

describe('task-plan phases', () => {
  it('treats a missing binding as idle', () => {
    expect(taskPlanPhase(null)).toBe('idle')
    expect(taskPlanPhase(view())).toBe('idle')
  })

  it('shows generating while an operation is queued or running', () => {
    const value = view({
      binding: { planning_binding_id: 'wplan_a', workspace_id: 'w', session_id: 's', origin_interaction_id: 'm', mode: 'initial', parent_run_id: null, parent_revision_id: null, current_draft_id: null, context_ref: { conversation_position: 0, attachments: [], constraints: [], settings_revision: 0, model: { provider_id: 'p', model_id: 'm' }, generation: {}, settings_digest: 'a'.repeat(64) }, status: 'active', row_version: 1, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
      operations: [{ planning_operation_id: 'wop_1', workspace_id: 'w', session_id: 's', planning_binding_id: 'wplan_a', operation: 'generate', command_id: 'cmd_1', base_draft_id: null, base_draft_version: 0, status: 'running', result_draft_id: null, result_draft_version: null, error_code: null, diagnostics: [], cancelled_at: null, cancel_reason: null, row_version: 1, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }],
      control: currentControl('generating'),
    })
    expect(taskPlanPhase(value)).toBe('generating')
    expect(latestOperation(value)?.planning_operation_id).toBe('wop_1')
    expect(planningProgressText(value.operations[0]!)).toContain('1/3')
    expect(taskPlanStatusHint('generating', value, value.operations[0]!)).toContain('1/3')
    expect(
      planningProgressText({
        ...value.operations[0]!,
        usage: {
          attempts: 2,
          total_tokens: null,
          cost_amount_minor: null,
          cost_currency: null,
          availability: 'unavailable',
        },
      }),
    ).toContain('2/3')
  })

  it('uses the server control projection for every phase', () => {
    const binding = { planning_binding_id: 'wplan_a', workspace_id: 'w', session_id: 's', origin_interaction_id: 'm', mode: 'repair' as const, parent_run_id: 'wrun_old', parent_revision_id: 'wrev_old', current_draft_id: 'wdraft_1', context_ref: { conversation_position: 0, attachments: [], constraints: [], settings_revision: 0, model: { provider_id: 'p', model_id: 'm' }, generation: {}, settings_digest: 'a'.repeat(64) }, status: 'active' as const, row_version: 1, created_at: '', updated_at: '' }
    const control = (state: NonNullable<TaskPlanViewWire['control']>['state']) => ({
      state,
      allowed_intents: [],
      hint: '',
      resume_available: false,
      cancel_available: false,
      start_available: false,
      repair_available: false,
      target: { workflow_run_id: 'wrun_old', run_status: 'cancelled' as const, run_row_version: 4, pause_requested: false, planning_operation_id: null, binding_id: 'wplan_a', binding_mode: 'repair', draft_id: 'wdraft_1', draft_version: 2, execution_digest: 'd'.repeat(64) },
      allowed_actions: [],
    })
    // A terminal run must not shadow an executable repair draft.
    expect(taskPlanPhase(view({
      binding,
      control: control('repair_ready'),
      execution: { allowed: true, digest: 'd'.repeat(64), draft_id: 'wdraft_1', draft_version: 2, blockers: [], frozen_selections: [] },
    }))).toBe('ready')
    expect(taskPlanPhase(view({ binding, control: control('terminal') }))).toBe('completed')
    expect(taskPlanPhase(view({ binding, control: control('draining') }))).toBe('pausing')
    expect(taskPlanPhase(view({ binding, control: control('paused') }))).toBe('paused')
    expect(taskPlanPhase(view({ binding, control: control('generating') }))).toBe('generating')
    expect(taskPlanPhase(view({ binding: null, control: control('none') }))).toBe('idle')
  })

  it('surfaces a terminal failed generation as an error phase, never generating', () => {
    const binding = { planning_binding_id: 'wplan_a', workspace_id: 'w', session_id: 's', origin_interaction_id: 'm', mode: 'initial' as const, parent_run_id: null, parent_revision_id: null, current_draft_id: null, context_ref: { conversation_position: 0, attachments: [], constraints: [], settings_revision: 0, model: { provider_id: 'p', model_id: 'm' }, generation: {}, settings_digest: 'a'.repeat(64) }, status: 'active' as const, row_version: 1, created_at: '', updated_at: '' }
    const failedOperation: TaskPlanViewWire['operations'][number] = {
      planning_operation_id: 'wop_1', workspace_id: 'w', session_id: 's', planning_binding_id: 'wplan_a', operation: 'generate', command_id: 'cmd_1', base_draft_id: null, base_draft_version: 0, status: 'failed', result_draft_id: null, result_draft_version: null, error_code: 'unavailable', diagnostics: ['planning_provider_unavailable:auth'], cancelled_at: null, cancel_reason: null, row_version: 2, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    }
    const value = view({ binding, operations: [failedOperation], control: currentControl('generate_failed') })
    expect(taskPlanPhase(value)).toBe('error')
    expect(taskPlanStatusHint('error', value, failedOperation)).toBe('')
    expect(planningDiagnosticText(failedOperation.diagnostics)).toContain('凭据被拒绝')
    expect(planningDiagnosticText(['planning_provider_unavailable'])).toBe(
      '规划模型暂时不可用。请稍后重试。',
    )
    // A cancelled generation without diagnostics still names the retry path.
    expect(planningDiagnosticText([])).toBe('')
  })

  it('omits idle instructions and keeps actionable planning failures', () => {
    expect(taskPlanStatusHint('idle', view(), null)).toBe('')
    expect(planningDiagnosticText(['invalid_plan: return a valid bounded graph'])).toContain(
      '计划校验失败',
    )
    expect(planningDiagnosticText(['planning_provider_unavailable'])).toContain('暂时不可用')
  })

  it('distinguishes invalid, needs_input, ready, running and completed', () => {
    const source = { workflow_definition_id: 'task_x', name: '目标', description: '', tags: [], origin: 'user' as const, input_contract: { kind: 'TaskContract', version: 1 } as const, required_outputs: [], edges: [{ from_node_id: 'work', to_node_id: 'audit' }], default_budget: { max_agent_generation_requests: null, default_node_max_agent_generation_requests: null, admission_timeout_seconds: null, max_concurrency: 1 }, nodes: [] }
    const draft = { draft: { draft_id: 'wdraft_1', workspace_id: 'w', source, source_hash: 'a', base_workflow_revision_id: null, base_head_row_version: 0, base_source_revision: 0, base_definition_source_hash: null, status: 'valid' as const, diagnostics: [], frozen_workflow_revision_id: null, row_version: 1, created_at: '', updated_at: '' }, ...draftBase }
    expect(taskPlanPhase(view({ binding: { planning_binding_id: 'wplan_a', workspace_id: 'w', session_id: 's', origin_interaction_id: 'm', mode: 'initial', parent_run_id: null, parent_revision_id: null, current_draft_id: 'wdraft_1', context_ref: { conversation_position: 0, attachments: [], constraints: [], settings_revision: 0, model: { provider_id: 'p', model_id: 'm' }, generation: {}, settings_digest: 'a'.repeat(64) }, status: 'active', row_version: 1, created_at: '', updated_at: '' }, draft, execution: { allowed: false, digest: null, draft_id: 'wdraft_1', draft_version: 1, blockers: ['invalid_draft'], frozen_selections: [] }, control: currentControl('draft') }))).toBe('needs_input')
    expect(taskPlanPhase(view({ binding: null, draft: { ...draft, draft: { ...draft.draft, status: 'invalid' } } }))).toBe('idle')
    const bound = view({ binding: { planning_binding_id: 'wplan_a', workspace_id: 'w', session_id: 's', origin_interaction_id: 'm', mode: 'initial', parent_run_id: null, parent_revision_id: null, current_draft_id: 'wdraft_1', context_ref: { conversation_position: 0, attachments: [], constraints: [], settings_revision: 0, model: { provider_id: 'p', model_id: 'm' }, generation: {}, settings_digest: 'a'.repeat(64) }, status: 'active', row_version: 1, created_at: '', updated_at: '' }, draft, execution: { allowed: true, digest: 'd'.repeat(64), draft_id: 'wdraft_1', draft_version: 1, blockers: [], frozen_selections: [] }, control: currentControl('draft') })
    expect(taskPlanPhase(bound)).toBe('ready')
    expect(taskPlanPhase({ ...bound, control: currentControl('running'), run: { workflow_run_id: 'wrun_1', status: 'running', result_status: null, pause_requested: false, lineage_root_run_id: null, origin: 'initial', active_node_ids: ['work'], nodes: [] } })).toBe('running')
    expect(taskPlanPhase({ ...bound, control: currentControl('draining'), run: { workflow_run_id: 'wrun_1', status: 'draining', result_status: null, pause_requested: true, lineage_root_run_id: null, origin: 'initial', active_node_ids: ['work'], nodes: [{ node_id: 'work', status: 'running' }] } })).toBe('pausing')
    expect(taskPlanPhase({ ...bound, control: currentControl('paused'), run: { workflow_run_id: 'wrun_1', status: 'paused', result_status: null, pause_requested: true, lineage_root_run_id: null, origin: 'initial', active_node_ids: [], nodes: [] } })).toBe('paused')
    expect(taskPlanPhase({ ...bound, control: currentControl('terminal'), run: { workflow_run_id: 'wrun_1', status: 'completed', result_status: 'succeeded', lineage_root_run_id: null, origin: 'initial', active_node_ids: [], nodes: [] } })).toBe('completed')
    expect(taskPlanPhase({ ...bound, binding: { ...bound.binding!, mode: 'repair', parent_run_id: 'wrun_1', parent_revision_id: 'wrev_1' }, control: currentControl('repair_ready'), run: { workflow_run_id: 'wrun_1', status: 'cancelled', result_status: null, lineage_root_run_id: null, origin: 'initial', active_node_ids: [], nodes: [] } })).toBe('ready')
    expect(nodeParents(source, 'audit')).toEqual(['work'])
  })

  it('keeps run terminality and blocker text human readable', () => {
    expect(runIsTerminal('completed')).toBe(true)
    expect(runIsTerminal('draining')).toBe(false)
    expect(blockerText(['invalid_draft', 'execution_selection_incompatible'])).toContain('完整校验')
    expect(blockerText(['execution_selection_incompatible'])).toContain('不兼容')
    expect(agentSelectionLabel('preset:review')).toContain('Review')
    expect(agentSelectionLabel('custom:helper')).toContain('helper')
    expect(planningRequestKey('generate', '目标', 0)).toBe(planningRequestKey('generate', '目标', 0))
    expect(planningRequestKey('generate', '目标', 0)).not.toBe(planningRequestKey('revise', '目标', 1))
  })

  it('only starts from a ready, saved, connected plan', () => {
    const base = { phase: 'ready' as const, busy: false, unsaved: false, connected: true }
    expect(canStartPlan(base)).toBe(true)
    expect(canStartPlan({ ...base, phase: 'needs_input' })).toBe(false)
    expect(canStartPlan({ ...base, busy: true })).toBe(false)
    expect(canStartPlan({ ...base, unsaved: true })).toBe(false)
    expect(canStartPlan({ ...base, connected: false })).toBe(false)
  })
})
