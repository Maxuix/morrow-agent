import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../api/client'
import type { SessionWire } from '../api/types'
import { DEFAULT_VISIBLE_SESSIONS, SIDEBAR_COLLAPSED_KEY, SidebarStore, sortSessions } from './sidebar'

function session(id: string, overrides: Partial<SessionWire> = {}): SessionWire {
  return {
    session_id: id,
    lifecycle: 'active',
    health: 'ok',
    created_at: '2026-09-08T10:00:00Z',
    updated_at: '2026-09-08T10:00:00Z',
    current_task_run_id: null,
    parent_session_id: null,
    ...overrides,
  }
}

function fakeStorage() {
  const map = new Map<string, string>()
  return {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => { map.set(key, value) },
  }
}

interface Route {
  pattern: RegExp
  reply: (url: string, call: number) => unknown
  status?: (call: number) => number
}

function routedFetch(routes: Route[]) {
  const calls = new Map<string, number>()
  const urls: string[] = []
  const fetchImpl: typeof fetch = async input => {
    const url = String(input)
    urls.push(url)
    const route = routes.find(candidate => candidate.pattern.test(url))
    if (route === undefined) {
      return new Response(JSON.stringify({error: {code: 'unrouted', message: url}}), {status: 404})
    }
    const call = (calls.get(route.pattern.source) ?? 0) + 1
    calls.set(route.pattern.source, call)
    return new Response(JSON.stringify(route.reply(url, call)), {status: route.status?.(call) ?? 200})
  }
  return {fetchImpl, urls}
}

const WORKSPACES = {
  items: [
    {workspace_id: 'ws_a', path: '/tmp/a', display_name: 'A', available: true, last_used_at: null, git_root: null},
    {workspace_id: 'ws_b', path: '/tmp/b', display_name: 'B', available: true, last_used_at: null, git_root: null},
  ],
  revision: 3,
}

function store(routes: Route[], storage: ReturnType<typeof fakeStorage> | null = fakeStorage()) {
  const {fetchImpl, urls} = routedFetch(routes)
  const instance = new SidebarStore(new ApiClient({baseUrl: '', token: '', fetchImpl}), {storage})
  return {store: instance, urls}
}

const sessionRoute = (sessions: SessionWire[], nextCursor: string | null = null): Route => ({
  pattern: /\/sessions\?/,
  reply: () => ({sessions, next_cursor: nextCursor}),
})

describe('sortSessions', () => {
  it('orders pinned sessions first, then newest first', () => {
    const ordered = sortSessions([
      session('ses_old', {created_at: '2026-09-01T00:00:00Z'}),
      session('ses_new_pinned', {created_at: '2026-09-09T00:00:00Z', metadata: {title: 'p', pinned: true, revision: 1}}),
      session('ses_new', {created_at: '2026-09-08T00:00:00Z'}),
    ])
    expect(ordered.map(row => row.session_id)).toEqual(['ses_new_pinned', 'ses_new', 'ses_old'])
  })
})

