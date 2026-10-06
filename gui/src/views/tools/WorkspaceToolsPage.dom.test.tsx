// @vitest-environment jsdom
import { createRoot, type Root } from 'react-dom/client'
import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ApiClient } from '../../api/client'
import type { ManagedSkill, SkillDraft } from '../../api/management'
import { NavigationStore } from '../../state/navigation'
import { WorkspaceToolsPage } from './WorkspaceToolsPage'

const skill: ManagedSkill = {
  enabled: false,
  status: {
    skill_id: 'skl_1', name: '报告助手', source_kind: 'imported', scope_id: 'ws_1',
    availability: 'available', effective_trust: 'workspace', requested_trust: 'untrusted',
    binding: { pinned_version_id: null },
  },
  versions: [{
    version: { version_id: 'skv_1', display_version: '1.0', tree_digest: 'ab', source_kind: 'imported' },
    summary: '生成周报',
    manifest: { required_tools: ['read'], required_mcp_servers: [], requested_permissions: [] },
    scripts: [], inspection_error: null,
  }],
  usage: [],
}

const draft: SkillDraft = {
  draft: { draft_id: 'sdf_1', name: 'Generated report', status: 'validated', row_version: 1, revision: 1, tree_digest: 'digest', evidence_refs: ['lcn_1'], accepted_version_id: null, parent_draft_id: null },
  validation: { valid: true, findings: [] },
  skill_md: '# Skill', text_diff: null, editable: true, inspection_error: null, diff: null,
}

const mcpServer = {
  server_id: 'mcp_demo', display_name: '演示服务', transport: 'stdio', executable: '/usr/bin/demo',
  argv_count: 0, cwd_policy: 'workspace', workspace_visibility: 'read_write', requested_launch_risks: ['network'],
  enabled: false, config_revision: 1, catalog_revision: null, catalog_status: 'missing', tool_count: 0, degraded_reason: '尚未刷新目录',
}

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { 'content-type': 'application/json' } })
}

function postedAction(call: { method: string; body: unknown }): string | undefined {
  if (call.method !== 'POST' || !call.body || typeof call.body !== 'object' || !('action' in call.body)) return undefined
  return String((call.body as { action: unknown }).action)
}

function client(options?: { conflict?: boolean; lost?: 'enable' | 'edit'; failure?: 'network' | 503 }) {
  let enabled = false
  let currentDraft = draft
  const receipts = new Set<string>()
  const calls: { url: string; method: string; body: unknown }[] = []
  const fetchImpl: typeof fetch = async (input, init) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = init?.body ? JSON.parse(String(init.body)) : null
    calls.push({ url, method, body })
    if (url.includes('/v1/workspaces')) {
      return json({ items: [{ workspace_id: 'ws_1', display_name: 'Demo 工程', path: '/tmp', available: true, last_used_at: null, git_root: null }], revision: 1 })
    }
    if (url.includes('/v1/management/skills') && method === 'GET') {
      return json({ skills: [{ ...skill, enabled }], scope: 'workspace', binding_digest: (enabled ? 'b' : 'a').repeat(64), next_cursor: null })
    }
    if (url.includes('/v1/management/skill-drafts')) return json({ drafts: [currentDraft], limit: 50, next_cursor: null })
    if (url.includes('/v1/management/skill-binding/') && method === 'POST') {
      if (options?.conflict) return new Response(JSON.stringify({ error: { code: 'conflict', message: 'stale' } }), { status: 409, headers: { 'content-type': 'application/json' } })
      if (!receipts.has(body.command_id)) {
        if (body.action === 'enable' || body.action === 'disable') enabled = body.action === 'enable'
        receipts.add(body.command_id)
        if (options?.lost === 'enable') {
          if (options.failure === 503) return json({ error: { message: 'lost receipt', code: 'server_error' } }, 503)
          throw new TypeError('lost receipt')
        }
      }
      return json({ result: { status: 'applied' } })
    }
    if (url.includes('/v1/management/skill-draft/') && method === 'POST') {
      if (options?.lost === 'edit' && !receipts.has(body.command_id)) {
        receipts.add(body.command_id)
        currentDraft = { ...draft, skill_md: body.skill_md, draft: { ...draft.draft, draft_id: 'sdf_2', parent_draft_id: 'sdf_1', revision: 2, row_version: 2 } }
        throw new TypeError('lost receipt')
      }
      return json({ result: { status: 'applied' } })
    }
    if (url.includes('/v1/mcp-management') && url.includes('identity=')) {
      return json({
        scope: 'workspace', revision: 1, digest: 'a'.repeat(64), server: mcpServer, tools: [],
        configuration: { timeout_ms: 15000, cwd: null, tool_policy: { allowlist: [], mappings: [] } },
        credentials: [], credential_status: 'none',
      })
    }
    if (url.includes('/v1/mcp-management')) {
      return json({ scope: 'workspace', revision: 1, digest: 'a'.repeat(64), servers: [mcpServer], next_cursor: null })
    }
    if (url.includes('/v1/mcp-actions')) return json({ status: 'accepted' })
    return json({})
  }
  return { client: new ApiClient({ baseUrl: '', token: '', fetchImpl }), calls }
}

