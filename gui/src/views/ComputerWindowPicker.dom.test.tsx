// @vitest-environment jsdom
import { useState } from 'react'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import type { ApiClient } from '../api/client'
import type { ComputerUseSettingsView, ComputerWindowSelection } from '../api/settings'
import { ComputerWindowPicker } from './ComputerWindowPicker'

afterEach(() => {cleanup(); vi.restoreAllMocks(); vi.useRealTimers()})
const candidate = {candidate_id: 'ccandidate_one', app: {bundle_id: 'com.example.Editor'}, display_label: '编辑器'}
const view: ComputerUseSettingsView = {
  revision: 1, settings: {enabled: true, mode: 'semantic', max_operations: 20,
    max_run_seconds: 120, max_call_seconds: 7, max_observation_bytes: 4096, image_long_edge_px: 100},
  host: {status: 'unavailable', reason: 'native_unverified'}, model: null,
  model_capabilities: {function_tools: true, images: false}, model_error: null,
  required_permission: 'full-access-manual', configuration_scope: 'global', applies_to: 'future_runs',
}
function fixture() {
  const expires_at = new Date(Date.now() + 30_000).toISOString()
  const selection: ComputerWindowSelection = {selection_id: 'cselection_one', expires_at, windows: [candidate],
    operations: ['observe'], delivery: 'background', image_share: 'none', applies_to: 'one_future_run'}
  const client = {computerWindowCandidates: vi.fn(async () => ({candidates: [candidate], expires_at})),
    selectComputerWindows: vi.fn(async () => selection)}
  function Harness({session = 's', current = view, permission = 'full-access-manual'}: {
    session?: string; current?: ComputerUseSettingsView; permission?: string
  }) {
    const [selected, setSelected] = useState<ComputerWindowSelection | null>(null)
    return <ComputerWindowPicker client={client as unknown as ApiClient} workspace="ws" session={session}
      view={current} permission={permission} selection={selected} onChange={setSelected}/>
  }
  return {client, Harness, selection}
}

it('reads only on request and binds explicit windows, delivery and optional capabilities', async () => {
  const user = userEvent.setup()
  const {client, Harness} = fixture()
  render(<Harness/> )
  expect(client.computerWindowCandidates).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', {name: '读取本地窗口'}))
  await user.click(await screen.findByRole('checkbox', {name: '编辑器'}))
  expect((screen.getByRole('checkbox', {name: '允许操作选中窗口'}) as HTMLInputElement).checked).toBe(false)
  expect((screen.getByRole('checkbox', {name: '分享受控窗口图像'}) as HTMLInputElement).disabled).toBe(true)
  expect((screen.getByRole('button', {name: '用于下次运行'}) as HTMLButtonElement).disabled).toBe(true)
  await user.selectOptions(screen.getByRole('combobox', {name: '桌面投递方式'}), 'background')
  await user.click(screen.getByRole('button', {name: '用于下次运行'}))
  await screen.findByText('下次普通对话运行使用：编辑器')
  expect(client.selectComputerWindows).toHaveBeenCalledWith('ws', 's', {
    candidate_ids: ['ccandidate_one'], allow_action: false, share_images: false, delivery: 'background',
  })
  expect(screen.queryByText('cselection_one')).toBeNull()
  expect(screen.getByText(/仅观察 · 后台投递 · 不分享图像/)).toBeDefined()
})

it('gates permission and requires explicit action and image choices', async () => {
  const user = userEvent.setup()
  const {client, Harness} = fixture()
  const ui = render(<Harness permission="manual"/> )
  expect((screen.getByRole('button', {name: '读取本地窗口'}) as HTMLButtonElement).disabled).toBe(true)
  ui.rerender(<Harness current={{...view, settings: {...view.settings, mode: 'hybrid'}, model_capabilities: {function_tools: true, images: true}}}/> )
  await user.click(screen.getByRole('button', {name: '读取本地窗口'}))
  await user.click(await screen.findByRole('checkbox', {name: '编辑器'}))
  await user.click(screen.getByRole('checkbox', {name: '允许操作选中窗口'}))
  await user.click(screen.getByRole('checkbox', {name: '分享受控窗口图像'}))
  await user.selectOptions(screen.getByRole('combobox', {name: '桌面投递方式'}), 'foreground')
  await user.click(screen.getByRole('button', {name: '用于下次运行'}))
  expect(client.selectComputerWindows).toHaveBeenCalledWith('ws', 's', {
    candidate_ids: ['ccandidate_one'], allow_action: true, share_images: true, delivery: 'foreground',
  })
})

it('discards a late selection after a session switch and clears a selected scope on settings change', async () => {
  const user = userEvent.setup()
  const {client, Harness, selection} = fixture()
  let resolve!: (value: ComputerWindowSelection) => void
  client.selectComputerWindows.mockImplementationOnce(() => new Promise(done => {resolve = done}))
  const ui = render(<Harness/> )
  await user.click(screen.getByRole('button', {name: '读取本地窗口'}))
  await user.click(await screen.findByRole('checkbox', {name: '编辑器'}))
  await user.selectOptions(screen.getByRole('combobox', {name: '桌面投递方式'}), 'background')
  await user.click(screen.getByRole('button', {name: '用于下次运行'}))
  ui.rerender(<Harness session="new"/> )
  await act(async () => resolve(selection))
  expect(screen.queryByText(/下次普通对话运行使用/)).toBeNull()
  await user.click(screen.getByRole('button', {name: '读取本地窗口'}))
  await user.click(await screen.findByRole('checkbox', {name: '编辑器'}))
  await user.selectOptions(screen.getByRole('combobox', {name: '桌面投递方式'}), 'background')
  await user.click(screen.getByRole('button', {name: '用于下次运行'}))
  await screen.findByText(/下次普通对话运行使用/)
  ui.rerender(<Harness session="new" current={{...view, revision: 2}}/> )
  await waitFor(() => expect(screen.queryByText(/下次普通对话运行使用/)).toBeNull())
})

it('keeps an expired selection visible until an explicit clear or refresh', async () => {
  const {client, selection} = fixture()
  let clock = Date.now()
  vi.spyOn(Date, 'now').mockImplementation(() => clock)
  render(<ComputerWindowPicker client={client as unknown as ApiClient} workspace="ws" session="s" view={view}
    permission="full-access-manual" selection={selection} onChange={vi.fn()}/> )
  clock += 31_000
  act(() => window.dispatchEvent(new Event('focus')))
  expect(screen.getByRole('alert').textContent).toContain('已过期')
  expect(screen.getByText(/下次普通对话运行使用/)).toBeDefined()
  expect(client.computerWindowCandidates).not.toHaveBeenCalled()
})
