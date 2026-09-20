// @vitest-environment jsdom
import { createRoot, type Root } from 'react-dom/client'
import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../api/client'
import { NavigationStore } from '../../state/navigation'
import { THEME_STORAGE_KEY } from '../../state/theme'
import { SettingsPage } from './SettingsPage'
import type { ChatSettingsView, ProviderSettingsView } from '../../api/settings'
import type { AgentDefinitionViewWire, AgentPresetCatalogWire } from '../../api/types'

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { 'content-type': 'application/json' } })
}

const providerView: ProviderSettingsView = {
  revision: 3,
  active_model: null,
  presets: [],
  adapters: [{ adapter_id: 'fake', discovery: false }],
  providers: [{
    provider_id: 'demo',
    adapter: 'fake',
    base_url: 'https://example.test',
    credential_configured: false,
    last_test: null,
    models: [{ model_id: 'local', api_model_id: 'vendor', capabilities: null }],
  }],
}

const chatSettings: ChatSettingsView = {
  permission_presets: [{ preset: 'manual', label: '手动', available: true, reason: null }],
  documents: {
    session: { revision: 1, settings: { model: { provider_id: 'demo', model_id: 'local' }, generation: null, permission: 'manual' } },
    workspace: { revision: 1, settings: { model: null, generation: null, permission: null } },
    global: { revision: 1, settings: { model: null, generation: null, permission: null } },
  },
  effective: { model: { provider_id: 'demo', model_id: 'local' }, generation: null, permission: 'manual' },
  sources: {
    model: { scope: 'session', revision: 1 },
    generation: { scope: 'adapter', revision: 0 },
    permission: { scope: 'session', revision: 1 },
  },
}

const presets: AgentPresetCatalogWire = {
  presets: [
    { definition_id: 'builtin_general', role: 'general', name: 'General', description: '通用执行', access: 'write', available: true, preference: null, preference_source: 'inherit_session' },
  ],
  revision: 3,
}

const helper: AgentDefinitionViewWire = {
  definition_id: 'helper',
  origin: 'user',
  source_revision: 2,
  source_hash: null,
  revoked: false,
  desired_ahead_of_published: true,
  source: {
    definition_id: 'helper',
    name: 'Helper',
    description: 'Inspect',
    role_prompt: 'Inspect carefully.',
    skill_version_ids: [],
    tool_requirements: [],
    access_mode_ceiling: 'read',
    max_agent_generation_requests: null,
    model_selection: 'invoking_active',
    derived_from_version_id: null,
    derived_from_definition_id: null,
    derived_from_source_hash: null,
  },
  head: { workspace_id: 'ws_1', definition_id: 'helper', version_id: 'adev_1', source_revision: 2, source_hash: 'a'.repeat(64), enabled: true, row_version: 4 },
  published_version: null,
}

function client(options?: { job?: Record<string, unknown> }) {
  const calls: { url: string; method: string; body: unknown }[] = []
  const fetchImpl: typeof fetch = async (input, init) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = init?.body ? JSON.parse(String(init.body)) : null
    calls.push({ url, method, body })
    const path = url.split('?')[0]
    if (path === '/v1/workspaces') {
      return json({ items: [{ workspace_id: 'ws_1', display_name: 'Demo 工程', path: '/tmp', available: true, last_used_at: null, git_root: null }], revision: 1 })
    }
    if (url.includes('/providers') && !url.includes('/catalog/providers')) return json(providerView)
    if (url.includes('/settings')) return json(chatSettings)
    if (url.includes('/agent-presets')) return json(presets)
    if (url.includes('/catalog/agent-definitions')) return json({ agent_definitions: [helper] })
    if (url.includes('/catalog/providers')) return json({ providers: [], active_model: null })
    if (url.includes('/catalog/skills')) return json({ skills: [] })
    if (url.includes('/catalog/tools')) return json({ tools: [{ name: 'read', description: 'read' }] })
    if (url.includes('/catalog/artifact-contracts')) return json({ contracts: [] })
    if (url.includes('/agent-definitions/') && url.includes('/publish')) {
      return json({ result: { agent_definition: { ...helper, desired_ahead_of_published: false } } })
    }
    if (url.includes('/agent-definitions/quick-save')) {
      return json({ definition_id: 'helper_custom', available_version_id: 'adev_2', enabled: true, agent_definition: helper })
    }
    if (url.includes('/state/status')) {
      return json({ scope: 'data_root', maintaining: false, blockers: ['ws_busy'], backups: ['backup-1'], next_cursor: null })
    }
    if (url.includes('/state-actions')) return json({ command_id: 'cmd_state_1' })
    if (url.includes('/state-jobs/')) {
      return json(options?.job ?? { status: 'succeeded', action: 'doctor', message: '存储健康', result: { checks: 3 } })
    }
    if (url.includes('/state/events')) {
      return json({ events: [{ action: 'backup', message: '已完成' }], next_cursor: null })
    }
    return json({})
  }
  return { client: new ApiClient({ baseUrl: '', token: '', fetchImpl, workspaceId: 'ws_1' }), calls }
}