describe('WorkspaceToolsPage skills and MCP', () => {
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

  it('binds a version and enables a skill without ID lookup or raw JSON', async () => {
    const user = userEvent.setup()
    const { client: api, calls } = client()
    await act(async () => {
      root.render(<WorkspaceToolsPage client={api} workspaceId="ws_1" section="skills" connected onNavigate={() => {}} onBack={() => {}} />)
    })
    await waitFor(() => screen.getByRole('button', { name: '未启用' }))
    expect(screen.getByRole('button', { name: '全局安装' })).toBeDefined()
    expect(screen.queryByLabelText('管理作用域')).toBeNull()
    await user.click(screen.getByRole('button', { name: '未启用' }))
    await waitFor(() => screen.getByText('报告助手'))
    expect(screen.getAllByText('本项目').length).toBeGreaterThan(0)
    expect(container.textContent).not.toContain('"skill_id"')
    await waitFor(() => screen.getByRole('button', { name: '在本项目启用' }))
    await user.click(screen.getByRole('button', { name: '绑定所选版本' }))
    await waitFor(() => calls.some(call => postedAction(call) === 'pin'))
    await user.click(screen.getByRole('button', { name: '在本项目启用' }))
    await waitFor(() => calls.some(call => postedAction(call) === 'enable'))
    await user.click(screen.getByRole('button', { name: '草稿' }))
    await waitFor(() => screen.getByRole('button', { name: '接受并发布版本' }))
    await user.click(screen.getByRole('button', { name: '重新校验' }))
    await waitFor(() => calls.some(call => postedAction(call) === 'validate'))
  })

  it.each(['network', 503] as const)('replays a lost enable after %s read-back without sending disable', async failure => {
    const user = userEvent.setup()
    const { client: api, calls } = client({ lost: 'enable', failure })
    await act(async () => {
      root.render(<WorkspaceToolsPage client={api} workspaceId="ws_1" section="skills" connected onNavigate={() => {}} onBack={() => {}} />)
    })
    await user.click(await screen.findByRole('button', { name: '未启用' }))
    await user.click(await screen.findByRole('button', { name: '在本项目启用' }))
    await screen.findByRole('button', { name: '重试原操作' })
    await user.click(screen.getByRole('button', { name: '已启用' }))
    const disable = await screen.findByRole('button', { name: '在本项目停用' })
    expect((disable as HTMLButtonElement).disabled || disable.closest('fieldset')?.disabled).toBe(true)
    await user.click(disable)
    expect(calls.filter(call => postedAction(call) === 'disable')).toHaveLength(0)
    await user.click(screen.getByRole('button', { name: '重试原操作' }))
    await screen.findByText(/已保存。新设置/)
    const writes = calls.filter(call => call.method === 'POST')
    expect(writes).toHaveLength(2)
    expect(writes[1].body).toEqual(writes[0].body)
    expect(writes[0].body).toMatchObject({ action: 'enable', expected_digest: 'a'.repeat(64) })
    expect(screen.queryByRole('button', { name: '重试原操作' })).toBeNull()
    expect(calls.filter(call => postedAction(call) === 'disable')).toHaveLength(0)
  })

  it('replays the original draft edit after read-back shows its successor', async () => {
    const user = userEvent.setup()
    const { client: api, calls } = client({ lost: 'edit' })
    await act(async () => {
      root.render(<WorkspaceToolsPage client={api} workspaceId="ws_1" section="skills" connected onNavigate={() => {}} onBack={() => {}} />)
    })
    await user.click(await screen.findByRole('button', { name: '草稿' }))
    await user.click(await screen.findByRole('button', { name: '编辑 SKILL.md' }))
    const input = screen.getByLabelText('新的完整 SKILL.md')
    await user.clear(input)
    await user.type(input, '# Revised skill')
    await user.click(screen.getByRole('button', { name: '保存 Draft 修订' }))
    await screen.findByRole('button', { name: '重试原操作' })
    await screen.findByText('# Revised skill')
    await user.click(screen.getByRole('button', { name: '重试原操作' }))
    await screen.findByText(/已保存。新设置/)
    const writes = calls.filter(call => call.method === 'POST')
    expect(writes).toHaveLength(2)
    expect(writes[1].url).toBe(writes[0].url)
    expect(writes[0].url).toContain('/skill-draft/sdf_1')
    expect(writes[1].body).toEqual(writes[0].body)
    expect(writes[1].body).toMatchObject({ action: 'edit', expected_row_version: 1, skill_md: '# Revised skill' })
  })

  it('keeps skill actions available after a 409 and explains that input is retained', async () => {
    const user = userEvent.setup()
    const { client: api } = client({ conflict: true })
    await act(async () => {
      root.render(<WorkspaceToolsPage client={api} workspaceId="ws_1" section="skills" connected onNavigate={() => {}} onBack={() => {}} />)
    })
    await user.click(await screen.findByRole('button', { name: '未启用' }))
    await user.click(await screen.findByRole('button', { name: '在本项目启用' }))
    await waitFor(() => screen.getByText(/你的输入已保留/))
    expect(screen.getByRole('button', { name: '在本项目启用' })).toBeDefined()
  })

  it('configures and enables MCP from the list without an ID inspect box', async () => {
    const user = userEvent.setup()
    const { client: api, calls } = client()
    await act(async () => {
      root.render(<WorkspaceToolsPage client={api} workspaceId="ws_1" section="mcp" connected onNavigate={() => {}} onBack={() => {}} />)
    })
    await waitFor(() => screen.getByText(/演示服务/))
    expect(screen.queryByLabelText('MCP查询ID')).toBeNull()
    expect(screen.queryByText(/打开此页不会启动进程/)).toBeNull()
    await user.click(screen.getByRole('button', { name: /演示服务/ }))
    await waitFor(() => screen.getByRole('button', { name: '启用 MCP 服务' }))
    expect(screen.getAllByText('尚未刷新目录').length).toBeGreaterThan(0)
    expect(screen.queryByText(/未把它显示成已连接/)).toBeNull()
    await user.click(screen.getByRole('button', { name: '启用 MCP 服务' }))
    await user.click(screen.getByRole('button', { name: '确认 MCP 操作' }))
    await waitFor(() => calls.some(call => call.url.includes('/v1/mcp-actions') && postedAction(call) === 'enable'))
    await user.click(screen.getByRole('button', { name: '添加 MCP 服务' }))
    expect(screen.getByLabelText('MCP服务ID')).toBeDefined()
    expect(screen.getByLabelText('MCP可执行文件')).toBeDefined()
  })

  it('locates a named skill without a cross-page drawer', async () => {
    const { client: api } = client()
    await act(async () => {
      root.render(<WorkspaceToolsPage client={api} workspaceId="ws_1" section="skills" item="报告助手" connected onNavigate={() => {}} onBack={() => {}} />)
    })
    await waitFor(() => screen.getByText('已定位到 Skill：报告助手'))
    expect(screen.queryByRole('heading', { name: '上下文与学习' })).toBeNull()
    expect(screen.queryByLabelText('打开上下文与学习管理')).toBeNull()
  })

  it('prompts when leaving a dirty MCP config form, including simulated back', async () => {
    const user = userEvent.setup()
    const { client: api } = client()
    window.location.hash = '#/tools/mcp'
    const navigation = new NavigationStore()
    const stop = navigation.start()
    await act(async () => {
      root.render(<WorkspaceToolsPage client={api} workspaceId="ws_1" section="mcp" connected
        registerGuard={navigation.registerDirtyGuard} onNavigate={() => {}} onBack={() => {}} />)
    })
    await waitFor(() => screen.getByRole('button', { name: '添加 MCP 服务' }))
    await user.click(screen.getByRole('button', { name: '添加 MCP 服务' }))
    expect(screen.getByLabelText('MCP服务ID')).toBeDefined()
    const blocked = navigation.navigate({ kind: 'chat' })
    await waitFor(() => screen.getByRole('heading', { name: 'MCP 配置尚未保存' }))
    await user.click(screen.getByRole('button', { name: '留在此页' }))
    expect(await blocked).toBe(false)
    expect(navigation.getState().location.kind).toBe('tools')
    window.location.hash = '#/chat'
    await new Promise<void>(resolve => setTimeout(resolve, 0))
    await new Promise<void>(resolve => setTimeout(resolve, 0))
    await new Promise<void>(resolve => setTimeout(resolve, 0))
    await waitFor(() => screen.getByRole('heading', { name: 'MCP 配置尚未保存' }))
    await user.click(screen.getByRole('button', { name: '留在此页' }))
    expect(navigation.getState().location.kind).toBe('tools')
    expect(window.location.hash).toContain('/tools/mcp')
    stop()
  })

  it('does not flash the previous workspace skill list while the next scope loads', async () => {
    let release: ((value: Response) => void) | undefined
    const pending = new Promise<Response>(resolve => { release = resolve })
    const fetchImpl: typeof fetch = async (input) => {
      const url = String(input)
      const path = url.split('?')[0]
      if (path === '/v1/workspaces') {
        return json({ items: [{ workspace_id: 'ws_1', display_name: 'Demo 工程', path: '/tmp', available: true, last_used_at: null, git_root: null }], revision: 1 })
      }
      if (url.includes('management/skills') && !url.includes('skill-drafts') && !url.includes('skill-binding')) return pending
      if (url.includes('skill-drafts')) return json({ drafts: [], limit: 50, next_cursor: null })
      return json({})
    }
    await act(async () => {
      root.render(<WorkspaceToolsPage client={new ApiClient({ baseUrl: '', token: '', fetchImpl, workspaceId: 'ws_1' })} workspaceId="ws_1" section="skills" connected onNavigate={() => {}} onBack={() => {}} />)
    })
    expect(screen.getByText('加载中…')).toBeDefined()
    expect(screen.queryByText('报告助手')).toBeNull()
    await act(async () => {
      release?.(json({ skills: [skill], scope: 'workspace', binding_digest: 'a'.repeat(64), next_cursor: null }))
    })
    await userEvent.setup().click(await screen.findByRole('button', { name: '未启用' }))
    await waitFor(() => screen.getByText('报告助手'))

    let releaseSecond: ((value: Response) => void) | undefined
    const secondPending = new Promise<Response>(resolve => { releaseSecond = resolve })
    const secondFetch: typeof fetch = async (input) => {
      const url = String(input)
      const path = url.split('?')[0]
      if (path === '/v1/workspaces') {
        return json({ items: [{ workspace_id: 'ws_2', display_name: '第二工程', path: '/tmp2', available: true, last_used_at: null, git_root: null }], revision: 1 })
      }
      if (url.includes('management/skills') && !url.includes('skill-drafts') && !url.includes('skill-binding')) return secondPending
      if (url.includes('skill-drafts')) return json({ drafts: [], limit: 50, next_cursor: null })
      return json({})
    }
    await act(async () => {
      root.render(<WorkspaceToolsPage client={new ApiClient({ baseUrl: '', token: '', fetchImpl: secondFetch, workspaceId: 'ws_2' })} workspaceId="ws_2" section="skills" connected onNavigate={() => {}} onBack={() => {}} />)
    })
    expect(screen.queryByText('报告助手')).toBeNull()
    expect(screen.getByText('加载中…')).toBeDefined()
    await act(async () => {
      releaseSecond?.(json({
        skills: [{
          ...skill,
          status: { ...skill.status, skill_id: 'skl_2', name: '第二项目助手' },
        }],
        scope: 'workspace', binding_digest: 'b'.repeat(64), next_cursor: null,
      }))
    })
    await userEvent.setup().click(await screen.findByRole('button', { name: '未启用' }))
    await waitFor(() => screen.getByText('第二项目助手'))
    expect(screen.queryByText('报告助手')).toBeNull()
  })
})
