import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'
import type { WorkspaceEntry } from '../api/client'
import type { ApprovalWire, SessionWire } from '../api/types'
import { DraftStorage } from '../state/chat'
import type { AppLocation } from '../state/navigation'
import { DEFAULT_VISIBLE_SESSIONS, SidebarStore } from '../state/sidebar'
import type { ConnectionState } from '../state/sync'
import { CONNECTION_LABELS, formatTimestamp } from './lib/labels'
import { useDismissablePopover } from './lib/popover'
import { WorkspaceManagerDialog } from './WorkspaceManager'
/** Footer copy is space-bound; the full label rides on `title`. */
const CONNECTION_SHORT: Record<ConnectionState, string> = {
  connecting: '连接中', live: '已连接', reconnecting: '重连中', offline: '离线', unauthorized: '令牌无效',
}

/** Single-line row label; untitled sessions use a readable product default. */
export function sessionTitle(session: SessionWire): string {
  return session.metadata?.title || '新对话'
}

/**
 * Codex-style sidebar: brand + search + collapse on the top row, three quick
 * actions, then every workspace as a collapsible group of its sessions.
 * Row actions (＋/…) surface on hover and keyboard focus and stay visible on
 * touch screens; the tree scrolls independently between fixed top and footer.
 */
export function WorkspaceSidebar({
  store,
  activeWorkspaceId,
  activeSessionId,
  cursor,
  connection,
  pendingApprovals,
  location,
  onNavigate,
  onSelectSession,
  onCreateSession,
  onCollapse,
  onSwitchWorkspace,
}: {
  store: SidebarStore
  activeWorkspaceId: string | null
  activeSessionId: string | null
  /** Durable event cursor; every bump re-syncs the active workspace's rows. */
  cursor: number
  connection: ConnectionState
  pendingApprovals: number | ApprovalWire[]
  /** Current location; nav rows highlight from its kind. */
  location: AppLocation
  /** Single navigation entry point (guards run inside the store). */
  onNavigate: (location: AppLocation) => void
  onSelectSession: (workspaceId: string, sessionId: string) => void
  onCreateSession: (workspaceId: string) => Promise<void>
  onCollapse: () => void
  onSwitchWorkspace: (workspaceId: string) => void
}) {
  const state = useSyncExternalStore(store.subscribe, store.getState)
  const treeRef = useRef<HTMLElement>(null)
  const settingsMenu = useDismissablePopover()
  const [actionError, setActionError] = useState<string | null>(null)
  const [creatingIn, setCreatingIn] = useState<string | null>(null)
  const [managing, setManaging] = useState<string | null>(null)
  const approvalRows = Array.isArray(pendingApprovals) ? pendingApprovals : []
  const pendingBySession = new Map<string, number>()
  for (const approval of approvalRows) pendingBySession.set(approval.session_id, (pendingBySession.get(approval.session_id) ?? 0) + 1)

  useEffect(() => { void store.loadAll() }, [store])
  useEffect(() => {
    if (activeWorkspaceId !== null) void store.refreshWorkspace(activeWorkspaceId)
  }, [store, activeWorkspaceId, cursor])

  const scrollToSession = (workspaceId: string, sessionId: string) => {
    requestAnimationFrame(() => {
      treeRef.current?.querySelector(`[data-session-row="${workspaceId}:${sessionId}"]`)
        ?.scrollIntoView({block: 'nearest'})
    })
  }
  const openSession = (workspaceId: string, sessionId: string) => {
    store.expandGroup(workspaceId)
    onSelectSession(workspaceId, sessionId)
    scrollToSession(workspaceId, sessionId)
  }
  const create = async (workspaceId: string) => {
    if (creatingIn !== null) return
    setCreatingIn(workspaceId); setActionError(null)
    try { await onCreateSession(workspaceId) } catch (error) {
      setActionError(error instanceof Error ? error.message : '创建失败；重试使用同一请求编号')
    } finally { setCreatingIn(null) }
  }
  // After a workspace remount the tree re-renders from the surviving store;
  // bring the selected session back into view.
  useEffect(() => {
    if (activeWorkspaceId !== null && activeSessionId !== null) {
      scrollToSession(activeWorkspaceId, activeSessionId)
    }
  }, [activeWorkspaceId, activeSessionId])

  return <>
    <header className="sidebar-top">
      <div className="sidebar-brand"><h1>Morrow</h1></div>
      <span className="sidebar-top-actions">
        <button type="button" className="icon-button" aria-label="搜索对话" title="搜索对话"
          aria-expanded={state.searchOpen} onClick={() => store.setSearchOpen(!state.searchOpen)}>
          <IconSearch/>
        </button>
        <button type="button" className="icon-button" aria-label="收起侧栏" title="收起侧栏" onClick={onCollapse}>
          <IconPanel/>
        </button>
      </span>
    </header>
    {state.searchOpen && <div className="sidebar-search">
      <input autoFocus aria-label="搜索对话" className="workspace-input" placeholder="搜索对话…"
        value={state.search} onChange={event => store.setSearch(event.target.value)}
        onKeyDown={event => {if (event.key === 'Escape') store.setSearchOpen(false)}}/>
      <details className="row-menu filter-menu">
        <summary className="icon-button" aria-label="筛选会话" title="筛选会话"><IconFilter/></summary>
        <div className="row-menu-popover">
          <label><input type="checkbox" checked={state.archived} onChange={event => store.setArchived(event.target.checked)}/> 显示已归档</label>
          <label><input type="checkbox" checked={state.includeExecution} onChange={event => store.setIncludeExecution(event.target.checked)}/> 显示执行会话</label>
        </div>
      </details>
    </div>}
    <nav className="sidebar-nav" aria-label="快捷导航">
      <button type="button" disabled={creatingIn !== null || activeWorkspaceId === null}
        onClick={() => {if (activeWorkspaceId !== null) void create(activeWorkspaceId)}}>
        <IconCompose/><span>新对话</span>
      </button>
      <button type="button" aria-current={location.kind === 'knowledge' ? 'page' : undefined}
        onClick={() => onNavigate({kind:'knowledge',section: location.kind === 'knowledge' ? location.section : 'profile'})}>
        <IconBook/><span>项目知识与偏好</span>
      </button>
      <button type="button" aria-current={location.kind === 'tools' ? 'page' : undefined}
        onClick={() => onNavigate({kind:'tools',section: location.kind === 'tools' ? location.section : 'skills'})}>
        <IconPlug/><span>技能与 MCP 工具</span>
      </button>
      <button type="button" aria-current={location.kind === 'editor' && location.target === 'workflows' ? 'page' : undefined}
        onClick={() => onNavigate({kind:'editor',target:'workflows'})}>
        <IconWorkflow/><span>工作流定义</span>
      </button>
    </nav>
    <nav ref={treeRef} className="sidebar-tree" aria-label="工作区与会话">
      <p className="tree-caption" aria-hidden="true">工作区</p>
      {state.workspaces.map(workspace => (
        <WorkspaceGroup
          key={workspace.workspace_id}
          store={store}
          workspace={workspace}
          sessions={state.sessions[workspace.workspace_id] ?? []}
          visible={state.visible[workspace.workspace_id] ?? DEFAULT_VISIBLE_SESSIONS}
          nextCursor={state.nextCursor[workspace.workspace_id] ?? null}
          loadError={state.errors[workspace.workspace_id] ?? null}
          collapsed={state.collapsed[workspace.workspace_id] ?? false}
          loadingAll={state.loading}
          searching={state.committedSearch !== ''}
          isCurrent={workspace.workspace_id === activeWorkspaceId}
          currentSessionId={workspace.workspace_id === activeWorkspaceId ? activeSessionId : null}
          creating={creatingIn === workspace.workspace_id}
          onOpenSession={openSession}
          onSwitchWorkspace={id => {setActionError(null); onSwitchWorkspace(id)}}
          onCreate={id => void create(id)}
          onManage={setManaging}
          pendingBySession={pendingBySession}
        />
      ))}
      {state.workspaces.length === 0 && (
        <p className="tree-empty">{state.loading ? '正在加载…' : state.error ?? '暂无工作区；可在设置中打开工作区管理。'}</p>
      )}
      {actionError !== null && <p role="alert" className="tree-error">{actionError}</p>}
    </nav>
    <footer className="sidebar-footer-row">
      <span className="footer-actions">
        <button type="button" className="settings-entry" title="设置"
          aria-current={location.kind === 'settings' ? 'page' : undefined}
          onClick={() => onNavigate({kind:'settings',section:'providers'})}>
          <IconGear/><span>设置</span>
        </button>
        <details ref={settingsMenu} className="row-menu settings-menu">
          <summary className="icon-button" title="更多选项（草稿管理）" aria-label="更多选项">
            <IconDots/>
          </summary>
          <SettingsPopover />
        </details>
      </span>
      <span className="footer-status" title={CONNECTION_LABELS[connection]}>
        <i aria-hidden="true" className={`connection-dot connection-${connection}`}/>
        <span aria-live="polite">{CONNECTION_SHORT[connection]}</span>
        {(Array.isArray(pendingApprovals) ? pendingApprovals.length : pendingApprovals) > 0 && <span className="footer-approvals">待审批 {Array.isArray(pendingApprovals) ? pendingApprovals.length : pendingApprovals}</span>}
      </span>
    </footer>
    <WorkspaceManagerDialog
      client={store.client}
      selected={activeWorkspaceId}
      onSelect={onSwitchWorkspace}
      open={managing !== null}
      onClose={() => setManaging(null)}
      initialTarget={managing}
      onChange={() => void store.loadAll()}
    />
  </>
}

