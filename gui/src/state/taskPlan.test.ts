import { beforeEach, describe, expect, it } from 'vitest'
import { ApiClient } from '../api/client'
import type { TaskPlanControlWire, TaskPlanViewWire } from '../api/types'
import { TaskPlanStore } from '../state/taskPlan'

interface Call { url: string; method: string | undefined; body: unknown }
const responses = new Map<string, unknown>()

beforeEach(() => { responses.clear() })

const currentControl: TaskPlanControlWire = {
  state: 'none',
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

function client(): { api: ApiClient; calls: Call[] } {
  const calls: Call[] = []
  const api = new ApiClient({
    baseUrl: '', token: 'test',
    fetchImpl: async (url, init) => {
      const plain = String(url).replace(/\?.*$/, '')
      calls.push({ url: String(url), method: init?.method, body: init?.body === undefined ? null : JSON.parse(String(init?.body)) })
      const key = `${init?.method ?? 'GET'} ${plain}`
      for (const [pattern, value] of responses) {
        // Patterns are full method+path prefixes; the more specific route is
        // inserted first, so `/task-plan/nodes` wins over `/task-plan`.
        if (!key.startsWith(pattern)) continue
        if (value === 'network') throw new TypeError('simulated network failure')
        if (value === 'conflict409') return new Response(JSON.stringify({ error: { code: 'stale', message: 'Plan version changed' } }), { status: 409 })
        return new Response(JSON.stringify(value), { status: 200 })
      }
      return new Response(JSON.stringify({ error: { code: 'not_found', message: 'missing stub' } }), { status: 404 })
    },

  })
  return { api, calls }
}

function operationView(status: import('../api/types').PlanningOperationStatus, draftStatus: 'valid' | 'invalid' | null = null): TaskPlanViewWire {
  const draft = draftStatus === null ? null : {
    draft: { draft_id: 'wdraft_1', workspace_id: 'w', source: { workflow_definition_id: 'task_x', name: '目标', description: '', tags: [], origin: 'user' as const, input_contract: { kind: 'TaskContract' as const, version: 1 as const }, required_outputs: [], edges: [], default_budget: { max_agent_generation_requests: null, default_node_max_agent_generation_requests: null, admission_timeout_seconds: null, max_concurrency: 1 }, nodes: [] }, source_hash: 'a'.repeat(64), base_workflow_revision_id: null, base_head_row_version: 0, base_source_revision: 0, base_definition_source_hash: null, status: draftStatus as import('../api/types').WorkflowDraftWire['status'], diagnostics: [], frozen_workflow_revision_id: null, row_version: 2, created_at: '', updated_at: '' },
    stale_reasons: [],
  }
  const version = draftStatus === null ? null : {
    draft_id: 'wdraft_1', version: 2,
    node_metadata: { work: { title: '工作', responsibility: 'implementation' as const, agent_selection: 'preset:general', model_choice: null, generation_choice: null, x: null, y: null } },
    execution_selections: { work: { model: { provider_id: 'p', model_id: 'm' }, generation: null, model_source: 'session', generation_source: 'session', agent_ref: { definition_id: 'builtin_general', version_id: 'adev_1', content_hash: 'a'.repeat(64) } } },
    created_from: 'llm_generate' as const, command_id: 'cmd_old', summary: '', validation_digest: 'b'.repeat(64), created_at: '',
  }
  return {
    binding: { planning_binding_id: 'wplan_a', workspace_id: 'w', session_id: 's', origin_interaction_id: 'm', mode: 'initial' as const, parent_run_id: null, parent_revision_id: null, current_draft_id: draft === null ? null : 'wdraft_1', context_ref: { conversation_position: 0, attachments: [], constraints: [], settings_revision: 0, model: { provider_id: 'p', model_id: 'm' }, generation: {}, settings_digest: 'a'.repeat(64) }, status: 'active', row_version: 1, created_at: '', updated_at: '' },
    draft,
    version,
    operations: [{ planning_operation_id: 'wop_1', workspace_id: 'w', session_id: 's', planning_binding_id: 'wplan_a', operation: 'generate', command_id: 'cmd_old', base_draft_id: null, base_draft_version: 0, status, result_draft_id: null, result_draft_version: null, error_code: null, diagnostics: [], cancelled_at: null, cancel_reason: null, row_version: 1, created_at: '', updated_at: '' }],
    execution: { allowed: false, digest: null, draft_id: null, draft_version: null, blockers: [], frozen_selections: [] },
    run: null,
    candidate: null,
    ownership: null,
    plan: null,
    allowed_actions: [],
    control: currentControl,
  }
}

describe('TaskPlanStore submission identity', () => {
  it('generates with the objective and replays the same command after a lost response', async () => {
    // No draft yet: both submissions are `generate` and share one retry identity.
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', operationView('succeeded'))
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan', 'network')
    const { api, calls } = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.submit('重构模块')
    const first = calls.find((call) => call.method === 'POST' && call.url.includes('/task-plan'))!
    expect(first.body).toMatchObject({ task: { objective: '重构模块' }, operation: 'generate' })
    expect(String((first.body as Record<string, unknown>).command_id)).toMatch(/^cmd_task_plan_generate_/)
    expect(store.getState().error).toContain('simulated network failure')

    // The submission outcome is unknown, so the retry reuses the same ID and
    // Core replays the receipt instead of duplicating the operation.
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan', operationView('succeeded'))
    await store.submit('重构模块')
    const posts = calls.filter((call) => call.method === 'POST' && call.url.includes('/task-plan') && !call.url.includes('/operations'))
    expect(String((posts[1]!.body as Record<string, unknown>).command_id)).toBe(String((posts[0]!.body as Record<string, unknown>).command_id))
  })

  it('sends a revise against the current binding after a draft exists', async () => {
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', operationView('succeeded', 'valid'))
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan', operationView('running'))
    const { api, calls } = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.refresh()
    await store.submit('改成三个节点')
    const revise = calls.find((call) => call.method === 'POST' && call.url.includes('/task-plan') && !call.url.includes('/operations'))!
    expect(revise.body).toMatchObject({ operation: 'revise', planning_binding_id: 'wplan_a', base_draft_version: 2 })
  })

  it('blocks a second submission while one is generating and cancels only planning', async () => {
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', operationView('running'))
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/operations/wop_1', operationView('cancelled'))
    const { api, calls } = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.refresh()
    expect(store.generating).toBe(true)
    await store.submit('另一个目标')
    expect(store.getState().error).toContain('已有生成正在进行')
    await store.cancelGeneration()
    expect(calls.some((call) => call.method === 'POST' && call.url.includes('/task-plan/operations/wop_1'))).toBe(true)
    expect(store.getState().notice).toContain('取消')
  })
})

describe('TaskPlanStore node editing', () => {
  it('posts a typed node edit against the current version and records undo', async () => {
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', operationView('succeeded', 'valid'))
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/nodes', {
      planning_operation_id: 'wop_edit', workspace_id: 'w', session_id: 's', planning_binding_id: 'wplan_a',
      operation: 'validate', command_id: 'cmd_edit', base_draft_id: 'wdraft_1', base_draft_version: 2,
      status: 'succeeded', result_draft_id: 'wdraft_1', result_draft_version: 3, error_code: null,
      diagnostics: [], cancelled_at: null, cancel_reason: null, row_version: 1, created_at: '', updated_at: '',
    })
    const { api, calls } = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.refresh()
    await store.editNode('replace', { nodeId: 'work', node: { node_id: 'work', title: '工作', task: '执行工作', agent: 'preset:general', responsibility: 'implementation', depends_on: [], completion: ['完成'] } })
    const edit = calls.find((call) => call.method === 'POST' && call.url.includes('/task-plan/nodes'))!
    expect(edit.body).toMatchObject({ binding_id: 'wplan_a', expected_version: 2, action: 'replace' })
    expect(store.undoVersion).toBe(2)
  })

  it('surfaces a version conflict, refreshes, and offers no undo for failed edits', async () => {
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', operationView('succeeded', 'valid'))
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/nodes', 'conflict409')
    const { api, calls } = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.refresh()
    await store.editNode('remove', { nodeId: 'work' })
    expect(store.getState().error).toContain('请重试')
    expect(store.undoVersion).toBeNull()
    const views = calls.filter((call) => call.method === 'GET' && call.url.includes('/task-plan') && !call.url.includes('events'))
    expect(views.length).toBeGreaterThanOrEqual(2)
  })

  it('edits the final delivery through the full-source endpoint and undoes via restore', async () => {
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', operationView('succeeded', 'valid'))
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/edit', {
      planning_operation_id: 'wop_del', workspace_id: 'w', session_id: 's', planning_binding_id: 'wplan_a',
      operation: 'validate', command_id: 'cmd_del', base_draft_id: 'wdraft_1', base_draft_version: 2,
      status: 'succeeded', result_draft_id: 'wdraft_1', result_draft_version: 3, error_code: null,
      diagnostics: [], cancelled_at: null, cancel_reason: null, row_version: 1, created_at: '', updated_at: '',
    })
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/nodes', {
      planning_operation_id: 'wop_undo', workspace_id: 'w', session_id: 's', planning_binding_id: 'wplan_a',
      operation: 'validate', command_id: 'cmd_undo', base_draft_id: 'wdraft_1', base_draft_version: 3,
      status: 'succeeded', result_draft_id: 'wdraft_1', result_draft_version: 4, error_code: null,
      diagnostics: [], cancelled_at: null, cancel_reason: null, row_version: 1, created_at: '', updated_at: '',
    })
    const { api, calls } = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.refresh()
    await store.editDeliveries([{ node_id: 'work', output_slot: 'result' }])
    const delivery = calls.find((call) => call.method === 'POST' && call.url.includes('/task-plan/edit'))!
    expect((delivery.body as { source: { required_outputs: unknown[] } }).source.required_outputs).toEqual([{ node_id: 'work', output_slot: 'result' }])
    expect(store.undoVersion).toBe(2)
    await store.undoEdit()
    const restore = calls.filter((call) => call.method === 'POST' && call.url.includes('/task-plan/nodes')).at(-1)!
    expect(restore.body).toMatchObject({ action: 'restore', restore_version: 2, expected_version: 2 })
  })
})

