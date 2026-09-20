import { commandId } from '../views/lib/editor'
import type { ApiClient, WorkspaceEntry } from '../api/client'
import type { SessionWire } from '../api/types'

/**
 * Cross-workspace sidebar data, owned at the App level.
 *
 * Switching a workspace remounts the whole `ActiveWorkspace` subtree, so the
 * grouped session tree and its collapse state must live OUTSIDE that subtree.
 * This store keeps one session page per workspace plus the collapsed/visible
 * bookkeeping, and survives remounts because App creates it once.
 *
 * Collapse state persists to localStorage; list data is a projection of
 * `searchSessions` per workspace (pinned first, then newest first) and is
 * refreshed by App on the durable event cursor, after mutations, and on
 * workspace registration changes.
 */

export const SIDEBAR_COLLAPSED_KEY = 'morrow.sidebar.collapsed.v1'
export const DEFAULT_VISIBLE_SESSIONS = 5
const REVEAL_STEP = 20

export interface SidebarState {
  revision: number
  workspaces: WorkspaceEntry[]
  /** First page per workspace, sorted for display. */
  sessions: Record<string, SessionWire[]>
  nextCursor: Record<string, string | null>
  /** Per-workspace load failure; a failed refresh keeps the previous rows. */
  errors: Record<string, string>
  /** Locally revealed row count per group; the rest hides behind 显示更多. */
  visible: Record<string, number>
  /** Workspace groups the user folded away (persisted). */
  collapsed: Record<string, boolean>
  searchOpen: boolean
  /** Live input text; `committedSearch` is what the lists were loaded with. */
  search: string
  committedSearch: string
  archived: boolean
  includeExecution: boolean
  loading: boolean
  error: string | null
}

/** Display order inside a group: pinned first, then newest first. Stable. */
export function sortSessions(sessions: SessionWire[]): SessionWire[] {
  return [...sessions].sort((a, b) => {
    const pinned = Number(b.metadata?.pinned ?? false) - Number(a.metadata?.pinned ?? false)
    return pinned !== 0 ? pinned : b.created_at.localeCompare(a.created_at)
  })
}

function mergeSessions(current: SessionWire[], incoming: SessionWire[]): SessionWire[] {
  const byId = new Map(current.map(session => [session.session_id, session]))
  for (const session of incoming) byId.set(session.session_id, session)
  return sortSessions([...byId.values()])
}

function readCollapsed(storage: Pick<Storage, 'getItem'> | null): Record<string, boolean> {
  if (!storage) return {}
  try {
    const value: unknown = JSON.parse(storage.getItem(SIDEBAR_COLLAPSED_KEY) ?? '{}')
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
    return Object.fromEntries(
      Object.entries(value).filter(([, folded]) => typeof folded === 'boolean'),
    )
  } catch {
    return {}
  }
}

export interface SidebarStoreOptions {
  storage?: Pick<Storage, 'getItem' | 'setItem'> | null
  /** Search debounce; injectable so tests never wait on wall-clock. */
  debounceMs?: number
}

export class SidebarStore {
  readonly client: ApiClient
  private state: SidebarState
  private listeners = new Set<() => void>()
  private generation = 0
  /** Per-workspace in-flight create identity: a retry replays the same command. */
  private createKeys = new Map<string, string>()
  private searchTimer: ReturnType<typeof setTimeout> | null = null
  private readonly storage: Pick<Storage, 'getItem' | 'setItem'> | null
  private readonly debounceMs: number

  constructor(client: ApiClient, options: SidebarStoreOptions = {}) {
    this.client = client
    this.storage = options.storage ?? (typeof localStorage === 'undefined' ? null : localStorage)
    this.debounceMs = options.debounceMs ?? 300
    this.state = {
      revision: 0,
      workspaces: [],
      sessions: {},
      nextCursor: {},
      errors: {},
      visible: {},
      collapsed: readCollapsed(this.storage),
      searchOpen: false,
      search: '',
      committedSearch: '',
      archived: false,
      includeExecution: false,
      loading: false,
      error: null,
    }
  }

