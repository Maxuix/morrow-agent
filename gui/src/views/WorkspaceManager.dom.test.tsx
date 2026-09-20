// @vitest-environment jsdom
import { createRoot, type Root } from 'react-dom/client'
import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient, WorkspaceEntry, WorkspaceList } from '../api/client'
import { WorkspaceManager } from './WorkspaceManager'

function entry(id: string, name: string): WorkspaceEntry {
  return { workspace_id: id, path: `/tmp/${id}`, display_name: name, available: true, last_used_at: null, git_root: null }
}

const LIST: WorkspaceList = { items: [entry('ws_a', 'A'), entry('ws_b', 'B')], revision: 1 }

interface Deferred<T> {
  promise: Promise<T>
  resolve: (value: T) => void
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}

const flush = async () => { await act(async () => { await Promise.resolve() }) }

describe('WorkspaceManager workspace switch race', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    // jsdom lacks the dialog API used by WorkspaceManagerDialog.
    if (typeof HTMLDialogElement.prototype.showModal !== 'function') {
      HTMLDialogElement.prototype.showModal = function showModal() {}
      HTMLDialogElement.prototype.close = function close() {}
    }
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => root.unmount())
    container.remove()
  })

  it('lets the newest click win when two choose chains overlap', async () => {
    const manages: Record<string, Deferred<unknown>> = { ws_a: deferred(), ws_b: deferred() }
    const client = {
      workspaces: async () => LIST,
      manageWorkspace: (id: string) => manages[id].promise,
    } as unknown as ApiClient
    const onSelect = vi.fn()
    act(() => {
      root.render(<WorkspaceManager client={client} selected={null} onSelect={onSelect} />)
    })
    const select = await screen.findByLabelText('选择工作区') as HTMLSelectElement
    // Two rapid changes before either manage call settles.
    act(() => { fireEvent.change(select, { target: { value: 'ws_a' } }) })
    act(() => { fireEvent.change(select, { target: { value: 'ws_b' } }) })
    expect(select.disabled).toBe(true)
    // B settles first, A last; the stale A chain must not commit its choice.
    await act(async () => { manages.ws_b.resolve({ workspace: entry('ws_b', 'B') }) })
    await flush()
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenLastCalledWith('ws_b')
    await act(async () => { manages.ws_a.resolve({ workspace: entry('ws_a', 'A') }) })
    await flush()
    expect(onSelect).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(select.disabled).toBe(false))
  })
})