describe('TaskPlanStore explicit start and control', () => {
  function readyView() {
    const value = operationView('succeeded', 'valid')
    value.execution = {
      allowed: true, digest: 'd'.repeat(64), draft_id: 'wdraft_1', draft_version: 2, blockers: [],
      frozen_selections: [],
    }
    return value
  }

  it('starts with the exact draft version and digest, replaying one command on retry', async () => {
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', readyView())
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/start', 'network')
    const { api, calls } = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.refresh()
    await store.startExecution()
    expect(store.getState().error).toContain('network failure')
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/start', {
      run: { workflow_run_id: 'wrun_1', status: 'running', result_status: null, lineage_root_run_id: null, origin: 'initial', active_node_ids: [], nodes: [] },
      receipt: {}, replayed: false,
    })
    await store.startExecution()
    const starts = calls.filter((call) => call.method === 'POST' && call.url.includes('/task-plan/start'))
    expect(starts).toHaveLength(2)
    expect(String((starts[0]!.body as Record<string, unknown>).command_id)).toBe(String((starts[1]!.body as Record<string, unknown>).command_id))
    expect(starts[0]!.body).toMatchObject({ draft_id: 'wdraft_1', draft_version: 2, execution_digest: 'd'.repeat(64), action_source: 'button' })
  })

  it('routes composer text through state-aware control and surfaces steer', async () => {
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', readyView())
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/control', { disposition: 'answered', intent: 'clarify', message: '当前计划有两个节点。' })
    const { api, calls } = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.refresh()
    const outcome = await store.control('这个计划是做什么的？')
    expect(outcome).toMatchObject({ kind: 'handled', intent: 'clarify' })
    expect(store.getState().notice).toContain('两个节点')
    const control = calls.find((call) => call.method === 'POST' && call.url.includes('/task-plan/control'))!
    expect(control.body).toMatchObject({ session_id: 's', text: '这个计划是做什么的？' })

    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/control', { disposition: 'steer', intent: 'steer', target_agent_run_id: 'arun_9' })
    const steer = await store.control('请把输出写成表格')
    expect(steer).toEqual({ kind: 'steer', targetAgentRunId: 'arun_9' })
  })

  it('pauses with the visible run version and replays the same command', async () => {
    const value = readyView()
    value.run = {
      workflow_run_id: 'wrun_1', status: 'running', result_status: null, pause_requested: false,
      row_version: 3, lineage_root_run_id: 'wrun_1', origin: 'initial', active_node_ids: ['work'],
      nodes: [{ node_id: 'work', status: 'running' }],
    }
    value.allowed_actions = ['pause', 'change', 'cancel']
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', value)
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/pause', 'network')
    const { api, calls } = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.refresh()
    await store.pause()
    expect(store.getState().error).toContain('network failure')
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/pause', {
      run: { ...value.run, status: 'draining', pause_requested: true },
      replayed: false, receipt: {},
    })
    await store.pause()
    const pauses = calls.filter((call) => call.method === 'POST' && call.url.includes('/task-plan/pause'))
    expect(pauses).toHaveLength(2)
    expect(pauses[0]!.body).toMatchObject({ session_id: 's', expected_run_row_version: 3 })
    expect(String((pauses[0]!.body as Record<string, unknown>).command_id)).toBe(
      String((pauses[1]!.body as Record<string, unknown>).command_id),
    )
  })

  it('prepares a change binding from the paused parent run', async () => {
    const value = readyView()
    value.run = {
      workflow_run_id: 'wrun_1', status: 'paused', result_status: null, pause_requested: true,
      row_version: 4, workflow_revision_id: 'wrev_1', lineage_root_run_id: 'wrun_1', origin: 'initial',
      active_node_ids: [], nodes: [{ node_id: 'work', status: 'completed' }],
    }
    value.allowed_actions = ['change']
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', value)
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/change', {
      run: value.run, binding: { ...value.binding, mode: 'change', parent_run_id: 'wrun_1' },
      replayed: false, receipt: {},
    })
    const { api, calls } = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.refresh()
    await store.prepareChange()
    const change = calls.find((call) => call.method === 'POST' && call.url.includes('/task-plan/change'))!
    expect(change.body).toMatchObject({
      session_id: 's', action_source: 'button', expected_run_row_version: 4,
    })
    expect(store.getState().notice).toContain('修改')
  })

  it('shows unresolved control outcomes as errors instead of falling back', async () => {
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', readyView())
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/control', { disposition: 'unresolved', intent: 'start', message: '这不是明确的开始指令（含否定、条件、疑问或引用）；请直接点击右侧“开始执行”。' })
    const { api } = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.refresh()
    const outcome = await store.control('先别开始')
    expect(outcome).toMatchObject({ kind: 'handled', intent: 'start' })
    expect(store.getState().error).toContain('明确的开始指令')
  })

  function changeView(stable: boolean) {
    const value = readyView()
    value.binding = { ...value.binding!, mode: 'change', parent_run_id: 'wrun_1', parent_revision_id: 'wrev_1' }
    value.run = {
      workflow_run_id: 'wrun_1', status: stable ? 'paused' : 'draining', result_status: null,
      pause_requested: true, row_version: 5, workflow_revision_id: 'wrev_1',
      lineage_root_run_id: 'wrun_1', origin: 'initial', active_node_ids: stable ? [] : ['audit'],
      nodes: [{ node_id: 'build', status: 'completed' }, { node_id: 'audit', status: stable ? 'queued' : 'running' }],
    }
    value.candidate = {
      parent_run_id: 'wrun_1', base_revision_id: 'wrev_1', expected_parent_row_version: 5,
      past_node_ids: ['build'], added_node_ids: [], removed_node_ids: [],
      source_hash: 'a'.repeat(64), settings_digest: 's'.repeat(64), stable,
      digest: 'c'.repeat(64),
    }
    value.allowed_actions = ['save_candidate', 'accept_change', 'reject_change', 'resume']
    return value
  }

  it('saves a candidate without treating it as a handoff', async () => {
    const value = changeView(true)
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', value)
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/apply-change', {
      run: value.run, child: null, decision: 'save_candidate', replayed: false, receipt: {},
    })
    const { api, calls } = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.refresh()
    await store.applyChange('save_candidate')
    expect(store.getState().notice).toContain('修改已保存')
    const apply = calls.find((call) => call.method === 'POST' && call.url.includes('/apply-change'))!
    expect(apply.body).toMatchObject({
      decision: 'save_candidate', candidate_digest: 'c'.repeat(64), expected_parent_row_version: 5,
    })
  })

  it('refuses accept while the parent is still draining', async () => {
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', changeView(false))
    const { api, calls } = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.refresh()
    await store.applyChange('accept_change')
    expect(store.getState().error).toContain('暂停完成后可应用修改')
    expect(calls.some((call) => call.method === 'POST' && call.url.includes('/apply-change'))).toBe(false)
  })

  it('resumes the original paused run', async () => {
    const value = changeView(true)
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', value)
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/resume', {
      run: { ...value.run, status: 'running', pause_requested: false }, replayed: false, receipt: {},
    })
    const { api, calls } = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.refresh()
    await store.resumeRun()
    expect(store.getState().notice).toContain('已继续原计划')
    expect(calls.some((call) => call.method === 'POST' && call.url.includes('/task-plan/resume'))).toBe(true)
  })

  it('prepares a repair draft from a terminal run', async () => {
    const value = readyView()
    value.run = {
      workflow_run_id: 'wrun_1', status: 'completed', result_status: 'needs_revision',
      pause_requested: false, row_version: 8, lineage_root_run_id: 'wrun_1', origin: 'initial',
      active_node_ids: [], nodes: [{ node_id: 'build', status: 'completed' }, { node_id: 'audit', status: 'completed' }],
    }
    value.allowed_actions = ['repair']
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', value)
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/repair', {
      run: value.run,
      binding: { ...value.binding!, mode: 'repair', parent_run_id: 'wrun_1' },
      replayed: false, receipt: {},
    })
    const { api, calls } = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.refresh()
    await store.prepareRepair()
    expect(store.getState().notice).toContain('修复计划已生成')
    const repair = calls.find((call) => call.method === 'POST' && call.url.includes('/task-plan/repair'))!
    expect(repair.body).toMatchObject({ session_id: 's', action_source: 'button' })
  })
})

