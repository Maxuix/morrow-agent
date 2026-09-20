// @vitest-environment jsdom
import { createRoot, type Root } from 'react-dom/client'
import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatModelControl, type ChatSettingsState } from './ChatSettingsBar'
import type { ChatSettings, ChatSettingsView, ProviderSettingsView } from '../api/settings'

const catalog: ProviderSettingsView = {
  revision: 1,
  active_model: { provider_id: 'demo', model_id: 'local' },
  adapters: [],
  presets: [],
  providers: [{
    provider_id: 'demo',
    adapter: 'fake',
    base_url: 'https://example.test',
    credential_configured: true,
    last_test: null,
    models: [{ model_id: 'local', api_model_id: 'vendor', capabilities: null, effective_capabilities: { reasoning_efforts: ['low', 'high'], input_types: ['text'], tool_protocol: 'none' } }],
  }],
}

const value: ChatSettingsView = {
  permission_presets: [{ preset: 'manual', label: '手动', available: true, reason: null }],
  documents: {
    session: { revision: 1, settings: { model: { provider_id: 'demo', model_id: 'local' }, generation: { reasoning_effort: 'low' }, permission: 'manual' } },
    workspace: { revision: 1, settings: { model: null, generation: null, permission: null } },
    global: { revision: 1, settings: { model: { provider_id: 'demo', model_id: 'local' }, generation: null, permission: null } },
  },
  effective: { model: { provider_id: 'demo', model_id: 'local' }, generation: { reasoning_effort: 'low' }, permission: 'manual' },
  sources: {
    model: { scope: 'session', revision: 1 },
    generation: { scope: 'session', revision: 1 },
    permission: { scope: 'session', revision: 1 },
  },
}

function settings(overrides: Partial<ChatSettingsState> = {}): ChatSettingsState {
  return {
    value,
    catalog,
    busy: false,
    error: '',
    ready: true,
    active: false,
    model: value.effective.model,
    efforts: ['low', 'high'],
    effort: 'low',
    permission: 'manual',
    permissionPresets: value.permission_presets,
    save: vi.fn(async () => {}),
    ...overrides,
  }
}

describe('ChatModelControl session-only composer', () => {
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

  it('keeps session model/thinking and inheritance, and links to settings', async () => {
    const user = userEvent.setup()
    const onOpenSettings = vi.fn()
    const state = settings()
    await act(async () => {
      root.render(<ChatModelControl settings={state} onOpenSettings={onOpenSettings} />)
    })
    await user.click(screen.getByTitle('模型与思考程度'))
    expect(screen.getByLabelText('会话模型')).toBeDefined()
    expect(screen.getByLabelText('思考程度')).toBeDefined()
    expect(screen.getByText(/模型来源：当前会话/)).toBeDefined()
    expect(screen.getByRole('button', { name: '使用继承设置' })).toBeDefined()
    expect(screen.queryByRole('button', { name: '管理 Provider' })).toBeNull()
    expect(screen.queryByRole('button', { name: '设为工作区默认' })).toBeNull()
    expect(screen.queryByRole('button', { name: '设为全局默认' })).toBeNull()
    await user.click(screen.getByRole('button', { name: '打开 Provider 设置' }))
    expect(onOpenSettings).toHaveBeenCalledTimes(1)
  })

  it('does not present missing credentials or unavailable models as selectable', async () => {
    const user = userEvent.setup()
    const save = vi.fn(async () => {})
    const missing = {
      ...catalog,
      providers: [{
        ...catalog.providers[0]!,
        credential_configured: false,
        models: [
          catalog.providers[0]!.models[0]!,
          { model_id: 'other', api_model_id: 'other', capabilities: null },
        ],
      }],
    }
    const state = settings({
      catalog: missing,
      ready: false,
      save,
      model: { provider_id: 'gone', model_id: 'retired' },
    })
    await act(async () => {
      root.render(<ChatModelControl settings={state} onOpenSettings={() => {}} />)
    })
    expect(screen.getByLabelText('模型配置不可用')).toBeDefined()
    await user.click(screen.getByTitle('模型与思考程度'))
    const sessionModel = screen.getByLabelText('会话模型') as HTMLSelectElement
    const unavailable = [...sessionModel.options].find(option => option.textContent?.includes('已不可用'))
    expect(unavailable?.disabled).toBe(true)
    expect(unavailable?.textContent).toContain('gone/retired')
    const missingCredential = [...sessionModel.options].find(option => option.textContent?.includes('缺少凭据'))
    expect(missingCredential?.disabled).toBe(true)
    expect(missingCredential?.textContent).toContain('local')
    expect(screen.queryByRole('option', { name: /^local$/ })).toBeNull()
    expect(save).not.toHaveBeenCalled()
  })

  it('keeps an empty catalog honest and does not invent a usable model', async () => {
    const user = userEvent.setup()
    const state = settings({
      catalog: { ...catalog, providers: [], active_model: null },
      model: null,
      ready: false,
      value: {
        ...value,
        effective: { ...value.effective, model: null },
      },
    })
    await act(async () => {
      root.render(<ChatModelControl settings={state} onOpenSettings={() => {}} />)
    })
    expect(screen.getAllByText('选择模型').length).toBeGreaterThan(0)
    expect(screen.getByLabelText('模型配置不可用')).toBeDefined()
    await user.click(screen.getByTitle('模型与思考程度'))
    const sessionModel = screen.getByLabelText('会话模型') as HTMLSelectElement
    const enabled = [...sessionModel.options].filter(option => !option.disabled && option.value !== '')
    expect(enabled).toEqual([])
  })

  it('saves only session chat settings and notes that a live run stays frozen', async () => {
    const user = userEvent.setup()
    const save = vi.fn(async (_settings: ChatSettings, _scope?: 'session' | 'workspace' | 'global') => {})
    const state = settings({ save, active: true })
    await act(async () => {
      root.render(<ChatModelControl settings={state} onOpenSettings={() => {}} />)
    })
    await user.click(screen.getByTitle('模型与思考程度'))
    expect(screen.getByText(/更改下次运行生效；已提交输入保留原设置/)).toBeDefined()
    await user.click(screen.getByRole('button', { name: '使用继承设置' }))
    expect(save).toHaveBeenCalledTimes(1)
    expect(save).toHaveBeenCalledWith({ model: null, generation: null, permission: null })
    const payload = save.mock.calls[0][0]
    expect(payload).not.toHaveProperty('run_id')
    expect(payload).not.toHaveProperty('agent_run_id')
    expect(payload).not.toHaveProperty('task_run_id')
    expect(payload).not.toHaveProperty('workflow_run_id')
  })
})
