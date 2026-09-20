/**
 * Unified app navigation: a discriminated `AppLocation` union is the single
 * source of truth for which main view is on screen, replacing independent
 * boolean/enum view flags. The location is mirrored into the URL hash so
 * refresh and browser back/forward keep working with the static bundle
 * (Vite base './', no router framework). Parsing is whitelist-only; unknown
 * or malformed targets fall back to chat.
 *
 * Draft-owning pages register `DirtyGuard`s on the store's guard stack. Every
 * leave path — navigate(), workspace/session switches in App, and hash
 * history jumps (back/forward) — awaits the stack before committing;
 * beforeunload uses the browser's native protection driven by `isDirty()`.
 * The Inspector (right-hand panel) is deliberately NOT part of AppLocation:
 * panel visibility is shell UI state, not a navigation target.
 */

export type KnowledgeSection = 'profile' | 'preferences' | 'knowledge'
export type ToolsSection = 'skills' | 'mcp'
export type SettingsSection = 'providers' | 'agents' | 'appearance' | 'diagnostics'
/** 工作流定义页面目标 */
export type EditorTarget = 'workflows'

/**
 * In-section landing for slash commands and deep links. Unknown values are
 * dropped at parse time so a stale hash cannot invent a form state.
 */
export type KnowledgeFocus =
  | 'edit'
  | 'reset'
  | 'list'
  | 'add'
  | 'show'
  | 'replace'
  | 'remove'
  | 'enable'
  | 'disable'
  | 'records'
  | 'learning'
  | 'promotions'
  | 'selection'

export type AppLocation =
  | { kind: 'chat' }
  | { kind: 'knowledge'; section: KnowledgeSection; focus?: KnowledgeFocus }
  | { kind: 'tools'; section: ToolsSection; item?: string }
  | { kind: 'editor'; target: EditorTarget; path?: string }
  | { kind: 'settings'; section: SettingsSection }

export const DEFAULT_LOCATION: AppLocation = { kind: 'chat' }

const KNOWLEDGE_SECTIONS: readonly KnowledgeSection[] = ['profile', 'preferences', 'knowledge']
const TOOLS_SECTIONS: readonly ToolsSection[] = ['skills', 'mcp']
const SETTINGS_SECTIONS: readonly SettingsSection[] = ['providers', 'agents', 'appearance', 'diagnostics']
const KNOWLEDGE_FOCUSES: readonly KnowledgeFocus[] = [
  'edit', 'reset', 'list', 'add', 'show', 'replace', 'remove', 'enable', 'disable',
  'records', 'learning', 'promotions', 'selection',
]

function optionalPick<T extends string>(allowed: readonly T[], value: string | null): T | undefined {
  return value && allowed.includes(value as T) ? (value as T) : undefined
}

function optionalItem(value: string | null): string | undefined {
  if (!value) return undefined
  const trimmed = value.trim().slice(0, 80)
  return trimmed || undefined
}

/** Views the shell renders directly; every location has its own container. */
export type ShellView = 'chat' | 'knowledge' | 'tools' | 'edit' | 'settings'

/** Map a location onto the shell view that renders it. */
export function viewForLocation(location: AppLocation): ShellView {
  switch (location.kind) {
    case 'chat':
      return 'chat'
    case 'knowledge':
      return 'knowledge'
    case 'tools':
      return 'tools'
    case 'editor':
      return 'edit'
    case 'settings':
      return 'settings'
  }
}

function pick<T extends string>(allowed: readonly T[], value: string | undefined, fallback: T): T {
  return allowed.includes(value as T) ? (value as T) : fallback
}

/** Whitelist parse; every illegal shape falls back to a safe location. */
export function parseLocation(hash: string): AppLocation {
  const raw = hash.startsWith('#') ? hash.slice(1) : hash
  const queryIndex = raw.indexOf('?')
  const path = queryIndex < 0 ? raw : raw.slice(0, queryIndex)
  const params = new URLSearchParams(queryIndex < 0 ? '' : raw.slice(queryIndex + 1))
  const [head, section] = path.split('/').filter(Boolean)
  switch (head) {
    case 'chat':
      return { kind: 'chat' }
    case 'knowledge': {
      const focus = optionalPick(KNOWLEDGE_FOCUSES, params.get('focus'))
      return focus
        ? { kind: 'knowledge', section: pick(KNOWLEDGE_SECTIONS, section, 'profile'), focus }
        : { kind: 'knowledge', section: pick(KNOWLEDGE_SECTIONS, section, 'profile') }
    }
    case 'tools': {
      const item = optionalItem(params.get('item'))
      return item
        ? { kind: 'tools', section: pick(TOOLS_SECTIONS, section, 'skills'), item }
        : { kind: 'tools', section: pick(TOOLS_SECTIONS, section, 'skills') }
    }
    case 'editor': {
      return { kind: 'editor', target: 'workflows' }
    }
    case 'settings':
      return { kind: 'settings', section: pick(SETTINGS_SECTIONS, section, 'providers') }
    default:
      return { kind: 'chat' }
  }
}