describe('TaskPlanStore refresh semantics and shared unsaved state', () => {
  it('runs a forced refresh after a command even while a poll refresh is in flight', async () => {
    let views = 0
    const api = new ApiClient({
      baseUrl: '', token: 'test',
      fetchImpl: async (url, init) => {
        if (init?.method === 'GET' && String(url).includes('/task-plan') && !String(url).includes('/control-receipts')) {
          views += 1
          await new Promise((resolve) => setTimeout(resolve, 5))
          return new Response(JSON.stringify(operationView('succeeded', 'valid')), { status: 200 })
        }
        return new Response(JSON.stringify({ error: { code: 'not_found', message: 'missing' } }), { status: 404 })
      },
    })
    const store = new TaskPlanStore(api, 'w', 's')
    const poll = store.refresh()
    store.refresh() // collapses into the in-flight poll fetch
    await store.refresh(undefined, true) // forced: post-command fetch must not be skipped
    await poll
    expect(views).toBe(2)
  })

  it('posts the dedicated dependencies action with the full parent set', async () => {
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', operationView('succeeded', 'valid'))
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/nodes', {
      planning_operation_id: 'wop_dep', workspace_id: 'w', session_id: 's', planning_binding_id: 'wplan_a',
      operation: 'validate', command_id: 'cmd_dep', base_draft_id: 'wdraft_1', base_draft_version: 2,
      status: 'succeeded', result_draft_id: 'wdraft_1', result_draft_version: 3, error_code: null,
      diagnostics: [], cancelled_at: null, cancel_reason: null, row_version: 1, created_at: '', updated_at: '',
    })
    const { api, calls } = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.refresh()
    await store.editNode('dependencies', { nodeId: 'work', dependsOn: ['prep', 'prep'] })
    const edit = calls.find((call) => call.method === 'POST' && call.url.includes('/task-plan/nodes'))!
    // The store forwards verbatim; server-side sorted(set(...)) dedupes.
    expect(edit.body).toMatchObject({ action: 'dependencies', node_id: 'work', depends_on: ['prep', 'prep'], expected_version: 2 })
  })

  it('exposes the plan-level unsaved registry for the chat start guard', () => {
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', operationView('succeeded', 'valid'))
    const { api } = client()
    const store = new TaskPlanStore(api, 'w', 's')
    expect(store.hasUnsavedEdits).toBe(false)
    store.setUnsavedNodes(['node_1', 'node_2'])
    expect(store.hasUnsavedEdits).toBe(true)
    store.setUnsavedNodes([])
    expect(store.hasUnsavedEdits).toBe(false)
  })
})