  getState = () => this.state
  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }
  private patch(value: Partial<SidebarState>) {
    this.state = { ...this.state, ...value, revision: this.state.revision + 1 }
    this.listeners.forEach(listener => listener())
  }

  /** Load the workspace list and the first session page of each workspace. */
  async loadAll(): Promise<void> {
    const generation = ++this.generation
    this.patch({ loading: true, error: null })
    try {
      const list = await this.client.workspaces()
      if (generation !== this.generation) return
      const pages = await Promise.all(
        list.items.map(workspace => this.fetchPage(workspace.workspace_id).catch(() => null)),
      )
      if (generation !== this.generation) return
      const sessions: Record<string, SessionWire[]> = {}
      const nextCursor: Record<string, string | null> = {}
      const errors: Record<string, string> = {}
      list.items.forEach((workspace, index) => {
        const id = workspace.workspace_id
        const page = pages[index]
        if (page) {
          sessions[id] = page.sessions
          nextCursor[id] = page.next_cursor
          return
        }
        // A single failed page must not blank the group; keep the previous
        // rows and surface the failure so the UI can say so.
        sessions[id] = this.state.sessions[id] ?? []
        nextCursor[id] = this.state.nextCursor[id] ?? null
        errors[id] = '会话列表加载失败'
      })
      this.patch({ workspaces: list.items, sessions, nextCursor, errors, loading: false })
    } catch (error) {
      if (generation !== this.generation) return
      this.patch({ loading: false, error: error instanceof Error ? error.message : '侧栏加载失败' })
    }
  }

  private fetchPage(workspace: string, cursor?: string) {
    const { committedSearch, archived, includeExecution } = this.state
    return this.client.searchSessions(workspace, committedSearch, archived, cursor, includeExecution)
      .then(page => ({ sessions: sortSessions(page.sessions), next_cursor: page.next_cursor }))
  }

  /** Re-fetch the first page of one workspace (event stream, after mutation). */
  async refreshWorkspace(workspace: string): Promise<void> {
    try {
      const page = await this.fetchPage(workspace)
      if (this.state.sessions[workspace] === undefined && page.sessions.length === 0) return
      this.patch({
        sessions: { ...this.state.sessions, [workspace]: page.sessions },
        nextCursor: { ...this.state.nextCursor, [workspace]: page.next_cursor },
      })
    } catch {
      // The next cursor bump retries; a transient failure must not blank the tree.
    }
  }

  /** 显示更多: reveal more loaded rows first, then fetch the next server page. */
  async loadMore(workspace: string): Promise<void> {
    const loaded = this.state.sessions[workspace] ?? []
    const visible = this.state.visible[workspace] ?? DEFAULT_VISIBLE_SESSIONS
    if (visible < loaded.length) {
      this.patch({ visible: { ...this.state.visible, [workspace]: visible + REVEAL_STEP } })
      return
    }
    const cursor = this.state.nextCursor[workspace]
    if (!cursor) return
    try {
      const page = await this.fetchPage(workspace, cursor)
      this.patch({
        sessions: { ...this.state.sessions, [workspace]: mergeSessions(loaded, page.sessions) },
        nextCursor: { ...this.state.nextCursor, [workspace]: page.next_cursor },
        visible: { ...this.state.visible, [workspace]: visible + REVEAL_STEP },
      })
    } catch (error) {
      this.patch({ error: error instanceof Error ? error.message : '会话列表加载失败' })
    }
  }

  toggleCollapsed(workspace: string) {
    const collapsed = !this.state.collapsed[workspace]
    this.patch({ collapsed: { ...this.state.collapsed, [workspace]: collapsed } })
    this.persistCollapsed()
  }

  /** Opening a session (or creating one) must surface it even in a folded group. */
  expandGroup(workspace: string) {
    if (!this.state.collapsed[workspace]) return
    this.patch({ collapsed: { ...this.state.collapsed, [workspace]: false } })
    this.persistCollapsed()
  }

  private persistCollapsed() {
    try { this.storage?.setItem(SIDEBAR_COLLAPSED_KEY, JSON.stringify(this.state.collapsed)) } catch { /* private mode: keep in memory */ }
  }

  setSearchOpen(open: boolean) {
    if (!open) {
      if (this.searchTimer !== null) clearTimeout(this.searchTimer)
      this.searchTimer = null
      this.patch({ searchOpen: false, search: '', committedSearch: '' })
      void this.reloadAllLists()
      return
    }
    this.patch({ searchOpen: true })
  }

  setSearch(text: string) {
    this.patch({ search: text })
    if (this.searchTimer !== null) clearTimeout(this.searchTimer)
    if (text === this.state.committedSearch) return
    this.searchTimer = setTimeout(() => {
      this.searchTimer = null
      this.patch({ committedSearch: this.state.search })
      void this.reloadAllLists()
    }, this.debounceMs)
  }

  setArchived(archived: boolean) {
    this.patch({ archived })
    void this.reloadAllLists()
  }

  setIncludeExecution(includeExecution: boolean) {
    this.patch({ includeExecution })
    void this.reloadAllLists()
  }

  private async reloadAllLists(): Promise<void> {
    const generation = ++this.generation
    const targets = this.state.workspaces.map(workspace => workspace.workspace_id)
    await Promise.all(targets.map(async workspace => {
      try {
        const page = await this.fetchPage(workspace)
        if (generation !== this.generation) return
        this.patch({
          sessions: { ...this.state.sessions, [workspace]: page.sessions },
          nextCursor: { ...this.state.nextCursor, [workspace]: page.next_cursor },
        })
      } catch { /* keep previous rows; the next trigger retries */ }
    }))
  }

  /** Merge a fresh/updated session into its group (create, fork, refresh). */
  upsertSession(workspace: string, session: SessionWire) {
    this.patch({
      sessions: {...this.state.sessions, [workspace]: mergeSessions(this.state.sessions[workspace] ?? [], [session])},
    })
  }

  /**
   * Create a session in the given workspace. The command id is kept until the
   * server accepts it, so a retry after a network failure cannot fork twice.
   */
  async createSession(workspace: string, options: {upsert?: boolean} = {}): Promise<SessionWire> {
    const key = this.createKeys.get(workspace) ?? commandId('chat_session')
    try {
      const session = await this.client.createChatSession(workspace, key)
      this.createKeys.delete(workspace)
      if (options.upsert !== false) {
        this.expandGroup(workspace)
        this.upsertSession(workspace, session)
      }
      return session
    } catch (error) {
      this.createKeys.set(workspace, key)
      throw error
    }
  }

  private async mutateMetadata(workspace: string, session: SessionWire, body: {title?: string; pinned?: boolean}) {
    const value = await this.client.sessionMetadata(workspace, session.session_id, {
      command_id: commandId('sidebar_session'),
      expected_revision: session.metadata?.revision ?? 0,
      ...body,
    })
    this.upsertSession(workspace, { ...session, metadata: value.metadata })
  }

  renameSession(workspace: string, session: SessionWire, title: string) {
    return this.mutateMetadata(workspace, session, { title })
  }

  togglePinned(workspace: string, session: SessionWire) {
    return this.mutateMetadata(workspace, session, { pinned: !session.metadata?.pinned })
  }

  async setArchivedSession(workspace: string, session: SessionWire, archive: boolean) {
    const value = await this.client.sessionLifecycle(
      workspace, session.session_id, archive ? 'archive' : 'unarchive', session.updated_at,
      commandId('sidebar_lifecycle'),
    )
    // Archive/unarchive moves the row across the active filter boundary: with
    // the filter off, archiving hides it; with the filter on, restoring does.
    if (archive !== this.state.archived) {
      this.removeSession(workspace, session.session_id)
      return
    }
    this.upsertSession(workspace, value.session)
  }

  removeSession(workspace: string, sessionId: string) {
    const current = this.state.sessions[workspace]
    if (current === undefined) return
    this.patch({
      sessions: {...this.state.sessions, [workspace]: current.filter(row => row.session_id !== sessionId)},
    })
  }
}