function closeMenu(menu: {current: HTMLDetailsElement | null}) {
  if (menu.current) menu.current.open = false
}

/**
 * One workspace group: a fixed 36px header row (collapse arrow + folder +
 * name, with ＋ and … on hover) over its indented session rows.
 */
function WorkspaceGroup({
  store, workspace, sessions, visible, nextCursor, loadError, collapsed, loadingAll, searching,
  isCurrent, currentSessionId, creating, onOpenSession, onSwitchWorkspace, onCreate, onManage,
  pendingBySession,
}: {
  store: SidebarStore
  workspace: WorkspaceEntry
  sessions: SessionWire[]
  visible: number
  nextCursor: string | null
  /** Set when the group's last refresh failed; the previous rows are kept. */
  loadError: string | null
  collapsed: boolean
  loadingAll: boolean
  searching: boolean
  isCurrent: boolean
  currentSessionId: string | null
  creating: boolean
  onOpenSession: (workspaceId: string, sessionId: string) => void
  onSwitchWorkspace: (workspaceId: string) => void
  onCreate: (workspaceId: string) => void
  onManage: (workspaceId: string) => void
  pendingBySession: Map<string, number>
}) {
  const name = workspace.display_name
  const shown = sessions.slice(0, visible)
  const hidden = sessions.length - shown.length
  const wsMenu = useDismissablePopover()
  return <section className="ws-group">
    <div className={`ws-row${isCurrent ? ' is-active' : ''}`}>
      <button type="button" className="ws-toggle" aria-expanded={!collapsed}
        aria-label={`${collapsed ? '展开' : '收起'}工作区 ${name}`} title={collapsed ? '展开' : '收起'}
        onClick={() => store.toggleCollapsed(workspace.workspace_id)}>
        <IconChevron/>
      </button>
      <button type="button" className="ws-main"
        title={workspace.available ? workspace.path : `${workspace.path}（目录失效）`}
        onClick={() => onSwitchWorkspace(workspace.workspace_id)}>
        <IconFolder/>
        <span className="ws-name">{name}</span>
        {!workspace.available && <span className="ws-badge">失效</span>}
      </button>
      <span className="row-actions">
        <button type="button" className="icon-button" disabled={creating} title={`在 ${name} 新建会话`}
          aria-label={`在 ${name} 新建会话`} onClick={() => onCreate(workspace.workspace_id)}>
          <IconPlus/>
        </button>
        <details ref={wsMenu} className="row-menu ws-menu">
          <summary className="icon-button" title={`工作区操作 · ${name}`} aria-label={`工作区操作：${name}`}>
            <IconDots/>
          </summary>
          <div className="row-menu-popover">
            <button type="button" onClick={() => {closeMenu(wsMenu); onManage(workspace.workspace_id)}}>管理工作区…</button>
          </div>
        </details>
      </span>
    </div>
    {!collapsed && <>
      {shown.length > 0 && <ul className="sess-rows">
        {shown.map(session => (
          <SessionRow key={session.session_id} workspaceId={workspace.workspace_id} session={session}
            current={session.session_id === currentSessionId} store={store}
            pendingCount={pendingBySession.get(session.session_id) ?? 0}
            onOpen={() => onOpenSession(workspace.workspace_id, session.session_id)}/>
        ))}
      </ul>}
      {shown.length === 0 && <p className="sess-empty" role={loadError !== null ? 'alert' : undefined}>
        {loadingAll ? '正在加载…' : loadError ?? (searching ? '无匹配会话' : '暂无会话')}</p>}
      {(hidden > 0 || nextCursor !== null) && (
        <button type="button" className="sess-more"
          onClick={() => void store.loadMore(workspace.workspace_id)}>
          显示更多{hidden > 0 ? `（${hidden}）` : ''}
        </button>
      )}
    </>}
  </section>
}

