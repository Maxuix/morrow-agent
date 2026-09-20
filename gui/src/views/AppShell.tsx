import { ChatWorkspace } from './ChatWorkspace'
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import type { ApiClient } from '../api/client'
import type { RunViewWire, SessionWire, WorkflowRunWire } from '../api/types'
import type { SidebarStore } from '../state/sidebar'
import {
  viewForLocation,
  type AppLocation,
  type NavigationStore,
} from '../state/navigation'
import type { PlanningGenerationTransport } from '../state/taskPlan'
import type { SyncStore } from '../state/sync'
import { ConnectionBanner } from './ConnectionBanner'
import { PatchEditor } from './PatchEditor'
import { WorkspaceSidebar } from './Sidebar'
import type { Theme } from '../state/theme'
import { WorkspaceKnowledgePage } from './knowledge/WorkspaceKnowledgePage'
import { WorkspaceToolsPage } from './tools/WorkspaceToolsPage'
import { WorkspaceEditorPage } from './editor/WorkspaceEditorPage'
import { SettingsPage } from './settings/SettingsPage'
import { isLocalDraft } from '../state/localDraft'

const MOBILE_SIDEBAR = '(max-width: 700px)'

/**
 * Default Chat shell with retained management views. The Chat stays mounted
 * across editor navigation so its draft and transcript position survive.
 * The sidebar tree itself is store-driven: `sidebarStore` lives at the App
 * level, so workspace switches remount this shell without losing the grouped
 * list, its fold state, or the search inputs.
 */