/** Canonical hash form; parse(serialize(x)) round-trips for every location. */
export function serializeLocation(location: AppLocation): string {
  switch (location.kind) {
    case 'chat':
      return '#/chat'
    case 'knowledge': {
      const params = new URLSearchParams()
      if (location.focus) params.set('focus', location.focus)
      const query = params.toString()
      return `#/knowledge/${location.section}${query ? `?${query}` : ''}`
    }
    case 'tools': {
      const params = new URLSearchParams()
      if (location.item) params.set('item', location.item)
      const query = params.toString()
      return `#/tools/${location.section}${query ? `?${query}` : ''}`
    }
    case 'editor': {
      return '#/editor/workflows'
    }
    case 'settings':
      return `#/settings/${location.section}`
  }
}

/**
 * One draft owner's leave guard. `isDirty` is a side-effect-free probe used
 * for beforeunload and cheap checks; `confirmLeave` may show UI (save / keep
 * draft / cancel) and resolves true when the leave may proceed.
 */
export interface DirtyGuard {
  isDirty(): boolean
  confirmLeave(): Promise<boolean>
}

export interface NavigationState {
  location: AppLocation
  /** Where a "返回对话" affordance should go; in-memory only, not in the URL. */
  returnTo: AppLocation | null
}

export class NavigationStore {
  private state: NavigationState
  /** Guard stack: later registrations are consulted first (LIFO). */
  private guards: DirtyGuard[] = []
  private listeners = new Set<() => void>()
  private settling = false

  constructor(hash?: string) {
    const initial = hash ?? (typeof window !== 'undefined' ? window.location.hash : '')
    this.state = { location: parseLocation(initial), returnTo: null }
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  getState = (): NavigationState => this.state

  registerDirtyGuard = (guard: DirtyGuard): (() => void) => {
    this.guards.push(guard)
    return () => {
      this.guards = this.guards.filter(item => item !== guard)
    }
  }

  hasDirtyGuards(): boolean {
    return this.guards.some(guard => guard.isDirty())
  }

  /** Resolve every dirty guard, newest first; false cancels the leave. */
  async confirmLeave(): Promise<boolean> {
    for (let index = this.guards.length - 1; index >= 0; index -= 1) {
      const guard = this.guards[index]
      if (!guard.isDirty()) continue
      if (!(await guard.confirmLeave())) return false
    }
    return true
  }

  /** The single navigation entry point; false means a guard cancelled. */
  async navigate(location: AppLocation, returnTo?: AppLocation): Promise<boolean> {
    if (returnTo === undefined && serializeLocation(location) === serializeLocation(this.state.location)) {
      return true
    }
    if (!(await this.confirmLeave())) return false
    this.commit({ location, returnTo: returnTo ?? null })
    return true
  }

  /**
   * Attach hash/beforeunload listeners and normalize the entry URL without
   * adding a history entry. Returns the detach function.
   */
  start(): () => void {
    if (typeof window === 'undefined') return () => {}
    const current = serializeLocation(this.state.location)
    if (window.location.hash !== current) window.history.replaceState(null, '', current)
    const onHashChange = () => {
      void this.settleHash()
    }
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!this.hasDirtyGuards()) return
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('hashchange', onHashChange)
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => {
      window.removeEventListener('hashchange', onHashChange)
      window.removeEventListener('beforeunload', onBeforeUnload)
    }
  }

  private commit(state: NavigationState) {
    this.state = state
    if (typeof window !== 'undefined') {
      const hash = serializeLocation(state.location)
      // Assigning location.hash pushes a history entry; the resulting
      // hashchange is a no-op because it matches the committed state.
      if (window.location.hash !== hash) window.location.hash = hash
    }
    for (const listener of this.listeners) listener()
  }

  /** Browser back/forward (or a manual hash edit) already changed the URL. */
  private async settleHash(): Promise<void> {
    if (this.settling || typeof window === 'undefined') return
    this.settling = true
    try {
      const parsed = parseLocation(window.location.hash)
      if (serializeLocation(parsed) === serializeLocation(this.state.location)) return
      if (await this.confirmLeave()) {
        this.commit({ location: parsed, returnTo: null })
      } else {
        // Cancelled: put the address bar back where the user still is. The
        // resulting hashchange matches current state and is ignored.
        window.location.hash = serializeLocation(this.state.location)
      }
    } finally {
      this.settling = false
    }
  }
}
