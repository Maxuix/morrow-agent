import { useCallback, useSyncExternalStore } from 'react'
import type { ApiClient } from '../api/client'
import type { RunViewWire } from '../api/types'
import type { SyncStore, WorkflowRunProjection } from './sync'

/**
 * Cheap change signature for a run projection: the run row version plus the
 * per-node row versions the store patches in place. Any status/node event
 * that touched the run moves this value, which schedules a view refetch.
 */
export function runProjectionSignature(projection: WorkflowRunProjection | undefined): string {
  if (projection === undefined) return 'absent'
  const nodes = projection.view?.nodes.map((item) => item.node.row_version).join(',') ?? 'noview'
  return `${projection.run.row_version}:${projection.run.status}:${nodes}`
}

const REFETCH_DEBOUNCE_MS = 150

/**
 * One authoritative run view per run id: fetched on selection change, then
 * kept fresh off store events (a moved signature schedules a debounced
 * refetch). No polling loops; a transient failure leaves the previous view
 * on screen and the next store event retries.
 */
interface RunViewEntry {
  view: RunViewWire | null
  fetchedSignature: string
  request: Promise<void> | null
  timer: ReturnType<typeof setTimeout> | null
  unsubscribe: (() => void) | null
  listeners: Set<() => void>
  client: ApiClient | null
  disposed: boolean
}

const cacheByStore = new WeakMap<SyncStore, Map<string, RunViewEntry>>()
const EMPTY_LISTENER = () => () => {}

function entryFor(store: SyncStore, runId: string): RunViewEntry {
  let cache = cacheByStore.get(store)
  if (cache === undefined) {
    cache = new Map()
    cacheByStore.set(store, cache)
  }
  let entry = cache.get(runId)
  if (entry === undefined) {
    entry = {
      view: null,
      fetchedSignature: '',
      request: null,
      timer: null,
      unsubscribe: null,
      listeners: new Set(),
      client: null,
      disposed: false,
    }
    cache.set(runId, entry)
  }
  return entry
}

function notify(entry: RunViewEntry): void {
  entry.listeners.forEach((listener) => listener())
}

function scheduleFetch(store: SyncStore, client: ApiClient, runId: string, entry: RunViewEntry): void {
  if (entry.disposed || entry.timer !== null || entry.request !== null) return
  entry.timer = setTimeout(() => {
    if (entry.disposed) return
    entry.timer = null
    fetchRunView(store, client, runId, entry)
  }, REFETCH_DEBOUNCE_MS)
}

function fetchRunView(store: SyncStore, client: ApiClient, runId: string, entry: RunViewEntry): void {
  if (entry.disposed || entry.request !== null) return
  entry.client = client
  const signature = runProjectionSignature(store.getState().workflowRuns.get(runId))
  entry.request = client.getRunView(runId).then(
    (view) => {
      if (entry.disposed) return
      entry.fetchedSignature = signature
      entry.view = view
      notify(entry)
    },
    () => {
      // Transient errors leave the last good view in place. The next durable
      // store event, or a new mount, retries without surfacing raw details.
    },
  ).finally(() => {
    if (entry.disposed) return
    entry.request = null
    notify(entry)
    if (runProjectionSignature(store.getState().workflowRuns.get(runId)) !== entry.fetchedSignature) {
      scheduleFetch(store, client, runId, entry)
    }
  })
}

function attachRunView(store: SyncStore, client: ApiClient, runId: string, listener: () => void): () => void {
  const entry = entryFor(store, runId)
  entry.disposed = false
  entry.listeners.add(listener)
  if (entry.client !== null && entry.client !== client) {
    entry.view = null
    entry.fetchedSignature = ''
  }
  entry.client = client
  if (entry.unsubscribe === null) {
    entry.unsubscribe = store.subscribe(() => {
      const signature = runProjectionSignature(store.getState().workflowRuns.get(runId))
      if (signature !== entry.fetchedSignature) scheduleFetch(store, client, runId, entry)
    })
  }
  if (entry.view === null && entry.request === null) fetchRunView(store, client, runId, entry)
  return () => {
    entry.listeners.delete(listener)
    if (entry.listeners.size > 0) return
    if (entry.timer !== null) clearTimeout(entry.timer)
    entry.timer = null
    entry.unsubscribe?.()
    entry.unsubscribe = null
    entry.disposed = true
    entry.request = null
    cacheByStore.get(store)?.delete(runId)
  }
}

/**
 * One authoritative run view per store/run pair. TaskPlanPanel and
 * WorkflowPanel may both need the same view for control/detail composition;
 * this cache keeps one request, one debounced event subscription, and one
 * snapshot for both consumers.
 */
export function useRunView(
  client: ApiClient,
  store: SyncStore,
  runId: string | null,
  enabled = true,
): RunViewWire | null {
  const subscribe = useCallback(
    (listener: () => void) => !enabled || runId === null
      ? EMPTY_LISTENER()
      : attachRunView(store, client, runId, listener),
    [client, enabled, runId, store],
  )
  const getSnapshot = useCallback(
    () => runId === null ? null : entryFor(store, runId).view,
    [runId, store],
  )
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
}
