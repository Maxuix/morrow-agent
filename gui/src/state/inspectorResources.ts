import { useCallback, useEffect, useRef, useSyncExternalStore } from 'react'

export interface InspectorResourceState<T> {
  data: T | null
  error: string | null
  loading: boolean
}

interface ResourceEntry<T> {
  state: InspectorResourceState<T>
  request: Promise<void> | null
  listeners: Set<() => void>
  lastUsed: number
}

const MAX_RESOURCE_ENTRIES = 64

/**
 * Small process-local cache for Inspector reads. The shell keeps inactive
 * bodies mounted, so the cache is deliberately separate from React body
 * state: a tab can be hidden without starting a request, while two consumers
 * of the same target still share one in-flight read.
 */
export class InspectorResourceCache {
  private readonly entries = new Map<string, ResourceEntry<unknown>>()
  private usage = 0

  read<T>(key: string): InspectorResourceState<T> {
    const entry = this.entry<T>(key)
    this.touch(entry)
    return entry.state
  }

  subscribe(key: string, listener: () => void): () => void {
    const entry = this.entry(key)
    this.touch(entry)
    entry.listeners.add(listener)
    return () => entry.listeners.delete(listener)
  }

  load<T>(key: string, loader: () => Promise<T>, force = false): Promise<void> {
    const entry = this.entry<T>(key)
    this.touch(entry)
    if (!force && (entry.request !== null || entry.state.data !== null)) {
      return entry.request ?? Promise.resolve()
    }
    if (entry.request !== null) return entry.request
    entry.state = { data: force ? entry.state.data : null, error: null, loading: true }
    this.notify(entry)
    const request = loader().then(
      value => {
        entry.state = { data: value, error: null, loading: false }
      },
      error => {
        entry.state = {
          data: entry.state.data,
          error: error instanceof Error ? error.message : '读取 Inspector 数据失败',
          loading: false,
        }
      },
    ).finally(() => {
      entry.request = null
      this.notify(entry)
    })
    entry.request = request
    return request
  }

  invalidate(key: string): void {
    const entry = this.entries.get(key)
    if (entry === undefined) return
    this.touch(entry)
    if (entry.request !== null) return
    entry.state = { data: null, error: null, loading: false }
    this.notify(entry)
  }

  clear(): void {
    this.entries.clear()
    this.usage = 0
  }

  private entry<T>(key: string): ResourceEntry<T> {
    const current = this.entries.get(key)
    if (current !== undefined) return current as ResourceEntry<T>
    this.evictIdleEntries()
    const created: ResourceEntry<T> = {
      state: { data: null, error: null, loading: false },
      request: null,
      listeners: new Set(),
      lastUsed: ++this.usage,
    }
    this.entries.set(key, created as ResourceEntry<unknown>)
    return created
  }

  private touch(entry: ResourceEntry<unknown>): void {
    entry.lastUsed = ++this.usage
  }

  private evictIdleEntries(): void {
    if (this.entries.size < MAX_RESOURCE_ENTRIES) return
    const idle = [...this.entries.entries()]
      .filter(([, entry]) => entry.listeners.size === 0 && entry.request === null)
      .sort(([, left], [, right]) => left.lastUsed - right.lastUsed)
    while (this.entries.size >= MAX_RESOURCE_ENTRIES && idle.length > 0) {
      const [key] = idle.shift()!
      this.entries.delete(key)
    }
  }

  private notify(entry: ResourceEntry<unknown>): void {
    entry.listeners.forEach(listener => listener())
  }
}

export const inspectorResources = new InspectorResourceCache()

export function inspectorResourceKey(
  kind: string,
  workspaceId: string | undefined,
  sessionId: string | undefined,
  target: Record<string, string | null | undefined> = {},
): string {
  return JSON.stringify([
    kind,
    workspaceId ?? null,
    sessionId ?? null,
    ...Object.keys(target).sort().map(key => [key, target[key] ?? null]),
  ])
}

export function useInspectorResource<T>(
  key: string,
  loader: () => Promise<T>,
  enabled: boolean,
): InspectorResourceState<T> & { reload: () => Promise<void> } {
  const loaderRef = useRef(loader)
  loaderRef.current = loader
  const subscribe = useCallback(
    (listener: () => void) => inspectorResources.subscribe(key, listener),
    [key],
  )
  const getSnapshot = useCallback(() => inspectorResources.read<T>(key), [key])
  const state = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
  useEffect(() => {
    if (enabled) void inspectorResources.load(key, () => loaderRef.current())
  }, [enabled, key])
  const reload = useCallback(
    () => inspectorResources.load(key, () => loaderRef.current(), true),
    [key],
  )
  return { ...state, reload }
}