describe('SettingsPage four tabs', () => {
  let container: HTMLDivElement
  let root: Root

  const localMemory = new Map<string, string>()
  const sessionMemory = new Map<string, string>()
  const makeStorage = (memory: Map<string, string>) => ({
    get length() { return memory.size },
    key(index: number) { return [...memory.keys()][index] ?? null },
    clear() { memory.clear() },
    getItem(key: string) { return memory.get(key) ?? null },
    setItem(key: string, value: string) { memory.set(key, value) },
    removeItem(key: string) { memory.delete(key) },
  })
  const storage = makeStorage(localMemory)
  const session = makeStorage(sessionMemory)

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    localMemory.clear()
    sessionMemory.clear()
    vi.stubGlobal('localStorage', storage)
    vi.stubGlobal('sessionStorage', session)
    document.documentElement.removeAttribute('data-theme')
  })

  afterEach(() => {
    act(() => root.unmount())
    container.remove()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('renders four tabs, returns to chat, and keeps providers/appearance without a workspace', async () => {
    const user = userEvent.setup()
    const onNavigate = vi.fn()
    const onBack = vi.fn()
    const { client: api } = client()
    await act(async () => {
      root.render(
        <SettingsPage client={api} section="providers" onNavigate={onNavigate} onBack={onBack}
          theme="system" onThemeChange={() => {}} />,
      )
    })
    expect(screen.getByRole('main', { name: '设置' })).toBeDefined()
    expect(screen.getByRole('region', { name: '模型设置' })).toBeDefined()
    expect(screen.getByRole('button', { name: 'Provider 与模型' }).getAttribute('aria-current')).toBe('page')
    await user.click(screen.getByRole('button', { name: '外观' }))
    expect(onNavigate).toHaveBeenCalledWith('appearance')
    await act(async () => {
      root.render(
        <SettingsPage client={api} section="appearance" onNavigate={onNavigate} onBack={onBack}
          theme="system" onThemeChange={() => {}} />,
      )
    })
    expect(screen.getByRole('region', { name: '外观' })).toBeDefined()
    await act(async () => {
      root.render(
        <SettingsPage client={api} section="agents" onNavigate={onNavigate} onBack={onBack}
          theme="system" onThemeChange={() => {}} />,
      )
    })
    expect(screen.getByText(/请先打开一个工作区/)).toBeDefined()
    await user.click(screen.getByRole('button', { name: /返回对话/ }))
    expect(onBack).toHaveBeenCalled()
  })

  it('previews and persists theme changes, falling back when storage is invalid', async () => {
    const user = userEvent.setup()
    storage.setItem(THEME_STORAGE_KEY, 'not-a-theme')
    let theme: 'light' | 'dark' | 'system' = 'system'
    const onThemeChange = vi.fn((next: typeof theme) => { theme = next })
    const { client: api } = client()
    await act(async () => {
      root.render(
        <SettingsPage client={api} section="appearance" onNavigate={() => {}} onBack={() => {}}
          theme={theme} onThemeChange={value => {
            onThemeChange(value)
            document.documentElement.dataset.theme = value === 'system' ? undefined as unknown as string : value
            if (value === 'system') delete document.documentElement.dataset.theme
            else document.documentElement.dataset.theme = value
            storage.setItem(THEME_STORAGE_KEY, value)
          }} />,
      )
    })
    await user.click(screen.getByRole('button', { name: '深色' }))
    expect(onThemeChange).toHaveBeenCalledWith('dark')
    expect(localMemory.get(THEME_STORAGE_KEY)).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
  })

  it('shows agent project scope and browsing does not write', async () => {
    const { client: api, calls } = client()
    await act(async () => {
      root.render(
        <SettingsPage client={api} section="agents" workspaceId="ws_1" connection="live"
          onNavigate={() => {}} onBack={() => {}} theme="system" onThemeChange={() => {}} />,
      )
    })
    await waitFor(() => screen.getAllByText(/应用于项目 Demo 工程/).length > 0)
    expect(screen.getByText('General')).toBeDefined()
    expect(screen.queryByText(/浏览目录不产生物化写入/)).toBeNull()
    const mutating = calls.filter(call => call.method !== 'GET')
    expect(mutating).toEqual([])
  })

  it('copy and publish use the original commands', async () => {
    const user = userEvent.setup()
    const { client: api, calls } = client()
    await act(async () => {
      root.render(
        <SettingsPage client={api} section="agents" workspaceId="ws_1" connection="live"
          onNavigate={() => {}} onBack={() => {}} theme="system" onThemeChange={() => {}} />,
      )
    })
    await waitFor(() => screen.getByRole('button', { name: '复制为自定义' }))
    await user.click(screen.getByRole('button', { name: '复制为自定义' }))
    const name = screen.getByLabelText('自定义 Agent 名称') as HTMLInputElement
    if (!name.value) await user.type(name, 'General 自定义')
    await user.click(screen.getByRole('button', { name: '保存并启用' }))
    await waitFor(() => calls.some(call => call.url.includes('/quick-save') && call.method === 'POST'))
    await waitFor(() => screen.getByRole('button', { name: '发布 Version' }))
    await user.click(screen.getByRole('button', { name: '发布 Version' }))
    await waitFor(() => calls.some(call => call.url.includes('/publish') && call.method === 'POST'))
  })

  it('shows diagnostics health, in-place confirm, and advanced details without a JSON dump', async () => {
    const user = userEvent.setup()
    const { client: api } = client()
    await act(async () => {
      root.render(
        <SettingsPage client={api} section="diagnostics" connection="live"
          onNavigate={() => {}} onBack={() => {}} theme="system" onThemeChange={() => {}} />,
      )
    })
    await waitFor(() => screen.getByText('未在维护'))
    expect(screen.getAllByText(/整个数据根/).length).toBeGreaterThan(0)
    expect(screen.getByText(/ws_busy/)).toBeDefined()
    await user.click(screen.getByRole('button', { name: '创建完整备份' }))
    expect(screen.getByRole('dialog', { name: '确认维护操作' })).toBeDefined()
    expect(screen.getAllByText(/不是当前项目或当前会话/).length).toBeGreaterThan(0)
    expect(screen.queryByText(/Crash Report/i)).toBeNull()
    expect(container.querySelector('pre')).toBeNull()
    await user.click(screen.getByText('高级诊断'))
    await user.click(screen.getByRole('button', { name: '读取事件' }))
    await waitFor(() => screen.getByText('backup · 已完成'))
    expect(container.querySelector('pre')).toBeNull()
  })

  it('intercepts leaving an unsaved provider form and leaves after discard', async () => {
    const user = userEvent.setup()
    const { client: api } = client()
    const navigation = new NavigationStore('#/settings/providers')
    await act(async () => {
      root.render(
        <SettingsPage client={api} section="providers" workspaceId="ws_1"
          registerGuard={navigation.registerDirtyGuard}
          onNavigate={() => {}} onBack={() => {}} theme="system" onThemeChange={() => {}} />,
      )
    })
    await waitFor(() => screen.getByLabelText('demo 凭据'))
    await user.type(screen.getByLabelText('demo 凭据'), 'sk-test')
    const blocked = navigation.navigate({ kind: 'chat' })
    await waitFor(() => screen.getByRole('heading', { name: 'Provider 表单有未保存内容' }))
    await user.click(screen.getByRole('button', { name: '留在此页' }))
    expect(await blocked).toBe(false)
    expect(navigation.getState().location).toEqual({ kind: 'settings', section: 'providers' })
    const leaving = navigation.navigate({ kind: 'chat' })
    await waitFor(() => screen.getByRole('heading', { name: 'Provider 表单有未保存内容' }))
    await user.click(screen.getByRole('button', { name: '放弃修改' }))
    expect(await leaving).toBe(true)
    expect(navigation.getState().location).toEqual({ kind: 'chat' })
  })

  it('guides when a provider has no credential and does not fake availability', async () => {
    const { client: api } = client()
    await act(async () => {
      root.render(
        <SettingsPage client={api} section="providers" workspaceId="ws_1"
          onNavigate={() => {}} onBack={() => {}} theme="system" onThemeChange={() => {}} />,
      )
    })
    await waitFor(() => screen.getByText('待配置凭据'))
    expect(screen.getByRole('button', { name: '测试连接' })).toHaveProperty('disabled', true)
    expect(screen.getByRole('button', { name: '发现模型' })).toHaveProperty('disabled', true)
    expect(screen.queryByText('凭据已配置')).toBeNull()
  })

  it('writes workspace and global defaults without run identity or run-control requests', async () => {
    const user = userEvent.setup()
    const configured: ProviderSettingsView = {
      ...providerView,
      active_model: { provider_id: 'demo', model_id: 'local' },
      providers: [{
        ...providerView.providers[0]!,
        credential_configured: true,
        models: [{
          model_id: 'local',
          api_model_id: 'vendor',
          capabilities: null,
          effective_capabilities: { reasoning_efforts: ['low'], input_types: ['text'], tool_protocol: 'none' },
        }],
      }],
    }
    const calls: { url: string; method: string; body: unknown }[] = []
    const fetchImpl: typeof fetch = async (input, init) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      const body = init?.body ? JSON.parse(String(init.body)) : null
      calls.push({ url, method, body })
      if (url.split('?')[0] === '/v1/workspaces') {
        return json({ items: [{ workspace_id: 'ws_1', display_name: 'Demo 工程', path: '/tmp', available: true, last_used_at: null, git_root: null }], revision: 1 })
      }
      if (url.includes('/providers') && !url.includes('/catalog/providers')) return json(configured)
      if (url.includes('/settings')) return json(chatSettings)
      return json({})
    }
    const api = new ApiClient({ baseUrl: '', token: '', fetchImpl, workspaceId: 'ws_1' })
    await act(async () => {
      root.render(
        <SettingsPage client={api} section="providers" workspaceId="ws_1" sessionId="se_1"
          onNavigate={() => {}} onBack={() => {}} theme="system" onThemeChange={() => {}} />,
      )
    })
    await waitFor(() => screen.getByRole('button', { name: '将当前生效设置设为项目默认' }))
    expect(screen.getByText(/用于新对话/)).toBeDefined()
    await user.click(screen.getByRole('button', { name: '将当前生效设置设为项目默认' }))
    await waitFor(() => calls.some(call => call.method === 'POST' && call.url.includes('/settings')))
    await user.click(screen.getByRole('button', { name: '将当前生效设置设为全局对话默认' }))
    await waitFor(() => calls.filter(call => call.method === 'POST' && call.url.includes('/settings')).length >= 2)
    const settingWrites = calls.filter(call => call.method === 'POST' && call.url.includes('/settings'))
    expect(settingWrites.map(call => call.url)).toEqual([
      '/v1/workspaces/ws_1/sessions/se_1/settings',
      '/v1/workspaces/ws_1/sessions/se_1/settings',
    ])
    expect(settingWrites.map(call => (call.body as { scope: string }).scope)).toEqual(['workspace', 'global'])
    for (const call of settingWrites) {
      const body = call.body as Record<string, unknown>
      expect(Object.keys(body).sort()).toEqual(['expected_revision', 'scope', 'settings'])
      expect(body).not.toHaveProperty('run_id')
      expect(body).not.toHaveProperty('agent_run_id')
      expect(body).not.toHaveProperty('task_run_id')
      expect(body).not.toHaveProperty('workflow_run_id')
    }
    expect(calls.some(call => /workflow-runs|agent-runs|\/pause|\/resume/.test(call.url))).toBe(false)
  })

  it('reloads agent presets for the next workspace without flashing the previous catalog', async () => {
    let releaseSecond: ((value: Response) => void) | undefined
    const secondPending = new Promise<Response>(resolve => { releaseSecond = resolve })
    const firstPresets = presets
    const secondPresets: AgentPresetCatalogWire = {
      presets: [
        { definition_id: 'builtin_explore', role: 'explore', name: 'Explore Two', description: '第二项目探查', access: 'read', available: true, preference: null, preference_source: 'inherit_session' },
      ],
      revision: 4,
    }
    const calls: { url: string; method: string }[] = []
    const fetchFor = (workspaceId: string, presetsResponse: Promise<Response> | Response): typeof fetch => async (input, init) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      calls.push({ url, method })
      const path = url.split('?')[0]
      if (path === '/v1/workspaces') {
        return json({ items: [
          { workspace_id: 'ws_1', display_name: 'Demo 工程', path: '/tmp', available: true, last_used_at: null, git_root: null },
          { workspace_id: 'ws_2', display_name: '第二工程', path: '/tmp2', available: true, last_used_at: null, git_root: null },
        ], revision: 1 })
      }
      if (url.includes('/agent-presets')) {
        expect(url).toContain(`/v1/workspaces/${workspaceId}/agent-presets`)
        return presetsResponse
      }
      if (url.includes('/catalog/agent-definitions')) return json({ agent_definitions: [] })
      if (url.includes('/catalog/providers')) return json({ providers: [], active_model: null })
      if (url.includes('/catalog/skills')) return json({ skills: [] })
      if (url.includes('/catalog/tools')) return json({ tools: [{ name: 'read', description: 'read' }] })
      if (url.includes('/catalog/artifact-contracts')) return json({ contracts: [] })
      return json({})
    }
    const first = new ApiClient({ baseUrl: '', token: '', fetchImpl: fetchFor('ws_1', json(firstPresets)), workspaceId: 'ws_1' })
    await act(async () => {
      root.render(
        <SettingsPage client={first} section="agents" workspaceId="ws_1" connection="live"
          onNavigate={() => {}} onBack={() => {}} theme="system" onThemeChange={() => {}} />,
      )
    })
    await waitFor(() => screen.getByText('General'))
    expect(screen.getAllByText(/应用于项目 Demo 工程/).length).toBeGreaterThan(0)

    const second = new ApiClient({ baseUrl: '', token: '', fetchImpl: fetchFor('ws_2', secondPending), workspaceId: 'ws_2' })
    await act(async () => {
      root.render(
        <SettingsPage client={second} section="agents" workspaceId="ws_2" connection="live"
          onNavigate={() => {}} onBack={() => {}} theme="system" onThemeChange={() => {}} />,
      )
    })
    expect(screen.queryByText('General')).toBeNull()
    expect(screen.getByText('正在读取 Agent 目录…')).toBeDefined()
    expect(screen.getAllByText(/应用于项目 (第二工程|ws_2)/).length).toBeGreaterThan(0)
    await act(async () => {
      releaseSecond?.(json(secondPresets))
    })
    await waitFor(() => screen.getByText('Explore Two'))
    expect(screen.queryByText('General')).toBeNull()
    expect(calls.filter(call => call.url.includes('/agent-presets')).every(call => call.method === 'GET')).toBe(true)
  })

  it('keeps credential input out of web storage, console, and the allowlisted key set', async () => {
    const user = userEvent.setup()
    const secret = 'sk-test-credential-never-store'
    const leaked: string[] = []
    const tap = (level: string) => (...args: unknown[]) => {
      leaked.push(`${level}:${args.map(value => typeof value === 'string' ? value : JSON.stringify(value)).join(' ')}`)
    }
    vi.spyOn(console, 'log').mockImplementation(tap('log'))
    vi.spyOn(console, 'info').mockImplementation(tap('info'))
    vi.spyOn(console, 'debug').mockImplementation(tap('debug'))
    vi.spyOn(console, 'warn').mockImplementation(tap('warn'))
    vi.spyOn(console, 'error').mockImplementation(tap('error'))
    const { client: api } = client()
    await act(async () => {
      root.render(
        <SettingsPage client={api} section="providers" workspaceId="ws_1"
          onNavigate={() => {}} onBack={() => {}} theme="system" onThemeChange={() => {}} />,
      )
    })
    await waitFor(() => screen.getByLabelText('demo 凭据'))
    await user.type(screen.getByLabelText('demo 凭据'), secret)
    const stored = [...localMemory.entries(), ...sessionMemory.entries()]
    expect(stored.map(([key]) => key).every(key => (
      key === THEME_STORAGE_KEY
      || key === 'morrow.sidebar.collapsed.v1'
      || key === 'morrow.chatDrafts.v1'
      || key === 'morrow.chatOutbox.v1'
      || key === 'morrow.attachments.v1'
      || key === 'morrow.workspace'
      || key.startsWith('morrow.selected.')
      || key.startsWith('morrow.inspector.v1.')
      || key.startsWith('morrow.editor.draft.')
    ) || stored.length === 0)).toBe(true)
    expect(stored.some(([, value]) => value.includes(secret))).toBe(false)
    expect(leaked.some(line => line.includes(secret))).toBe(false)
    expect((screen.getByLabelText('demo 凭据') as HTMLInputElement).type).toBe('password')
  })
})