export function AppShell({
  client,
  store,
  sidebarStore,
  workspaceId,
  navigation,
  selectedSessionId,
  onSelectSession,
  onSwitchWorkspace,
  onCreateSession,
  onPersistSession,
  theme,
  onThemeChange,
}: {
  client: ApiClient
  store: SyncStore
  sidebarStore: SidebarStore
  workspaceId: string | null
  navigation: NavigationStore
  selectedSessionId: string | null
  onSelectSession: (workspaceId: string, sessionId: string) => void
  onSwitchWorkspace: (workspaceId: string) => void
  onCreateSession: (workspaceId: string) => Promise<void>
  /** Durable creation is deferred until the first Prompt in a local draft. */
  onPersistSession?: (workspaceId: string) => Promise<SessionWire>
  theme: Theme
  onThemeChange: (theme: Theme) => void
}) {
  // Desktop starts with the sidebar open and can collapse it from the top row;
  // mobile starts collapsed and uses the fixed toggle + overlay.
  const [mobileOverlay, setMobileOverlay] = useState(
    () => window.matchMedia(MOBILE_SIDEBAR).matches,
  )
  const [sidebarOpen, setSidebarOpen] = useState(
    () => !window.matchMedia(MOBILE_SIDEBAR).matches,
  )
  useEffect(() => {
    const media = window.matchMedia(MOBILE_SIDEBAR)
    const sync = () => {
      setMobileOverlay(media.matches)
      setSidebarOpen(false)
    }
    media.addEventListener('change', sync)
    return () => media.removeEventListener('change', sync)
  }, [])
  const { location, returnTo } = useSyncExternalStore(navigation.subscribe, navigation.getState)
  const activeView = viewForLocation(location)
  const navigate = useCallback(
    (target: AppLocation, returnTarget?: AppLocation) => navigation.navigate(target, returnTarget),
    [navigation],
  )
  // Every main-area page offers 返回对话: it goes to the location recorded as
  // returnTo (usually the originating chat), falling back to chat.
  const backTarget = returnTo ?? { kind: 'chat' } as AppLocation
  const backToChat = useCallback(() => { void navigate(backTarget) }, [navigate, backTarget])
  // Sidebar top-level navigation carries returnTo so deep pages can return to
  // the originating chat; navigating between pages keeps the original target.
  const navigateFromSidebar = useCallback(
    (target: AppLocation) => navigate(target, location.kind === 'chat' ? { kind: 'chat' } : backTarget),
    [navigate, location, backTarget],
  )
  const openFromChat = useCallback((target: AppLocation, returnTarget?: AppLocation) => {
    void navigate(target, returnTarget ?? { kind: 'chat' })
  }, [navigate])
  // Draft-owning forms each register on the navigation store's guard stack so
  // every leave path (nav click, workspace/session switch, browser back)
  // resolves unsaved changes before committing. Multiple forms may be dirty.
  const registerGuard = navigation.registerDirtyGuard
  // Generation-scoped planning pause/resume (B/D coordination, A09): the
  // transport adapts the store's seam onto the coordinator-owned client
  // methods; the operations routes live on the coordinator's endpoints.
  // Memoized on the client: a fresh object per render would recreate every
  // session's TaskPlanStore and drop its in-flight control state.
  const generationTransport: PlanningGenerationTransport = useMemo(
    () => ({
      pauseGeneration: (workspace, session, operationId, body) =>
        client.taskPlanGenerationPause(workspace, session, operationId, body),
      resumeGeneration: (workspace, session, operationId, body) =>
        client.taskPlanGenerationResume(workspace, session, operationId, body),
    }),
    [client],
  )
  const state = useSyncExternalStore(
    useCallback((listener: () => void) => store.subscribe(listener), [store]),
    useCallback(() => store.getState(), [store]),
  )

  // The edit-pending flow replaces the chat columns: pause → edit Future
  // nodes → preview diff + risk → confirm → continuation child.
  const [patchContext, setPatchContext] = useState<{
    run: WorkflowRunWire
    view: RunViewWire
  } | null>(null)
  const editingPatch = patchContext !== null && workspaceId !== null
  const pendingApprovals = [...state.pendingApprovals.values()]

  return (
    <div className={`app-shell${sidebarOpen ? '' : ' is-sidebar-closed'}`}
      onKeyDown={event => {if(event.key === 'Escape' && mobileOverlay) setSidebarOpen(false)}}>
      {!sidebarOpen && <button className="sidebar-toggle editor-button" aria-controls="workspace-sidebar" aria-expanded={false} onClick={() => setSidebarOpen(true)}>导航与会话</button>}
      {mobileOverlay && sidebarOpen && <button className="sidebar-backdrop" aria-label="关闭导航遮罩" onClick={() => setSidebarOpen(false)}/>}
      <aside id="workspace-sidebar" className={`app-sidebar ${mobileOverlay && sidebarOpen ? 'is-open' : ''}`} aria-label="导航与会话">
        <WorkspaceSidebar
          store={sidebarStore}
          activeWorkspaceId={workspaceId}
          activeSessionId={selectedSessionId}
          cursor={state.cursor}
          connection={state.connection}
          pendingApprovals={pendingApprovals}
          location={location}
          onNavigate={target => {void navigateFromSidebar(target).then(ok => {if (ok && mobileOverlay) setSidebarOpen(false)})}}
          onSelectSession={onSelectSession}
          onCreateSession={onCreateSession}
          onSwitchWorkspace={onSwitchWorkspace}
          onCollapse={() => setSidebarOpen(false)}
        />
      </aside>
      <div className="app-content" inert={mobileOverlay && sidebarOpen}>
      <ConnectionBanner connection={state.connection} onRetry={() => store.retry()} />

      <div className={activeView === 'chat' && !editingPatch ? 'flex min-h-0 flex-1' : 'hidden'}>{workspaceId && <ChatWorkspace client={client} store={store} workspaceId={workspaceId} cursor={state.cursor} selected={selectedSessionId} onSelectSession={onSelectSession} onCreateSession={onCreateSession} onPersistSession={onPersistSession} onSessionUpserted={(session) => sidebarStore.upsertSession(workspaceId, session)} onSettings={() => {void navigate({kind:'settings',section:'providers'},{kind:'chat'})}} onWorkflowEdit={(run, view) => {setPatchContext({run,view})}} onNavigate={openFromChat} onPauseTurn={(ws, sid, request) => client.chatGuiPause(ws, sid, request)} planningGenerationTransport={generationTransport} registerGuard={registerGuard} />}</div>
      {activeView === 'chat' && editingPatch && workspaceId !== null && patchContext !== null ? (
        <PatchEditor
          client={client}
          run={patchContext.run}
          view={patchContext.view}
          workspaceId={workspaceId}
          onClose={() => setPatchContext(null)}
        />
      ) : activeView === 'knowledge' && workspaceId !== null ? (
        <WorkspaceKnowledgePage client={client} workspaceId={workspaceId}
          section={location.kind === 'knowledge' ? location.section : 'profile'}
          focus={location.kind === 'knowledge' ? location.focus : undefined}
          connected={state.connection === 'live'} registerGuard={registerGuard}
          onNavigate={section => {void navigate({kind:'knowledge',section})}}
          onBack={backToChat} />
      ) : activeView === 'tools' ? (
        <WorkspaceToolsPage client={client} workspaceId={workspaceId}
          section={location.kind === 'tools' ? location.section : 'skills'}
          item={location.kind === 'tools' ? location.item : undefined}
          connected={state.connection === 'live'} registerGuard={registerGuard}
          onNavigate={section => {void navigate({kind:'tools',section})}}
          onBack={backToChat} />
      ) : activeView === 'settings' ? (
        <SettingsPage client={client}
          section={location.kind === 'settings' ? location.section : 'providers'}
          workspaceId={workspaceId}
          sessionId={isLocalDraft(selectedSessionId) ? null : selectedSessionId}
          connection={state.connection}
          theme={theme}
          onThemeChange={onThemeChange}
          registerGuard={registerGuard}
          onManageTools={() => { void navigate({ kind: 'tools', section: 'skills' }, location) }}
          onNavigate={section => {void navigate({kind:'settings',section})}}
          onBack={backToChat} />
      ) : activeView === 'edit' ? (
        <WorkspaceEditorPage client={client}
          onManageTools={() => { void navigate({ kind: 'tools', section: 'skills' }, location) }}
          onManageAgents={() => { void navigate({ kind: 'settings', section: 'agents' }, location) }}
          onBack={backToChat}
          registerGuard={registerGuard} />
      ) : null}

      </div>
    </div>
  )
}