/** One session row: single-line title with ellipsis, status dot, and a … menu. */
export function SessionRow({workspaceId, session, current, store, pendingCount = 0, onOpen}: {
  workspaceId: string
  session: SessionWire
  current: boolean
  store: SidebarStore
  pendingCount?: number
  onOpen: () => void
}) {
  const title = sessionTitle(session)
  const running = session.lifecycle === 'active' && session.current_task_run_id !== null
  const attention = session.health !== 'ok'
  const archived = session.lifecycle === 'archived'
  const suffix = [archived ? '已归档' : '', attention ? `健康状态 ${session.health}` : '', formatTimestamp(session.created_at)]
    .filter(Boolean).join(' · ')
  return <li className={`sess-row${current ? ' is-current' : ''}${archived ? ' is-archived' : ''}`}
    data-session-row={`${workspaceId}:${session.session_id}`}>
    <button type="button" className="sess-main" aria-current={current ? 'true' : undefined}
      title={`${title} · ${suffix}`} onClick={onOpen}>
      {session.metadata?.pinned && <span className="sess-pin" title="已置顶" aria-label="已置顶"><IconPin/></span>}
      <span className="sess-title">{title}</span>
      {running && <i className="sess-dot dot-run" title="运行中" aria-label="运行中"/>}
      {!running && attention && <i className="sess-dot dot-attention" title="需要处理" aria-label="需要处理"/>}
      {pendingCount > 0 && <span className="sess-approval-badge" title={`${pendingCount} 个待审批`} aria-label={`${pendingCount} 个待审批`}>{pendingCount}</span>}
    </button>
    <SessionRowMenu workspaceId={workspaceId} session={session} store={store}/>
  </li>
}