describe('control submission identity and durable receipts', () => {
  const receipt = {
    command_id: 'cmd_plan_ctrl_a', client_message_id: 'client.plan.a', session_id: 's',
    text: '继续', state: 'terminal', intent: 'resume_run', status: 'unresolved' as const,
    disposition: 'unresolved', message: '原运行已结束（已取消或失败），无法原地恢复；可生成修复计划作为新的运行。',
    result_kind: null, result_id: null, error_code: null, outcome: {disposition: 'unresolved'},
    revision: 2, created_at: '2026-09-09T00:00:00Z', updated_at: '2026-09-09T00:00:00Z',
  }

  it('sends one command per input and replays the same id on retry', async () => {
    // The more specific route must be registered first: stub matching is prefix-based.
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan/control-receipts', {items: [receipt]})
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', operationView('succeeded', 'valid'))
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/control', {
      disposition: 'unresolved', intent: 'resume_run', message: receipt.message, next_action: 'repair',
      state: 'terminal', control: null, deterministic: true, replayed: true,
    })
    const {api, calls} = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.refresh()
    expect(store.getState().receipts).toHaveLength(1)

    await store.control('继续')
    expect(store.getState().error).toContain('无法原地恢复')
    const posts = calls.filter((call) => call.method === 'POST' && call.url.includes('/task-plan/control'))
    expect(posts).toHaveLength(1)
    expect(String((posts[0]!.body as Record<string, unknown>).client_message_id)).toMatch(/^client\.plan\./)

    // The same text in the same target state reuses the identity: the server
    // replays the stored receipt instead of executing a second command.
    await store.control('继续')
    const retries = calls.filter((call) => call.method === 'POST' && call.url.includes('/task-plan/control'))
    expect(retries).toHaveLength(2)
    expect(String((retries[1]!.body as Record<string, unknown>).command_id)).toBe(
      String((retries[0]!.body as Record<string, unknown>).command_id),
    )
  })

  it('rejects a concurrent second control input while one is in flight', async () => {
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan/control-receipts', {items: []})
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', operationView('succeeded', 'valid'))
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/control', {
      disposition: 'executed', intent: 'pause_run', state: 'draining', control: null, deterministic: true,
    })
    const {api, calls} = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.refresh()
    const [first, second] = await Promise.all([store.control('暂停'), store.control('暂停')])
    expect(first.kind).toBe('handled')
    expect(second.kind).toBe('handled')
    expect(calls.filter((call) => call.method === 'POST' && call.url.includes('/task-plan/control'))).toHaveLength(1)
    expect(store.getState().error).toContain('仍在处理中')
  })
})

