// @vitest-environment jsdom
import { createRoot, type Root } from 'react-dom/client'
import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

/** Empty-registration backend: no workspaces, provider settings readable. */
function stubFetch() {
  const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/v1/workspaces')) {
      return new Response(JSON.stringify({ items: [], revision: 0 }), { status: 200 })
    }
    if (url.includes('/v1/providers')) {
      return new Response(
        JSON.stringify({ revision: 1, active_model: null, adapters: [], presets: [], providers: [] }),
        { status: 200 },
      )
    }
    return new Response(JSON.stringify({ detail: 'not found' }), { status: 404 })
  })
  vi.stubGlobal('fetch', fetchImpl)
  return fetchImpl
}

describe('App without a workspace (N01)', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    // jsdom lacks the dialog API used by WorkspaceManagerDialog.
    if (typeof HTMLDialogElement.prototype.showModal !== 'function') {
      HTMLDialogElement.prototype.showModal = function showModal() {}
      HTMLDialogElement.prototype.close = function close() {}
    }
    window.location.hash = ''
    sessionStorage.clear()
    stubFetch()
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => root.unmount())
    container.remove()
    vi.unstubAllGlobals()
    window.location.hash = ''
  })

  it('reaches provider settings from the no-workspace view and returns to chat', async () => {
    const user = userEvent.setup()
    act(() => {
      root.render(<App />)
    })
    // No registered workspace: the app offers the manager hint plus 模型设置.
    await waitFor(() => screen.getByText(/尚未选择工作区/))
    expect(screen.queryByRole('main', { name: '模型设置' })).toBeNull()

    await user.click(screen.getByRole('button', { name: '模型设置' }))
    await waitFor(() => screen.getByRole('main', { name: '设置' }))
    expect(screen.getByRole('region', { name: '模型设置' })).toBeDefined()
    expect(screen.getByRole('heading', { name: 'Provider 与模型' })).toBeDefined()
    expect(window.location.hash).toBe('#/settings/providers')
    // The settings pane replaces the workspace prompt while it is open.
    expect(screen.queryByText(/尚未选择工作区/)).toBeNull()
    await user.click(screen.getByRole('button', { name: '外观' }))
    await waitFor(() => screen.getByRole('region', { name: '外观' }))

    await user.click(screen.getByRole('button', { name: /返回对话/ }))
    await waitFor(() => screen.getByText(/尚未选择工作区/))
    expect(screen.queryByRole('region', { name: '模型设置' })).toBeNull()
    expect(window.location.hash).toBe('#/chat')
  })

  it('opens settings directly from a deep link without any workspace', async () => {
    window.location.hash = '#/settings/providers'
    act(() => {
      root.render(<App />)
    })
    await waitFor(() => screen.getByRole('main', { name: '设置' }))
    expect(screen.getByRole('region', { name: '模型设置' })).toBeDefined()
    expect(screen.getByRole('button', { name: /返回对话/ })).toBeDefined()
  })
})
