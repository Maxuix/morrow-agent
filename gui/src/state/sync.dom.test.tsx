// @vitest-environment jsdom
import { StrictMode, useEffect, useSyncExternalStore } from 'react'
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import type { ApiClient } from '../api/client'
import type { SnapshotWire } from '../api/types'
import { SyncStore, type WebSocketLike } from './sync'

afterEach(cleanup)

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}

function Projection({ store }: { store: SyncStore }) {
  const state = useSyncExternalStore(listener => store.subscribe(listener), () => store.getState())
  useEffect(() => { void store.start(); return () => store.stop() }, [store])
  return <output>{state.connection}:{state.cursor}</output>
}

const snapshot = (cursor: number): SnapshotWire => ({
  cursor, workflow_runs: [], pending_approvals: [],
})

it('StrictMode reboots the same store and ignores the first delayed snapshot', async () => {
  const first = deferred<SnapshotWire>()
  let snapshots = 0
  const sockets: WebSocketLike[] = []
  const client = {
    snapshot: () => ++snapshots === 1 ? first.promise : Promise.resolve(snapshot(9)),
    listSessions: async () => ({ sessions: [], next_cursor: null }),
    scopePath: (path: string) => path,
  } as unknown as ApiClient
  const store = new SyncStore({ client, token: '', wsFactory: () => {
    const socket: WebSocketLike = { onmessage: null, onclose: null, close() {} }
    sockets.push(socket)
    return socket
  } })
  await act(async () => { render(<StrictMode><Projection store={store} /></StrictMode>) })
  expect(snapshots).toBe(2)
  expect(sockets).toHaveLength(1)
  act(() => sockets[0].onmessage?.({ data: JSON.stringify({ type: 'hello', latest_cursor: 9 }) }))
  expect(screen.getByRole('status').textContent).toBe('live:9')
  await act(async () => { first.resolve(snapshot(1)) })
  expect(store.getState().cursor).toBe(9)
  expect(sockets).toHaveLength(1)
})

it('unmount during snapshot loading creates no ghost socket or projection', async () => {
  const loading = deferred<SnapshotWire>()
  let socketCount = 0
  let sessionQueries = 0
  const client = {
    snapshot: () => loading.promise,
    listSessions: async () => { sessionQueries++; return { sessions: [], next_cursor: null } },
    scopePath: (path: string) => path,
  } as unknown as ApiClient
  const store = new SyncStore({ client, token: '', wsFactory: () => {
    socketCount++
    return { onmessage: null, onclose: null, close() {} }
  } })
  const view = render(<Projection store={store} />)
  view.unmount()
  await act(async () => { loading.resolve(snapshot(7)) })
  expect(socketCount).toBe(0)
  expect(sessionQueries).toBe(0)
  expect(store.getState().cursor).toBe(0)
})
