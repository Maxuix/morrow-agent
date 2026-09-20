// @vitest-environment jsdom
import { act } from '@testing-library/react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../api/client'
import type { RunViewWire } from '../api/types'
import type { SyncStore } from './sync'
import { useRunView } from './runView'

function projection(rowVersion: number) {
  return {
    run: { row_version: rowVersion, status: 'running' },
    view: { nodes: [] },
  }
}

function makeHarness() {
  let current = { workflowRuns: new Map([['wrun_1', projection(1)]]) }
  const listeners = new Set<() => void>()
  let subscriptions = 0
  const store = {
    getState: () => current,
    subscribe: (listener: () => void) => {
      subscriptions += 1
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    update(rowVersion: number) {
      current = { workflowRuns: new Map([['wrun_1', projection(rowVersion)]]) }
      listeners.forEach(listener => listener())
    },
    get subscriptionCount() { return subscriptions },
  } as unknown as SyncStore & { update: (rowVersion: number) => void; subscriptionCount: number }
  const views: RunViewWire[] = []
  const client = {
    getRunView: vi.fn(async () => {
      const view = {} as RunViewWire
      views.push(view)
      return view
    }),
  } as unknown as ApiClient
  return { store, client, views }
}

function Probe({ client, store, enabled = true }: { client: ApiClient; store: SyncStore; enabled?: boolean }) {
  const view = useRunView(client, store, 'wrun_1', enabled)
  return <output>{view === null ? 'empty' : 'ready'}</output>
}

describe('shared run view resource', () => {
  let root: Root | null = null
  let container: HTMLDivElement | null = null

  afterEach(() => {
    vi.useRealTimers()
    if (root !== null) act(() => root?.unmount())
    container?.remove()
    root = null
    container = null
  })

  it('deduplicates consumers and refetches once after a durable run change', async () => {
    vi.useFakeTimers()
    const harness = makeHarness()
    container = document.createElement('div')
    root = createRoot(container)
    act(() => root?.render(<><Probe client={harness.client} store={harness.store} /><Probe client={harness.client} store={harness.store} /></>))
    await act(async () => { await Promise.resolve() })
    expect(harness.client.getRunView).toHaveBeenCalledTimes(1)
    expect(harness.store.subscriptionCount).toBe(1)

    act(() => harness.store.update(2))
    await act(async () => {
      vi.advanceTimersByTime(150)
      await Promise.resolve()
    })
    expect(harness.client.getRunView).toHaveBeenCalledTimes(2)
  })

  it('does not subscribe or fetch while an inspector tab is inactive', async () => {
    const harness = makeHarness()
    container = document.createElement('div')
    root = createRoot(container)
    act(() => root?.render(<Probe client={harness.client} store={harness.store} enabled={false} />))
    await act(async () => { await Promise.resolve() })
    expect(harness.client.getRunView).not.toHaveBeenCalled()
    expect(harness.store.subscriptionCount).toBe(0)

    act(() => root?.render(<Probe client={harness.client} store={harness.store} enabled />))
    await act(async () => { await Promise.resolve() })
    expect(harness.client.getRunView).toHaveBeenCalledTimes(1)
    expect(harness.store.subscriptionCount).toBe(1)
  })
})