describe('direct-run control channel (BUG-GUI-001)', () => {
  const directRunView = (state: 'running' | 'draining' | 'paused' | 'none'): TaskPlanViewWire => ({
    ...operationView('succeeded'),
    binding: null,
    draft: null,
    version: null,
    control: {
      state,
      allowed_intents: [],
      hint: '',
      resume_available: state === 'paused' || state === 'draining',
      cancel_available: state !== 'none',
      start_available: false,
      repair_available: false,
      target: {
        workflow_run_id: state === 'none' ? null : 'wrun_1',
        run_status: state === 'none' ? null : 'running',
        run_row_version: state === 'none' ? null : 3,
        pause_requested: false,
        planning_operation_id: null,
        binding_id: null,
        binding_mode: null,
        draft_id: null,
        draft_version: null,
        execution_digest: null,
      },
      allowed_actions: [],
    },
  })

  it('lets a binding-less live run through control with the expected run id', async () => {
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan/control-receipts', {items: []})
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', directRunView('running'))
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/control', {
      disposition: 'executed', intent: 'pause_run', state: 'running', control: null, deterministic: true,
    })
    const {api, calls} = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.refresh()
    const outcome = await store.control('暂停')
    expect(outcome.kind).toBe('handled')
    const post = calls.find((call) => call.method === 'POST' && call.url.includes('/task-plan/control'))!
    expect(post.body).toMatchObject({session_id: 's', text: '暂停', expected_workflow_run_id: 'wrun_1'})
  })

  it('stays unhandled for a binding-less session with no live run', async () => {
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan/control-receipts', {items: []})
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', directRunView('none'))
    const {api, calls} = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.refresh()
    const outcome = await store.control('暂停')
    expect(outcome.kind).toBe('unhandled')
    expect(calls.filter((call) => call.method === 'POST' && call.url.includes('/task-plan/control'))).toHaveLength(0)
  })

  it('reuses the control command id across a run state change when the response was lost', async () => {
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan/control-receipts', {items: []})
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', directRunView('running'))
    // The first response is lost; by the time the client retries, the run has
    // moved running → draining. Text + run are unchanged, so one command id.
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/control', 'network')
    const {api, calls} = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.refresh()
    await store.control('暂停')
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan/control', {
      disposition: 'executed', intent: 'pause_run', state: 'draining', control: null, deterministic: true,
    })
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', directRunView('draining'))
    const second = await store.control('暂停')
    expect(second.kind).toBe('handled')
    const posts = calls.filter((call) => call.method === 'POST' && call.url.includes('/task-plan/control'))
    expect(posts).toHaveLength(2)
    expect(String((posts[1]!.body as Record<string, unknown>).command_id)).toBe(
      String((posts[0]!.body as Record<string, unknown>).command_id),
    )
  })
})