/** … menu per session row: rename, pin, archive / restore. */
function SessionRowMenu({workspaceId, session, store}: {
  workspaceId: string; session: SessionWire; store: SidebarStore
}) {
  const menu = useDismissablePopover()
  const [title, setTitle] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const label = sessionTitle(session)
  const run = (fn: () => Promise<void>) => {
    setBusy(true); setError(null)
    fn().then(() => {closeMenu(menu)}, cause => setError(cause instanceof Error ? cause.message : '操作失败'))
      .finally(() => setBusy(false))
  }
  return <details ref={menu} className="row-menu sess-menu"
    onToggle={event => {if ((event.target as HTMLDetailsElement).open) setTitle(session.metadata?.title ?? '')}}>
    <summary className="icon-button" title={`会话操作 · ${label}`} aria-label={`会话操作：${label}`}>
      <IconDots/>
    </summary>
    <div className="row-menu-popover">
      {error !== null && <p role="alert" className="menu-error">{error}</p>}
      <label className="menu-field">对话名称
        <input aria-label="对话名称" className="workspace-input" maxLength={120} value={title}
          onChange={event => setTitle(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter' && title.trim() && !busy) run(() => store.renameSession(workspaceId, session, title.trim()))
          }}/>
      </label>
      <button type="button" disabled={busy || !title.trim() || title.trim() === (session.metadata?.title ?? '')}
        onClick={() => run(() => store.renameSession(workspaceId, session, title.trim()))}>保存名称</button>
      <button type="button" disabled={busy} onClick={() => run(() => store.togglePinned(workspaceId, session))}>
        {session.metadata?.pinned ? '取消置顶' : '置顶'}
      </button>
      {session.lifecycle === 'archived'
        ? <button type="button" disabled={busy} onClick={() => run(() => store.setArchivedSession(workspaceId, session, false))}>恢复对话</button>
        : <button type="button" disabled={busy} onClick={() => run(() => store.setArchivedSession(workspaceId, session, true))}>归档对话</button>}
    </div>
  </details>
}

/** Session-scoped composer drafts. Theme and diagnostics now live in settings. */
function SettingsPopover() {
  const [draftsOpen, setDraftsOpen] = useState(false)
  return <div className="row-menu-popover sidebar-settings-popover">
    <button type="button" aria-expanded={draftsOpen} onClick={() => setDraftsOpen(value => !value)}>草稿管理</button>
    {draftsOpen && <DraftManagerSection/>}
  </div>
}

/** Per-tab composer drafts, listed for copy + cleanup (storage quota recovery). */
function DraftManagerSection() {
  const [drafts] = useState(() => new DraftStorage(sessionStorage))
  // Bumped on every mutation so the entry list re-reads sessionStorage.
  const [, setRevision] = useState(0)
  const entries = Object.entries(drafts.entries())
  const clear = (key: string) => {
    drafts.remove(key)
    window.dispatchEvent(new CustomEvent('morrow:draft-cleared', {detail: key}))
    setRevision(value => value + 1)
  }
  return <div className="draft-manager">
    <p>草稿仅保留至此标签页关闭。</p>
    {entries.length === 0 && <p className="draft-none">暂无草稿。</p>}
    {entries.map(([key, value]) => {
      const [workspaceId, sessionId] = parseDraftKey(key)
      return <div key={key} className="draft-entry">
        <p className="draft-key" title={key}>{sessionId.slice(-8)} · {workspaceId.slice(0, 16)} · {value.length} 字符</p>
        <div className="draft-entry-actions">
          <button type="button" onClick={() => {void navigator.clipboard.writeText(value)}}>复制草稿</button>
          <button type="button" onClick={() => clear(key)}>清除此草稿</button>
        </div>
      </div>
    })}
  </div>
}

