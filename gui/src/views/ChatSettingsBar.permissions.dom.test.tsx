// @vitest-environment jsdom
import { createRoot, type Root } from 'react-dom/client'
import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../api/client'
import type { ChatPermissionsView, ChatSettingsView, ProviderSettingsView } from '../api/settings'
import { ChatPermissionControl, type ChatSettingsState } from './ChatSettingsBar'

const settingsView: ChatSettingsView = {
  permission_presets: [
    {preset: 'manual', label: '手动', available: true, reason: null},
    {preset: 'full-access-manual', label: '完整访问（逐次确认）', available: true, reason: null},
  ],
  documents: {
    session: {revision: 1, settings: {model: null, generation: null, permission: 'full-access-manual'}},
    workspace: {revision: 1, settings: {model: null, generation: null, permission: null}},
    global: {revision: 1, settings: {model: null, generation: null, permission: null}},
  },
  effective: {model: null, generation: null, permission: 'full-access-manual'},
  sources: {
    model: {scope: 'adapter', revision: 1}, generation: {scope: 'adapter', revision: 1},
    permission: {scope: 'session', revision: 1},
  },
}

const baseState = (): ChatSettingsState => ({
  value: settingsView, catalog: {} as ProviderSettingsView, busy: false, error: '', ready: true,
  active: false, model: null, efforts: [], effort: '', permission: 'full-access-manual',
  permissionPresets: settingsView.permission_presets, save: vi.fn(async () => {}),
})

const permissions: ChatPermissionsView = {
  run_ids: ['run_secret'], next_run_cursor: null, snapshot: null,
  grants: [{grant_id: 'grant_secret', row_version: 3, capabilities: ['host.process'], expires_at: '2099-01-01T00:00:00Z', status: 'active'}],
  next_grant_cursor: null,
  session_scopes: {items: [{scope: 'session:workspace_write', revision: 7, approval_id: 'approval_secret'}], next_cursor: null},
}

function clientWithPermissions() {
  return {
    chatPermissions: vi.fn(async () => permissions),
    revokeChatPermission: vi.fn(async () => ({disposition: 'applied'})),
  } as unknown as ApiClient & {
    chatPermissions: ReturnType<typeof vi.fn>
    revokeChatPermission: ReturnType<typeof vi.fn>
  }
}

describe('ChatPermissionControl authorization popover', () => {
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

  it('loads current run/session receipts in the composer and revokes without exposing IDs', async () => {
    const user = userEvent.setup()
    const client = clientWithPermissions()
    const onHostAllowedChange = vi.fn()
    await act(async () => {
      root.render(<ChatPermissionControl settings={baseState()} client={client} workspace="ws" session="session_1"
        hostAllowed={false} onHostAllowedChange={onHostAllowedChange}/>)
    })
    await user.click(screen.getByTitle('会话权限预设与授权'))
    await waitFor(() => expect(screen.getByText('当前运行授权')).toBeDefined())
    expect(screen.getByText(/host\.process · 到期/)).toBeDefined()
    expect(screen.getByText('工作区写入')).toBeDefined()
    expect(container.textContent).not.toContain('grant_secret')
    expect(container.textContent).not.toContain('approval_secret')
    await user.click(screen.getByRole('checkbox', {name: '允许本次运行使用 Host'}))
    expect(onHostAllowedChange).toHaveBeenCalledWith(true)
    await user.click(screen.getAllByRole('button', {name: '撤销此授权'})[0]!)
    await waitFor(() => expect(client.revokeChatPermission).toHaveBeenCalledTimes(1))
    expect(client.revokeChatPermission.mock.calls[0][2]).toMatchObject({kind: 'grant', subject_id: 'grant_secret', expected_revision: 3})
  })

  it('opens and refreshes from the /grant composer command focus signal', async () => {
    const client = clientWithPermissions()
    await act(async () => {
      root.render(<ChatPermissionControl settings={baseState()} client={client} workspace="ws" session="session_1" focusSignal={1}/>)
    })
    await waitFor(() => expect((screen.getByTitle('会话权限预设与授权').parentElement as HTMLDetailsElement).open).toBe(true))
    expect(client.chatPermissions).toHaveBeenCalled()
  })
  it('shows frozen desktop scope separately from Shell Host and revokes its own receipt', async () => {
    const user = userEvent.setup()
    const client = clientWithPermissions()
    client.chatPermissions.mockResolvedValue({...permissions, grants: [
      {grant_id: 'desktop_private', row_version: 4, capabilities: ['computer_use_host'],
        expires_at: '2099-01-01T00:00:00Z', status: 'active', computer_use: {
          apps: ['com.example.Notes'], window_scope: 'selected_windows', window_count: 2,
          operations: ['observe'], delivery: 'background', image_share: 'none',
        }},
      {grant_id: 'shell_private', row_version: 2, capabilities: ['unconfined_host_process'],
        expires_at: '2099-01-01T00:00:00Z', status: 'active'},
    ]})
    await act(async () => root.render(<ChatPermissionControl settings={baseState()} client={client} workspace="ws" session="session_1"/>))
    await user.click(screen.getByTitle('会话权限预设与授权'))
    await screen.findByText(/桌面窗口 · 到期/)
    expect(screen.getByText(/Shell Host · 到期/)).toBeDefined()
    expect(screen.getByText(/com.example.Notes · 2 个选中窗口/).textContent).toContain('仅观察')
    expect(screen.getByText(/com.example.Notes · 2 个选中窗口/).textContent).toContain('后台投递')
    expect(container.textContent).not.toContain('desktop_private')
    expect(container.textContent).not.toContain('shell_private')
    await user.click(screen.getAllByRole('button', {name: '撤销此授权'})[0]!)
    await waitFor(() => expect(client.revokeChatPermission).toHaveBeenCalledTimes(1))
    expect(client.revokeChatPermission.mock.calls[0][2]).toMatchObject({kind: 'grant', subject_id: 'desktop_private', expected_revision: 4})
  })

  it('refreshes settling state without hiding unknown desktop effects', async () => {
    const user = userEvent.setup()
    const client = clientWithPermissions()
    client.chatPermissions.mockResolvedValue({...permissions, desktop_runtime: {
      scope: 'local_host', state: 'quarantined', native_pending: true, unknown_actions: 1,
    }})
    await act(async () => root.render(<ChatPermissionControl settings={baseState()} client={client} workspace="ws" session="session_1"/>))
    await user.click(screen.getByTitle('会话权限预设与授权'))
    await screen.findByText(/原生调用尚未停稳/)
    client.chatPermissions.mockResolvedValue({...permissions, desktop_runtime: {
      scope: 'local_host', state: 'idle', native_pending: false, unknown_actions: 1,
    }})
    await user.click(screen.getByRole('button', {name: '刷新授权状态'}))
    await screen.findByText(/当前无桌面会话/)
    expect(screen.queryByText(/原生调用尚未停稳/)).toBeNull()
    expect(screen.getByRole('alert').textContent).toContain('效果未知')
    expect(client.chatPermissions.mock.calls.every(call => call[0] === 'ws' && call[1] === 'session_1')).toBe(true)
  })

})
