// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import type { ApiClient } from '../api/client'
import type { ComputerUseSettingsView } from '../api/settings'
import { ComputerUseControl } from './ComputerUseControl'

afterEach(cleanup)
const initial: ComputerUseSettingsView = {
  revision: 7,
  settings: {enabled: false, mode: 'semantic', max_operations: 20, max_run_seconds: 120,
    max_call_seconds: 7, max_observation_bytes: 4096, image_long_edge_px: 100},
  host: {status: 'unavailable', reason: 'disabled'}, model: null,
  model_capabilities: {function_tools: true, images: false}, model_error: null,
  required_permission: 'full-access-manual', configuration_scope: 'global', applies_to: 'future_runs',
}

it('saves the global revision and preserves budgets, without granting a run', async () => {
  const user = userEvent.setup()
  const next = {...initial, revision: 8, settings: {...initial.settings, enabled: true},
    host: {status: 'unavailable' as const, reason: 'native_unverified'}}
  let resolve!: (value: ComputerUseSettingsView) => void
  const client = {computerUseSettings: vi.fn(async () => initial),
    saveComputerUseSettings: vi.fn(() => new Promise<ComputerUseSettingsView>(done => {resolve = done}))}
  render(<ComputerUseControl client={client as unknown as ApiClient} workspace="ws" session="s"/> )
  const checkbox = await screen.findByRole('checkbox', {name: '启用桌面功能（全局）'})
  await user.click(checkbox)
  expect(client.saveComputerUseSettings).toHaveBeenCalledWith('ws', 's', next.settings, 7)
  act(() => window.dispatchEvent(new Event('focus')))
  expect(client.computerUseSettings).toHaveBeenCalledTimes(1)
  await act(async () => resolve(next))
  expect(screen.getByRole('status').textContent).toContain('原生验收未通过')
  expect((checkbox as HTMLInputElement).disabled).toBe(false)
  expect(screen.getByText(/独立桌面授权/)).toBeDefined()
})

it('refreshes a conflicting global revision and shows exact-model limitations', async () => {
  const user = userEvent.setup()
  const next = {...initial, revision: 9, settings: {...initial.settings, mode: 'hybrid' as const},
    model_error: 'images_not_supported' as const}
  const client = {computerUseSettings: vi.fn().mockResolvedValueOnce(initial).mockResolvedValue(next),
    saveComputerUseSettings: vi.fn(async () => {throw new Error('全局设置已变化')})}
  render(<ComputerUseControl client={client as unknown as ApiClient} workspace="ws" session="s"/> )
  const mode = await screen.findByRole('combobox', {name: '桌面观察方式'})
  await user.selectOptions(mode, 'hybrid')
  await waitFor(() => expect((mode as HTMLSelectElement).value).toBe('hybrid'))
  expect(screen.getByRole('alert').textContent).toContain('全局设置已变化')
  expect(screen.getByText(/当前模型不支持图像/)).toBeDefined()
  expect(client.computerUseSettings).toHaveBeenCalledTimes(2)
})

it('discards late status from an old session and refreshes when the model changes', async () => {
  let old!: (value: ComputerUseSettingsView) => void
  const client = {computerUseSettings: vi.fn().mockImplementationOnce(() => new Promise(done => {old = done}))
    .mockResolvedValue({...initial, host: {status: 'unavailable', reason: 'sdk_missing'}})}
  const view = render(<ComputerUseControl client={client as unknown as ApiClient} workspace="ws" session="old" modelKey="a"/> )
  view.rerender(<ComputerUseControl client={client as unknown as ApiClient} workspace="ws" session="new" modelKey="a"/> )
  await screen.findByText('宿主：未安装桌面组件')
  await act(async () => old(initial))
  expect(screen.queryByText('宿主：已关闭')).toBeNull()
  view.rerender(<ComputerUseControl client={client as unknown as ApiClient} workspace="ws" session="new" modelKey="b"/> )
  await waitFor(() => expect(client.computerUseSettings).toHaveBeenCalledTimes(3))
})
