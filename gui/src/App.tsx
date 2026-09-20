import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'
import { ApiClient } from './api/client'
import { NavigationStore, type AppLocation } from './state/navigation'
import { SidebarStore } from './state/sidebar'
import { SyncStore } from './state/sync'
import { AppShell } from './views/AppShell'
import { applyTheme, persistTheme, readStoredTheme, type Theme } from './state/theme'
import { SettingsPage } from './views/settings/SettingsPage'
import { WorkspaceManager } from './views/WorkspaceManager'
import type { SessionWire } from './api/types'
import { newLocalDraftId } from './state/localDraft'

/** Start the local GUI without a browser session token. */
export default function App() {
  const [theme, setTheme] = useState<Theme>(() => readStoredTheme())

  useEffect(() => {
    applyTheme(theme)
    persistTheme(theme)
  }, [theme])

  return <BootedApp theme={theme} onThemeChange={setTheme} />
}

function BootedApp({
  theme,
  onThemeChange,
}: {
  theme: Theme
  onThemeChange: (theme: Theme) => void
}) {
  const [client] = useState(() => new ApiClient({baseUrl:'',token:''}))
  const [sidebarStore] = useState(() => new SidebarStore(client))
  const [navigation] = useState(() => new NavigationStore())
  useEffect(() => navigation.start(), [navigation])
  const { location, returnTo } = useSyncExternalStore(navigation.subscribe, navigation.getState)
  const [workspaceId,setWorkspaceId] = useState<string|null>(null)
  // Selected session per workspace; the sidebar tree writes it so a single
  // click can switch workspace AND session in one step.
  const [selectedByWorkspace,setSelectedByWorkspace] = useState<Record<string,string>>({})
  useEffect(()=>{let alive=true;void Promise.all([client.workspaces()]).then(([list])=>{
    const saved=sessionStorage.getItem('morrow.workspace');
    if(alive)setWorkspaceId(list.items.find(w=>w.workspace_id===saved)?.workspace_id??list.items[0]?.workspace_id??'')
  });return()=>{alive=false}},[client])
  const select=(id:string)=>{sessionStorage.setItem('morrow.workspace',id);setWorkspaceId(id)}
  /**
   * Draft protection: every workspace/session switch first resolves the
   * navigation store's guard stack (save / keep draft / cancel), then fires
   * the cancellable guard event the active chat listens to (unsaved-draft
   * storage failures call preventDefault and keep the user in place).
   */
  const selectSession=useCallback((wsId:string,sid:string)=>{
    void (async () => {
      if(!(await navigation.confirmLeave()))return
      if(!window.dispatchEvent(new Event('morrow:before-workspace-switch',{cancelable:true})))return
      sessionStorage.setItem(`morrow.selected.${wsId}`,sid)
      setSelectedByWorkspace(current=>({...current,[wsId]:sid}))
      if(wsId!==workspaceId)select(wsId)
      await navigation.navigate({kind:'chat'})
    })()
  },[navigation,workspaceId])
  const selectWorkspace=useCallback((id:string)=>{
    void (async () => {
      if(!(await navigation.confirmLeave()))return
      if(!window.dispatchEvent(new Event('morrow:before-workspace-switch',{cancelable:true})))return
      select(id)
    })()
  },[navigation])
  /** Start a browser-only draft. No Core call is made until its first send. */
  const startDraft=useCallback(async(wsId:string)=>{
    selectSession(wsId,newLocalDraftId())
  },[selectSession])
  /** Persist a Session on first Prompt submission. */
  const createSession=useCallback(async(wsId:string):Promise<SessionWire>=>{
    // Keep a just-created row out of the sidebar until its first Prompt is
    // accepted; a failed send must not look like a real empty conversation.
    return sidebarStore.createSession(wsId, {upsert: false})
  },[sidebarStore])
  const openSettings=useCallback(()=>{
    void navigation.navigate({kind:'settings',section:'providers'},{kind:'chat'})
  },[navigation])
  // Settings stay reachable without a workspace (provider / appearance, N01).
  const settingsOnly=!workspaceId&&location.kind==='settings'
  const backTo:AppLocation=returnTo??{kind:'chat'}
  const settingsSection=location.kind==='settings'?location.section:'providers'
  return <div className="app-root">
    {settingsOnly && (
      <SettingsPage
        client={client}
        section={settingsSection}
        onNavigate={section => { void navigation.navigate({kind:'settings',section}) }}
        onBack={() => { void navigation.navigate(backTo) }}
        theme={theme}
        onThemeChange={onThemeChange}
      />
    )}
    {!settingsOnly&&!workspaceId&&<WorkspaceManager client={client} selected={workspaceId} onSelect={selectWorkspace}/>}
    {!settingsOnly&&workspaceId===''&&<NoWorkspaceView onOpenSettings={openSettings}/>}
    {workspaceId&&<ActiveWorkspace key={workspaceId} workspaceId={workspaceId} sidebarStore={sidebarStore}
      navigation={navigation}
      selectedSessionId={selectedByWorkspace[workspaceId] ?? sessionStorage.getItem(`morrow.selected.${workspaceId}`)}
      onSelectSession={selectSession} onSwitchWorkspace={selectWorkspace} onCreateSession={startDraft} onPersistSession={createSession}
      theme={theme} onThemeChange={onThemeChange}/>}
  </div>
}

function ActiveWorkspace({workspaceId,sidebarStore,navigation,selectedSessionId,onSelectSession,onSwitchWorkspace,onCreateSession,onPersistSession,theme,onThemeChange}:{
  workspaceId:string;sidebarStore:SidebarStore
  navigation:NavigationStore
  selectedSessionId:string|null;onSelectSession:(wsId:string,sid:string)=>void;onSwitchWorkspace:(id:string)=>void;onCreateSession:(wsId:string)=>Promise<void>;onPersistSession:(wsId:string)=>Promise<SessionWire>
  theme:Theme;onThemeChange:(theme:Theme)=>void
}) {
  const [{client,store}]=useState(()=>{const token='';const client=new ApiClient({baseUrl:'',token,workspaceId});return{client,store:new SyncStore({client,token})}})
  useEffect(()=>{void store.start();return()=>store.stop()},[store])
  return <AppShell workspaceId={workspaceId} client={client} store={store} sidebarStore={sidebarStore}
    navigation={navigation}
    selectedSessionId={selectedSessionId} onSelectSession={onSelectSession}
    onSwitchWorkspace={onSwitchWorkspace} onCreateSession={onCreateSession} onPersistSession={onPersistSession}
    theme={theme} onThemeChange={onThemeChange}/>
}

/** Registered-workspace list is empty: point at the manager and settings. */
function NoWorkspaceView({onOpenSettings}:{onOpenSettings:()=>void}) {
  return <div className="p-6"><p>尚未选择工作区。请打开“管理工作区”创建或注册文件夹。</p><button className="editor-button" onClick={onOpenSettings}>模型设置</button></div>
}