describe('SidebarStore', () => {
  it('loads every workspace first page and hides rows beyond the default reveal', async () => {
    const sessions = Array.from({length: 6}, (_, index) => session(`ses_${index}`))
    const {store: instance} = store([{
      pattern: /\/v1\/workspaces$/,
      reply: () => WORKSPACES,
    }, sessionRoute(sessions, 'c2')])
    await instance.loadAll()
    const state = instance.getState()
    expect(state.workspaces.map(row => row.workspace_id)).toEqual(['ws_a', 'ws_b'])
    expect(state.sessions.ws_a).toHaveLength(6)
    expect(state.visible.ws_a ?? DEFAULT_VISIBLE_SESSIONS).toBe(DEFAULT_VISIBLE_SESSIONS)
    expect(state.error).toBeNull()
  })

  it('keeps collapsed groups across instances and re-expands on demand', async () => {
    const storage = fakeStorage()
    const {store: first} = store([{pattern: /\/v1\/workspaces$/, reply: () => WORKSPACES}, sessionRoute([])], storage)
    await first.loadAll()
    first.toggleCollapsed('ws_a')
    expect(first.getState().collapsed.ws_a).toBe(true)
    expect(JSON.parse(storage.getItem(SIDEBAR_COLLAPSED_KEY) ?? '{}')).toEqual({ws_a: true})

    const {store: second} = store([{pattern: /\/v1\/workspaces$/, reply: () => WORKSPACES}, sessionRoute([])], storage)
    expect(second.getState().collapsed.ws_a).toBe(true)
    second.expandGroup('ws_a')
    expect(second.getState().collapsed.ws_a).toBe(false)
    expect(JSON.parse(storage.getItem(SIDEBAR_COLLAPSED_KEY) ?? '{}')).toEqual({ws_a: false})
  })

  it('reveals already-loaded rows on 显示更多 before fetching the next page', async () => {
    const firstPage = Array.from({length: 6}, (_, index) => session(`ses_${index}`))
    const {store: instance, urls} = store([{
      pattern: /\/v1\/workspaces$/,
      reply: () => WORKSPACES,
    }, {
      pattern: /workspaces\/ws_a\/sessions\?/,
      reply: (_url, call) => call === 1
        ? {sessions: firstPage, next_cursor: 'c2'}
        : {sessions: [session('ses_6')], next_cursor: null},
    }])
    await instance.loadAll()
    const listLoads = urls.length
    await instance.loadMore('ws_a')
    expect(instance.getState().visible.ws_a).toBe(DEFAULT_VISIBLE_SESSIONS + 20)
    expect(instance.getState().sessions.ws_a).toHaveLength(6)
    expect(urls.length).toBe(listLoads) // local reveal only
    await instance.loadMore('ws_a')
    expect(instance.getState().sessions.ws_a.map(row => row.session_id)).toContain('ses_6')
    expect(instance.getState().nextCursor.ws_a).toBeNull()
  })

  it('retries session creation with the same command id after a failure', async () => {
    const created = session('ses_fresh')
    const bodies: string[] = []
    const fetchImpl: typeof fetch = async (input, init) => {
      const url = String(input)
      if (url.endsWith('/workspaces/ws_a/sessions') && init?.method === 'POST') {
        bodies.push(String(init.body))
        if (bodies.length === 1) return new Response(JSON.stringify({error: {code: 'x', message: 'boom'}}), {status: 500})
        return new Response(JSON.stringify({result: {session: created}}), {status: 200})
      }
      return new Response(JSON.stringify({items: [], revision: 1}), {status: 200})
    }
    const instance = new SidebarStore(new ApiClient({baseUrl: '', token: '', fetchImpl}), {storage: null})
    await expect(instance.createSession('ws_a')).rejects.toThrow('boom')
    await expect(instance.createSession('ws_a')).resolves.toEqual(created)
    expect(bodies).toHaveLength(2)
    expect(JSON.parse(bodies[0]).command_id).toBe(JSON.parse(bodies[1]).command_id)
    expect(instance.getState().sessions.ws_a?.map(row => row.session_id)).toContain('ses_fresh')
  })

  it('patches rename and pin in place without a refetch', async () => {
    const original = session('ses_t', {metadata: {title: '旧名', pinned: false, revision: 4}})
    const requests: Array<{method: string; url: string}> = []
    const fetchImpl: typeof fetch = async (input, init) => {
      requests.push({method: init?.method ?? 'GET', url: String(input)})
      if (init?.method === 'PATCH') {
        return new Response(JSON.stringify({metadata: {title: '新名', pinned: true, revision: 5}}), {status: 200})
      }
      return new Response(JSON.stringify({items: [], revision: 1}), {status: 200})
    }
    const instance = new SidebarStore(new ApiClient({baseUrl: '', token: '', fetchImpl}), {storage: null})
    instance.upsertSession('ws_a', original)
    await instance.renameSession('ws_a', original, '新名')
    await instance.togglePinned('ws_a', {...original, metadata: {title: '新名', pinned: false, revision: 5}})
    expect(requests.filter(row => row.method === 'PATCH')).toHaveLength(2)
    expect(requests.every(row => !row.url.includes('/sessions?'))).toBe(true)
  })

  it('drops a session row when archiving moves it outside the active filter', async () => {
    const original = session('ses_x')
    const lifecycle: typeof fetch = async (input, init) => {
      if (String(input).endsWith('/archive') && init?.method === 'POST') {
        return new Response(JSON.stringify({session: {...original, lifecycle: 'archived'}}), {status: 200})
      }
      return new Response(JSON.stringify({items: [], revision: 1}), {status: 200})
    }
    const instance = new SidebarStore(new ApiClient({baseUrl: '', token: '', fetchImpl: lifecycle}), {storage: null})
    instance.upsertSession('ws_a', original)
    await instance.setArchivedSession('ws_a', original, true)
    expect(instance.getState().sessions.ws_a).toHaveLength(0)
  })

  it('keeps previous rows and records an error when one workspace page fails to load', async () => {
    const kept = [session('ses_keep')]
    const {store: instance} = store([{
      pattern: /\/v1\/workspaces$/,
      reply: () => WORKSPACES,
    }, {
      pattern: /workspaces\/ws_a\/sessions\?/,
      reply: () => ({sessions: kept, next_cursor: null}),
      status: (call) => (call === 1 ? 200 : 500),
    }, {
      pattern: /workspaces\/ws_b\/sessions\?/,
      reply: () => ({sessions: [session('ses_b')], next_cursor: null}),
    }])
    await instance.loadAll()
    expect(instance.getState().sessions.ws_a?.map(row => row.session_id)).toEqual(['ses_keep'])
    expect(instance.getState().errors.ws_a).toBeUndefined()
    await instance.loadAll()
    const state = instance.getState()
    expect(state.sessions.ws_a?.map(row => row.session_id)).toEqual(['ses_keep'])
    expect(state.errors.ws_a).toBe('会话列表加载失败')
    expect(state.sessions.ws_b?.map(row => row.session_id)).toEqual(['ses_b'])
    expect(state.errors.ws_b).toBeUndefined()
  })

  it('debounces search input into one committed reload', async () => {
    vi.useFakeTimers()
    try {
      const {store: instance, urls} = store([{
        pattern: /\/v1\/workspaces$/,
        reply: () => WORKSPACES,
      }, sessionRoute([])])
      await instance.loadAll()
      const listLoads = urls.length
      instance.setSearchOpen(true)
      instance.setSearch('重')
      expect(instance.getState().committedSearch).toBe('')
      await vi.advanceTimersByTimeAsync(301)
      expect(instance.getState().committedSearch).toBe('重')
      const searched = urls.slice(listLoads).filter(url => url.includes('search=%E9%87%8D'))
      expect(searched.length).toBeGreaterThan(0)
    } finally {
      vi.useRealTimers()
    }
  })
})
