// @vitest-environment jsdom
import { createRoot, type Root } from 'react-dom/client'
import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiClient } from '../../api/client'
import type { LearningPage, ResolvedContext } from '../../api/management'
import { InspectorStore, type InspectorScope } from '../../state/inspector'
import { inspectorResources } from '../../state/inspectorResources'
import { ChatInspector } from './ChatInspector'

const scope: InspectorScope = { workspaceId: 'ws_1', sessionId: 'se_1' }

const contextFixture: ResolvedContext = {
  workspace_id: 'ws_1',
  session_id: 'se_1',
  task_run_id: 'task_1',
  agent_run_id: 'arun_123',
  available_runs: [{
    agent_run_id: 'arun_123',
    label: '当前会话 · 对话运行 · 2026-01-01 10:00',
    task_title: '当前会话',
    node_id: null,
    node_title: null,
    created_at: '2026-01-01T10:00:00+00:00',
  }],
  status: 'resolved',
  preferences: [{
    preference_id: 'pref_1', statement: '默认使用中文。', scope: 'workspace', revision: 1,
    updated_at: '2026-01-01T10:00:00+00:00',
  }],
  profile: {
    name: 'Morrow', summary: '项目摘要', goals: ['完成交付'], tech_stack: ['Python'],
    constraints: [], conventions: ['运行离线测试'],
  },
  source_revisions: [],
  preference_digest: 'secret_digest_should_not_render',
  omitted_count: 0,
  refresh_status: 'ok',
  prompt_constraints: {
    availability: 'available',
    message: null,
    sections: [
      { kind: 'profile', label: '提示配置', available: true, summary: '已冻结提示配置引用；正文不在历史投影中。' },
      { kind: 'role', label: '角色约束', available: true, summary: '已冻结角色约束摘要；正文不在历史投影中。' },
      { kind: 'project_instructions', label: '项目指令', available: true, summary: '已记录项目指令来源元数据。', sources: [{ path: 'AGENTS.md', scope: 'workspace', byte_count: 128 }] },
    ],
  },
  knowledge: [],
  memory_selection_id: null,
  pending_learning_count: 0,
  convention_count: 1,
  language: null,
  verbosity: null,
}

const learningFixture: LearningPage = {
  candidates: [{
    expired: false,
    candidate: {
      candidate_id: 'lcn_123', candidate_type: 'profile', status: 'proposed', row_version: 1,
      semantic_key: 'communication.language', proposed_scope: 'workspace',
      proposed_payload: { path: 'summary', value: '中文' },
    },
    evidence: [{ evidence_id: 'lev_1', source_kind: 'user_turn', excerpt_redacted: '默认使用中文。' }],
    target: { statement: null, reason: null }, conflicts: [],
    source: { kind: 'task', known: true, session_id: 'se_1', task_run_id: 'task_1' },
  }],
  proposals: [],
  next_cursor: null,
  limit: 50,
  candidate_count: 1,
  proposal_count: 0,
  total_count: 1,
  unknown_source_count: 0,
  source_scope: 'session',
}

function jsonResponse(value: unknown) {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}

function Harness({ store, client }: { store: InspectorStore; client: ApiClient }) {
  return <ChatInspector
    store={store}
    bodyProps={{ client, workspaceId: scope.workspaceId, sessionId: scope.sessionId, currentTaskRunId: 'task_1' }}
  />
}

describe('Inspector context and learning bodies', () => {
  let container: HTMLDivElement
  let root: Root
  let store: InspectorStore
  let calls: { url: string; method: string; body: string | undefined }[]
  let client: ApiClient

  beforeEach(() => {
    sessionStorage.clear()
    inspectorResources.clear()
    calls = []
    client = new ApiClient({
      baseUrl: '',
      token: '',
      fetchImpl: async (url, init) => {
        const path = String(url)
        calls.push({ url: path, method: init?.method ?? 'GET', body: typeof init?.body === 'string' ? init.body : undefined })
        if (path.includes('/management/context')) return jsonResponse(contextFixture)
        if (path.includes('/management/learning-decision/')) return jsonResponse({ result: { status: 'applied' } })
        if (path.includes('/management/learning')) return jsonResponse(learningFixture)
        return jsonResponse({})
      },
    })
    store = new InspectorStore()
    store.setScope(scope)
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => root.unmount())
    container.remove()
    inspectorResources.clear()
  })

  it('loads only the active body and keeps frozen context identifiers out of visible text', async () => {
    act(() => {
      store.openInspectorTab('context')
      store.openInspectorTab('learning')
      root.render(<Harness store={store} client={client} />)
    })
    await act(async () => { await Promise.resolve() })
    expect(calls.filter(call => call.method === 'GET').map(call => call.url)).toEqual([
      '/v1/management/learning?session_id=se_1&task_run_id=task_1&page=0',
    ])

    await act(async () => {
      store.activateTab('context')
      await Promise.resolve()
    })
    expect(calls.filter(call => call.method === 'GET').map(call => call.url)).toContain(
      '/v1/management/context?session_id=se_1&task_run_id=task_1',
    )
    expect(container.textContent).toContain('当前会话 · 对话运行')
    expect(container.textContent).toContain('提示配置 · 已记录')
    expect(container.textContent).not.toContain('arun_123')
    expect(container.textContent).not.toContain('secret_digest_should_not_render')
  })

  it('uses existing learning decisions and refreshes the same scoped collection', async () => {
    act(() => {
      store.openInspectorTab('learning')
      root.render(<Harness store={store} client={client} />)
    })
    await act(async () => { await Promise.resolve() })
    expect(screen.getByText('当前任务')).toBeTruthy()
    await userEvent.setup().click(screen.getByRole('button', { name: '接受候选' }))
    await act(async () => { await Promise.resolve(); await Promise.resolve() })

    const decision = calls.find(call => call.method === 'POST')
    expect(decision?.url).toBe('/v1/management/learning-decision/lcn_123')
    expect(decision?.body).toContain('command_id')
    expect(calls.filter(call => call.method === 'GET').every(call => call.url.includes('session_id=se_1'))).toBe(true)
    expect(screen.getByRole('status').textContent).toContain('审阅决定已提交')
  })
})
