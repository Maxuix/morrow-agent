// @vitest-environment jsdom
import { createRoot, type Root } from 'react-dom/client'
import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiClient } from '../../api/client'
import { NavigationStore } from '../../state/navigation'
import { WorkspaceKnowledgePage } from './WorkspaceKnowledgePage'

const knowledgeItem = {
  head: { knowledge_id: 'knw_1', semantic_key: 'architecture.api', category: 'architecture', status: 'active', row_version: 3 },
  current_revision: { statement: '公开接口使用 JSON。' },
  evidence: [{ evidence_id: 'lev_1', source_kind: 'chat', excerpt_redacted: '约定用 JSON' }],
}

const candidateItem = {
  candidate_id: 'lcn_1',
  candidate_type: 'project_knowledge',
  semantic_key: 'architecture.api',
  status: 'proposed',
  session_id: null,
  task_run_id: null,
}

const candidateView = {
  candidate: {
    candidate_id: 'lcn_1', candidate_type: 'project_knowledge', status: 'proposed', row_version: 2,
    semantic_key: 'architecture.api', proposed_scope: 'workspace',
    proposed_payload: { statement: '公开接口使用 JSON。', semantic_key: 'architecture.api', category: 'architecture' },
  },
  evidence: [],
  target: { statement: null, reason: null },
  conflicts: [],
}

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { 'content-type': 'application/json' } })
}

function postedAction(call: { method: string; body: unknown }): string | undefined {
  if (call.method !== 'POST' || !call.body || typeof call.body !== 'object' || !('action' in call.body)) return undefined
  return String((call.body as { action: unknown }).action)
}

function client(options?: { conflict?: boolean; knowledge?: unknown[] }) {
  const calls: { url: string; method: string; body: unknown }[] = []
  const fetchImpl: typeof fetch = async (input, init) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = init?.body ? JSON.parse(String(init.body)) : null
    calls.push({ url, method, body })
    if (url.includes('/v1/workspaces')) return json({ items: [{ workspace_id: 'ws_1', display_name: 'Demo 工程', path: '/tmp', available: true, last_used_at: null, git_root: null }], revision: 1 })
    if (url.includes('/v1/knowledge-management/knowledge') && method === 'GET') {
      return json({ items: options?.knowledge ?? [knowledgeItem], next_cursor: null })
    }
    if (url.includes('/v1/knowledge-management/candidates') && url.includes('identity=')) return json(candidateView)
    if (url.includes('/v1/knowledge-management/candidates')) return json({ items: [candidateItem], next_cursor: null })
    if (url.includes('/v1/knowledge-management/learning-status')) {
      return json({ policy: { mode: 'review_only', row_version: 1 }, pending_reviews: 0, proposed_candidates: 1 })
    }
    if (url.includes('/v1/knowledge-management/proposals')) return json({ items: [], next_cursor: null })
    if (url.includes('/v1/knowledge-management/promotions')) {
      return json({ items: [{ operation_id: 'pop_1', status: 'needs_resolution', target: '偏好文档', failure_code: 'conflict' }], next_cursor: null })
    }
    if (url.includes('/v1/knowledge-management/activations')) return json({ items: [], next_cursor: null })
    if (url.includes('/v1/knowledge-commands/learning')) return json({ status: 'applied' })
    if (url.includes('/v1/management/knowledge/') && method === 'POST') {
      if (options?.conflict) return new Response(JSON.stringify({ error: { code: 'conflict', message: 'stale' } }), { status: 409, headers: { 'content-type': 'application/json' } })
      return json({ result: { status: 'applied' } })
    }
    if (url.includes('/v1/management/learning-decision/') && method === 'POST') {
      if (options?.conflict) return new Response(JSON.stringify({ error: { code: 'conflict', message: 'stale' } }), { status: 409, headers: { 'content-type': 'application/json' } })
      return json({ result: { status: 'applied' } })
    }
    return json({ items: [], next_cursor: null })
  }
  return { client: new ApiClient({ baseUrl: '', token: '', fetchImpl }), calls }
}