function parseDraftKey(key: string): [string, string] {
  try {
    const value: unknown = JSON.parse(key)
    if (Array.isArray(value) && value.length === 2 && value.every(item => typeof item === 'string')) {
      return value as [string, string]
    }
  } catch { /* fall through to the raw key */ }
  return [key, key]
}

/* 16-grid stroke icons, sized 15px (12px for the chevron / pin). */
function Icon({children, size = 15}: {children: ReactNode; size?: number}) {
  return <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor"
    strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
}
const IconSearch = () => <Icon><circle cx="7.2" cy="7.2" r="4.4"/><path d="M10.6 10.6 14 14"/></Icon>
const IconPanel = () => <Icon><rect x="1.8" y="2.8" width="12.4" height="10.4" rx="2"/><path d="M6.2 2.8v10.4"/><path d="M11.6 6.4 9.8 8l1.8 1.6"/></Icon>
const IconCompose = () => <Icon><path d="M8 2.5H4A1.5 1.5 0 0 0 2.5 4v8A1.5 1.5 0 0 0 4 13.5h8a1.5 1.5 0 0 0 1.5-1.5V8"/><path d="M11.2 2.7a1.4 1.4 0 0 1 2 2L7.6 10.3l-2.6.6.6-2.6Z"/></Icon>
const IconBook = () => <Icon><path d="M2.8 3a1.5 1.5 0 0 1 1.5-1.5h8.4v11.7H4.3A1.5 1.5 0 0 0 2.8 14.7V3Z"/><path d="M6 5.2h4M6 8h2.8"/></Icon>
const IconPlug = () => <Icon><path d="M6 2.5v3M10 2.5v3M4.5 5.5h7v3a3.5 3.5 0 0 1-7 0v-3ZM8 12v2.5"/></Icon>
const IconWorkflow = () => <Icon><path d="M2.5 2h2.2a1 1 0 0 1 1 1v2a1 1 0 0 1-1 1H2.5a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1Zm8.8 0h2.2a1 1 0 0 1 1 1v2a1 1 0 0 1-1 1h-2.2a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1ZM6.9 10h2.2a1 1 0 0 1 1 1v2a1 1 0 0 1-1 1H6.9a1 1 0 0 1-1-1v-2a1 1 0 0 1 1-1ZM3.6 6v1.4a1.8 1.8 0 0 0 1.8 1.8h5.2a1.8 1.8 0 0 0 1.8-1.8V6M8 9.2v.8"/></Icon>
const IconChevron = () => <Icon size={12}><path d="m6 3.5 4.5 4.5L6 12.5"/></Icon>
const IconFolder = () => <Icon><path d="M1.9 4.2A1.4 1.4 0 0 1 3.3 2.8h2.9l1.4 1.7h5.1a1.4 1.4 0 0 1 1.4 1.4v6a1.4 1.4 0 0 1-1.4 1.4H3.3a1.4 1.4 0 0 1-1.4-1.4Z"/></Icon>
const IconPlus = () => <Icon><path d="M8 3.5v9M3.5 8h9"/></Icon>
const IconDots = () => <Icon><circle cx="3.2" cy="8" r="1" fill="currentColor" stroke="none"/><circle cx="8" cy="8" r="1" fill="currentColor" stroke="none"/><circle cx="12.8" cy="8" r="1" fill="currentColor" stroke="none"/></Icon>
const IconGear = () => <Icon><circle cx="8" cy="8" r="2.2"/><path d="M8 1.8v1.7M8 12.5v1.7M2.9 4.9l1.5.9M11.6 10.2l1.5.9M2.9 11.1l1.5-.9M11.6 5.8l1.5-.9"/></Icon>
const IconFilter = () => <Icon><path d="M2.5 3.5h11l-4.2 5v4l-2.6 1.4v-5.4Z"/></Icon>
const IconPin = () => <Icon size={11}><path d="m8 1.8 1.5 3.2 3.4.5-2.5 2.4.6 3.4L8 9.6l-3 1.7.6-3.4L3.1 5.5l3.4-.5Z"/></Icon>