describe('generation-scoped pause/resume (B coordination, A09)', () => {
  const generationView = (status: string): TaskPlanViewWire => ({
    ...operationView('queued'),
    operations: [],
    // B's additive top-level projection; read structurally by the store.
    generation: { planning_operation_id: 'wop_gen_1', status, pause_lifecycle: status === 'paused' ? 'suspended' : 'requested' },
  } as unknown as TaskPlanViewWire)

  function generationTransport() {
    const calls: Array<{operation: string; body: Record<string, unknown>}> = []
    return {
      calls,
      transport: {
        pauseGeneration: async (_ws: string, _s: string, _operationId: string, body: Record<string, unknown>) => {
          calls.push({operation: 'pause', body})
          return {replayed: false}
        },
        resumeGeneration: async (_ws: string, _s: string, _operationId: string, body: Record<string, unknown>) => {
          calls.push({operation: 'resume', body})
          return {status: 'succeeded'}
        },
      },
    }
  }

  it('pauses the planning operation via the injected transport, never the run endpoints', async () => {
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', generationView('running'))
    responses.set('POST /v1/workspaces/w/sessions/s/task-plan', 'network')
    const {api} = client()
    const {calls, transport} = generationTransport()
    const store = new TaskPlanStore(api, 'w', 's', transport)
    await store.start()
    await store.pauseGeneration()
    expect(calls).toHaveLength(1)
    expect(calls[0].operation).toBe('pause')
    expect(calls[0].body.session_id).toBe('s')
    expect(String(calls[0].body.command_id)).toMatch(/^cmd_plan_gen_pause_/)
    // The run-scoped endpoints were never touched.
    expect(store.getState().error).toBeNull()
    expect(store.getState().notice).toContain('暂停生成')
    store.stop()
  })

  it('resumes only a paused generation and reports the terminal operation', async () => {
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', generationView('paused'))
    const {api} = client()
    const {calls, transport} = generationTransport()
    const store = new TaskPlanStore(api, 'w', 's', transport)
    await store.start()
    await store.resumeGeneration()
    expect(calls).toHaveLength(1)
    expect(calls[0].operation).toBe('resume')
    expect(store.getState().notice).toContain('继续生成')
    store.stop()
  })

  it('refuses wrong states and stays honest without a transport', async () => {
    responses.set('GET /v1/workspaces/w/sessions/s/task-plan', generationView('running'))
    const {api} = client()
    const store = new TaskPlanStore(api, 'w', 's')
    await store.start()
    await store.resumeGeneration()
    expect(store.getState().error).toContain('没有可继续的生成')
    await store.pauseGeneration()
    expect(store.getState().error).toContain('当前无法暂停生成')
    store.stop()
  })
})