describe('WorkspaceKnowledgePage knowledge library', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })
  afterEach(() => {
    act(() => root.unmount())
    container.remove()
  })

  it('lists knowledge with structured fields and enable/disable/dispute/delete', async () => {
    const user = userEvent.setup()
    const { client: api, calls } = client()
    await act(async () => {
      root.render(<WorkspaceKnowledgePage client={api} workspaceId="ws_1" section="knowledge" connected onNavigate={() => {}} onBack={() => {}} />)
    })
    await waitFor(() => screen.getByText('公开接口使用 JSON。'))
    expect(screen.getByText('architecture.api')).toBeDefined()
    expect(screen.getByText(/来源 1 条/)).toBeDefined()
    expect(container.textContent).not.toContain('"knowledge_id"')
    expect(screen.queryByLabelText(/知识 ID|按 ID/)).toBeNull()
    await user.click(screen.getByRole('button', { name: '停用' }))
    await waitFor(() => calls.some(call => postedAction(call) === 'disable'))
    const disable = calls.find(call => postedAction(call) === 'disable')
    expect(disable?.url).toContain('/v1/management/knowledge/knw_1')
    await user.click(screen.getByRole('button', { name: '标记争议' }))
    await waitFor(() => calls.some(call => postedAction(call) === 'dispute'))
    await user.click(screen.getByRole('button', { name: '删除' }))
    await user.click(screen.getByRole('button', { name: '确认删除' }))
    await waitFor(() => calls.some(call => postedAction(call) === 'delete'))
  })

  it('opens real candidate review instead of a form that cannot submit', async () => {
    const user = userEvent.setup()
    const { client: api, calls } = client()
    await act(async () => {
      root.render(<WorkspaceKnowledgePage client={api} workspaceId="ws_1" section="knowledge" connected onNavigate={() => {}} onBack={() => {}} />)
    })
    await user.click(await screen.findByRole('button', { name: '审阅候选' }))
    await user.click(await screen.findByRole('button', { name: /architecture.api · project_knowledge/ }))
    await user.click(await screen.findByRole('button', { name: '接受候选' }))
    await waitFor(() => expect(calls.some(call => call.method === 'POST' && call.url.includes('learning-decision/lcn_1'))).toBe(true))
    expect(screen.queryByRole('button', { name: '＋ 新增知识' })).toBeNull()
    expect(screen.queryByLabelText('知识正文')).toBeNull()
    expect(screen.queryByRole('option', { name: /自动学习/ })).toBeNull()
  })

  it('keeps user input and explains a revision conflict', async () => {
    const user = userEvent.setup()
    const { client: api } = client({ conflict: true })
    await act(async () => {
      root.render(<WorkspaceKnowledgePage client={api} workspaceId="ws_1" section="knowledge" connected onNavigate={() => {}} onBack={() => {}} />)
    })
    await waitFor(() => screen.getByRole('button', { name: '停用' }))
    await user.click(screen.getByRole('button', { name: '停用' }))
    await waitFor(() => screen.getByText(/你的输入已保留/))
    expect(screen.getByText('公开接口使用 JSON。')).toBeDefined()
  })

  it('reviews workspace-wide candidates with known/unknown source and learning mode', async () => {
    const user = userEvent.setup()
    const { client: api, calls } = client()
    await act(async () => {
      root.render(<WorkspaceKnowledgePage client={api} workspaceId="ws_1" section="knowledge" connected onNavigate={() => {}} onBack={() => {}} />)
    })
    await waitFor(() => screen.getByRole('button', { name: '学习' }))
    await user.click(screen.getByRole('button', { name: '学习' }))
    await waitFor(() => screen.getByLabelText('学习模式'))
    expect(screen.getAllByText(/来源未知/).length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: /architecture.api · project_knowledge/ }))
    await waitFor(() => screen.getByRole('button', { name: '接受候选' }))
    expect(screen.getByText('建议正文')).toBeDefined()
    await user.click(screen.getByRole('button', { name: '历史' }))
    await waitFor(() => screen.getByText(/失败：conflict/))
    await user.click(screen.getByRole('button', { name: '重试推广' }))
    await user.click(screen.getByRole('button', { name: '确认执行' }))
    await waitFor(() => calls.some(call => {
      if (!call.url.includes('/v1/knowledge-commands/learning') || !call.body || typeof call.body !== 'object') return false
      return (call.body as { recovery_action?: unknown }).recovery_action === 'retry'
    }))
  })

  it('opens the learning secondary area from a focus param without a cross-page drawer', async () => {
    const { client: api } = client()
    await act(async () => {
      root.render(<WorkspaceKnowledgePage client={api} workspaceId="ws_1" section="knowledge" focus="learning" connected onNavigate={() => {}} onBack={() => {}} />)
    })
    await waitFor(() => screen.getByLabelText('学习模式'))
    expect(screen.queryByRole('heading', { name: '上下文与学习' })).toBeNull()
    expect(screen.queryByLabelText('打开上下文与学习管理')).toBeNull()
  })

  it('protects edits in the real candidate form when leaving', async () => {
    const user = userEvent.setup()
    const { client: api } = client()
    const navigation = new NavigationStore('#/knowledge/knowledge')
    await act(async () => {
      root.render(<WorkspaceKnowledgePage client={api} workspaceId="ws_1" section="knowledge" focus="add" connected
        registerGuard={navigation.registerDirtyGuard} onNavigate={() => {}} onBack={() => {}} />)
    })
    await user.click(await screen.findByRole('button', { name: /architecture.api · project_knowledge/ }))
    await user.click(await screen.findByRole('checkbox', { name: '编辑候选后接受' }))
    const blocked = navigation.navigate({ kind: 'chat' })
    await screen.findByRole('heading', { name: '候选编辑尚未提交' })
    await user.click(screen.getByRole('button', { name: '留在此页' }))
    expect(await blocked).toBe(false)
    const leaving = navigation.navigate({ kind: 'chat' })
    await screen.findByRole('heading', { name: '候选编辑尚未提交' })
    await user.click(screen.getByRole('button', { name: '放弃修改' }))
    expect(await leaving).toBe(true)
  })

  it('does not flash the previous workspace knowledge while the next scope loads', async () => {
    let release: ((value: Response) => void) | undefined
    const pending = new Promise<Response>(resolve => { release = resolve })
    const fetchImpl: typeof fetch = async (input, init) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      const path = url.split('?')[0]
      if (path === '/v1/workspaces') {
        return json({ items: [
          { workspace_id: 'ws_1', display_name: 'Demo 工程', path: '/tmp', available: true, last_used_at: null, git_root: null },
          { workspace_id: 'ws_2', display_name: '第二工程', path: '/tmp2', available: true, last_used_at: null, git_root: null },
        ], revision: 1 })
      }
      if (url.includes('knowledge-management/knowledge') && method === 'GET') return pending
      return json({ items: [], next_cursor: null })
    }
    const first = new ApiClient({ baseUrl: '', token: '', fetchImpl, workspaceId: 'ws_1' })
    await act(async () => {
      root.render(<WorkspaceKnowledgePage client={first} workspaceId="ws_1" section="knowledge" connected onNavigate={() => {}} onBack={() => {}} />)
    })
    expect(screen.getByText('正在读取知识…')).toBeDefined()
    expect(screen.queryByText('公开接口使用 JSON。')).toBeNull()
    await act(async () => {
      release?.(json({ items: [knowledgeItem], next_cursor: null }))
    })
    await waitFor(() => screen.getByText('公开接口使用 JSON。'))

    let releaseSecond: ((value: Response) => void) | undefined
    const secondPending = new Promise<Response>(resolve => { releaseSecond = resolve })
    const secondFetch: typeof fetch = async (input, init) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      const path = url.split('?')[0]
      if (path === '/v1/workspaces') {
        return json({ items: [
          { workspace_id: 'ws_2', display_name: '第二工程', path: '/tmp2', available: true, last_used_at: null, git_root: null },
        ], revision: 1 })
      }
      if (url.includes('knowledge-management/knowledge') && method === 'GET') return secondPending
      return json({ items: [], next_cursor: null })
    }
    const second = new ApiClient({ baseUrl: '', token: '', fetchImpl: secondFetch, workspaceId: 'ws_2' })
    await act(async () => {
      root.render(<WorkspaceKnowledgePage client={second} workspaceId="ws_2" section="knowledge" connected onNavigate={() => {}} onBack={() => {}} />)
    })
    expect(screen.queryByText('公开接口使用 JSON。')).toBeNull()
    expect(screen.getByText('正在读取知识…')).toBeDefined()
    await act(async () => {
      releaseSecond?.(json({
        items: [{
          head: { knowledge_id: 'knw_2', semantic_key: 'domain.other', category: 'domain', status: 'active', row_version: 1 },
          current_revision: { statement: '第二工作区的知识。' },
          evidence: [],
        }],
        next_cursor: null,
      }))
    })
    await waitFor(() => screen.getByText('第二工作区的知识。'))
    expect(screen.queryByText('公开接口使用 JSON。')).toBeNull()
  })
})
