import { outcomeSummary } from './lib/outcomePresentation'
import { applyAssetCommand, assetCommandEntries } from './knowledgeCommands'
import type { AppLocation } from '../state/navigation'
import { AttachmentDraftStorage, useAttachments } from '../state/attachments'
import { AppErrorBoundary } from '../components/ErrorBoundary'
import { PublishedWorkflowPicker, type WorkflowChoice } from './ChatWorkflowInput'
import { AttachmentComposer } from './AttachmentComposer'
import type { ChatSettings } from '../api/settings'
import { ChatModelControl, ChatPermissionControl, useChatSettings } from './ChatSettingsBar'
import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { ApiError, type ApiClient } from '../api/client'
import { taskWorkflowAvailable, type ChatCapabilities, type InteractionInput, type RecoveryStatus, type StageFrame } from '../api/chat'
import type { ApprovalWire, SessionWire, TaskRunWire, WorkflowRunWire, RunViewWire } from '../api/types'
import type { SyncStore } from '../state/sync'
import { ChatStore, DraftStorage, OutboxStorage } from '../state/chat'
import { ActivityStore, groupByRun, type ActivityRun, type ActivityState } from '../state/activity'
import { planningGenerationOf, TaskPlanStore, type PlanningGenerationTransport } from '../state/taskPlan'
import { ChatMessage, Markdown } from './ChatMessage'
import { ActivityTimeline } from './ActivityTimeline'
import { ChatComposer } from './ChatComposer'
import { InlineApprovalCard } from './chat/InlineApprovalCard'
import { mergeApprovalViews, uniqueApprovals } from '../state/approvalDecision'
import { liveStageFrames } from './lib/stages'
import { editForkState } from './lib/messageActions'
import { buildTranscript, type TranscriptEntry, type WorkflowCardView } from './lib/transcript'
import { controlReceiptLabel, isExplicitPauseCommand, isPureContinueCommand, looksLikeStartCommand, planRunTopologyDefault, workflowTarget } from './lib/taskPlan'
import { chatOperation, chatOperationEntries, type ChatOperation } from './lib/chatOperations'
import {
  executionAcceptsInput,
  executionBusy,
  executionControlTarget,
  executionControls,
  executionLabel,
  executionStopTarget,
  executionStopping,
  executionTimingFrozen,
  guiPauseRequestBody,
  pausedSendRoute,
  runningControlRoute,
} from './lib/execution'
import type { GuiPauseRequest } from '../api/contracts'
import { commandId } from './lib/editor'
import { SessionActions } from './SessionActions'
import { InspectorStore, type FileTarget, type InspectorKind, type InspectorTarget } from '../state/inspector'
import { FileBufferStore } from '../state/fileBuffer'
import type { DirtyGuard } from '../state/navigation'
import { ChatColumns } from './inspector/ChatColumns'
import { InspectorHeaderButtons } from './inspector/HeaderButtons'
import { RecoveryBanner } from './RecoveryBanner'
import { isLocalDraft } from '../state/localDraft'

/**
 * The chat surface. The session tree lives in the App-level sidebar; this
 * component receives the selected session (`selected`) and routes every
 * switch — including fork targets — through `onSelectSession`, which enforces
 * the cancellable draft guard before any workspace/session change.
 *
 * `onPauseTurn` is the transport for the ordinary-chat turn pause (frozen
 * `GuiPauseRequest`); the caller owns the HTTP entry. When it is absent the
 * pause control stays disabled with an honest reason — never a fake success.
 * `planningGenerationTransport` is the same seam for the generation-scoped
 * planning pause/resume (`/task-plan/operations/{id}/pause|resume`).
 */
export function ChatWorkspace({client, store, workspaceId, cursor, selected, onSelectSession, onCreateSession, onPersistSession, onSessionUpserted, onSettings, onWorkflowEdit, onNavigate, onPauseTurn, planningGenerationTransport, registerGuard}: {
  client: ApiClient; store: SyncStore; workspaceId: string; cursor: number
  selected: string | null
  onSelectSession: (wsId: string, sid: string) => void
  onCreateSession: (wsId: string) => Promise<void>
  onPersistSession?: (wsId: string) => Promise<SessionWire>
  onSessionUpserted: (session: SessionWire) => void
  onSettings: () => void; onWorkflowEdit: (run: WorkflowRunWire, view: RunViewWire) => void
  onNavigate: (location: AppLocation, returnTo?: AppLocation) => void
  onPauseTurn?: (workspaceId: string, sessionId: string, request: GuiPauseRequest) => Promise<unknown>
  planningGenerationTransport?: PlanningGenerationTransport
  registerGuard?: (guard: DirtyGuard) => () => void
}) {
  const [capabilities, setCapabilities] = useState<ChatCapabilities | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loadTick, setLoadTick] = useState(0)
  const [drafts] = useState(() => new DraftStorage(sessionStorage))
  // P09.3: a capabilities response from a previous workspace/cursor generation
  // must never land after a switch (workspace/session/generation isolation).
  // Only a workspace switch resets to the loading state; cursor bumps (every
  // snapshot frame, also covering Core restarts) refetch silently so the chat
  // subtree — composer, drafts, plan store wiring — never flickers mid-run.
  const capabilitiesWorkspace = useRef(workspaceId)
  useEffect(() => {
    let cancelled = false
    setError(null)
    if (capabilitiesWorkspace.current !== workspaceId) {
      capabilitiesWorkspace.current = workspaceId
      setCapabilities(null)
    }
    client.capabilities().then(caps => {
      if (cancelled) return
      if (caps.interaction_protocol_version !== 1 || !caps.features.chat?.available) throw new Error('当前 Core 不支持 Chat 协议 1。')
      setCapabilities(caps)
    }).catch((err: unknown) => {
      if (!cancelled) setError(err instanceof Error ? err.message : '无法加载会话')
    })
    return () => { cancelled = true }
  }, [client, workspaceId, cursor, loadTick]) // durable events also cover Core restarts
  // The task-plan store survives the capabilities refetch flicker (every
  // cursor bump re-runs the effect above, which would otherwise unmount the
  // chat subtree and wipe in-flight control receipts/notices). Only stable
  // primitives enter the memo — never `capabilities`, which resets to null
  // on each refetch — so the store instance is stable for the session.
  const planStore = useMemo(
    () =>
      selected === null
        ? null
        : new TaskPlanStore(client, workspaceId, selected, planningGenerationTransport),
    [client, workspaceId, selected, planningGenerationTransport],
  )
  // Nothing selected yet (first visit): fall back to the newest session so the
  // workspace opens on an existing conversation instead of an empty prompt.
  useEffect(() => {
    if (selected !== null) return
    let cancelled = false
    void client.searchSessions(workspaceId).then(page => {
      if (!cancelled && page.sessions[0] !== undefined) onSelectSession(workspaceId, page.sessions[0].session_id)
    }, () => {})
    return () => {cancelled = true}
  }, [client, workspaceId, selected, onSelectSession])
  const create = async () => {
    setError(null)
    try {
      await onCreateSession(workspaceId)
    } catch (e) {
      setError(e instanceof Error ? e.message : '创建失败；重试使用同一请求编号')
      throw e
    }
  }
  // Inspector 外壳状态独立于 Chat 正文，按
  // workspaceId + sessionId 隔离并持久化到 sessionStorage，跨工作区重建可恢复。
  const [inspector] = useState(() => new InspectorStore())
  // 文件编辑缓冲区：与 Inspector 外壳同生命周期，草稿只在内存中，切标签/隐藏
  // 面板不丢，但不写入任何浏览器存储。
  const [fileBuffers] = useState(() => new FileBufferStore(client))
  const draftSelected = isLocalDraft(selected)
  const persistSession = onPersistSession ?? ((wsId: string) => client.createChatSession(wsId, commandId('chat_session')))
  return <main className="chat-layout" data-workspace-id={workspaceId}>
    <div className="chat-main">{error && <div role="alert" className="p-3">{error} <button className="editor-button" onClick={() => setLoadTick(t => t + 1)}>重试连接</button></div>}
      {!capabilities ? <p className="p-6">正在连接 Chat…</p> : draftSelected ? <DraftSession client={client} workspace={workspaceId} draftId={selected!} capabilities={capabilities} drafts={drafts} onPersistSession={persistSession} onSelectSession={sid=>onSelectSession(workspaceId,sid)} onSessionUpserted={onSessionUpserted} /> : selected ? <AppErrorBoundary key={`${workspaceId}:${selected}`}><SessionChat key={`${workspaceId}:${selected}`} client={client} store={store} capabilities={capabilities} sessionId={selected} drafts={drafts} planStore={planStore!} inspector={inspector} fileBuffers={fileBuffers} registerGuard={registerGuard} onNew={create} onSessionUpserted={onSessionUpserted} onFork={child=>{onSessionUpserted(child);onSelectSession(workspaceId,child.session_id)}} onSelectSession={sid=>onSelectSession(workspaceId,sid)} onSettings={onSettings} onWorkflowEdit={onWorkflowEdit} onNavigate={onNavigate} onPauseTurn={onPauseTurn} planningGenerationTransport={planningGenerationTransport}/></AppErrorBoundary> : <div className="chat-empty"><h2>开始新的对话</h2><button className="editor-button" onClick={create}>开始新对话</button></div>}
    </div>
  </main>
}

/** Stable empty state for sessions whose Core does not advertise the activity stream. */
const NO_ACTIVITY: ActivityState = {status: 'offline', items: [], content: {}, activity_epoch: '', activity_sequence: 0}
const noopSubscribe = () => () => {}

/**
 * A new conversation is deliberately kept outside the durable Session tree.
 * The composer is still the normal ChatComposer, but it has no Session-bound
 * stores or effects, so opening this view cannot issue a snapshot/settings/
 * interaction request. The first successful Prompt creates the Session and
 * then hands the user to SessionChat for the normal live transcript.
 */
export function DraftSession({client, workspace, draftId, capabilities, drafts, onPersistSession, onSelectSession, onSessionUpserted}: {
  client: ApiClient; workspace: string; draftId: string; capabilities: ChatCapabilities; drafts: DraftStorage
  onPersistSession: (workspace: string) => Promise<SessionWire>
  onSelectSession: (sessionId: string) => void
  onSessionUpserted: (session: SessionWire) => void
}) {
  const [text, setText] = useState(() => drafts.read(workspace, draftId))
  const textRef = useRef(text)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [created, setCreated] = useState<SessionWire | null>(null)
  const inputRef = useRef<InteractionInput | null>(null)

  // A draft never reaches the operational store. Drop its browser-only text
  // when the user leaves this draft (tab close also clears sessionStorage), so
  // abandoned drafts cannot consume the global draft quota indefinitely.
  useEffect(() => () => {
    try { drafts.remove(drafts.key(workspace, draftId)) } catch { /* best effort cleanup */ }
  }, [drafts, workspace, draftId])

  const onText = (value: string) => {
    setText(value)
    textRef.current = value
    try {
      drafts.write(workspace, draftId, value)
      setError(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '草稿保存失败，请复制当前输入。')
    }
  }

  const submit = async () => {
    const value = textRef.current.trim()
    if (!value || pending) return
    setPending(true)
    setError(null)
    const input = inputRef.current ?? {
      client_message_id: commandId('chat'),
      text: textRef.current,
      intent: 'send' as const,
    }
    inputRef.current = input
    try {
      // If the first send was accepted by POST /sessions but its interaction
      // response was lost, retain the returned Session and never create twice.
      const session = created ?? await onPersistSession(workspace)
      if (!created) setCreated(session)
      let receipt
      try {
        receipt = await client.chatReceipt(workspace, session.session_id, input.client_message_id)
      } catch (cause) {
        if (!(cause instanceof ApiError) || cause.status !== 404) throw cause
      }
      if (!receipt) await client.chatSend(workspace, session.session_id, input)
      try { drafts.remove(drafts.key(workspace, draftId)) } catch { /* sessionStorage is best effort */ }
      inputRef.current = null
      onSessionUpserted(session)
      onSelectSession(session.session_id)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '发送失败，请重试。')
    } finally {
      setPending(false)
    }
  }

  return <section className="chat-session-workspace draft-session-workspace" aria-label="中心 Chat" data-draft-id={draftId}>
    <header className="chat-session-header">
      <div className="chat-session-title"><h2>新对话</h2></div>
    </header>
    <div className="chat-transcript" aria-label="对话历史">
      <div className="chat-reading"><div className="chat-empty"><h3>今天想完成什么？</h3></div></div>
    </div>
    <div className="chat-bottom"><div className="chat-reading">
      {error && <p role="alert" className="text-sm text-failed">{error}</p>}
      <ChatComposer
        text={text} onText={onText} onSend={() => void submit()} active={false} running={false}
        pending={pending} ready={!pending} capabilities={capabilities} commands={[]}
        onCommand={() => {}} onStop={() => {}} />
    </div></div>
  </section>
}

function SessionChat({client, store, capabilities, sessionId, drafts, planStore, inspector, fileBuffers, registerGuard, onNew, onSettings, onWorkflowEdit, onSessionUpserted, onFork, onSelectSession, onNavigate, onPauseTurn, planningGenerationTransport}: {
  client: ApiClient; store: SyncStore; capabilities: ChatCapabilities; sessionId: string; drafts: DraftStorage
  planStore: TaskPlanStore
  inspector: InspectorStore
  fileBuffers: FileBufferStore
  registerGuard?: (guard: DirtyGuard) => () => void
  onSessionUpserted:(session:SessionWire)=>void; onFork:(child:SessionWire)=>void; onSelectSession:(sid:string)=>void
  onNew: () => Promise<void>; onSettings: () => void; onWorkflowEdit: (run: WorkflowRunWire, view: RunViewWire) => void
  onNavigate: (location: AppLocation, returnTo?: AppLocation) => void
  onPauseTurn?: (workspaceId: string, sessionId: string, request: GuiPauseRequest) => Promise<unknown>
  planningGenerationTransport?: PlanningGenerationTransport
}) {
  const [imageSupported,setImageSupported] = useState(false)
  const [permission, setPermission] = useState<ChatSettings['permission']>(null)
  const [hostAllowed, setHostAllowed] = useState(false)
  useEffect(() => setHostAllowed(false), [permission])
  const workspace = capabilities.workspace_id
  // Inspector scope 跟随当前 workspace + session；切换时保存原 scope、加载新
  // scope，回到原会话恢复标签顺序/当前标签/目标。
  useEffect(() => { inspector.setScope({workspaceId: workspace, sessionId}) }, [inspector, workspace, sessionId])
  const inspectorToggleRef = useRef<HTMLButtonElement>(null)
  const attachments=useAttachments(client,workspace,sessionId,capabilities.attachment_limits)
  const attachmentCompatible=imageSupported||!attachments.entries.some(i=>i.row?.representation?.parts.some(p=>p.media_type.startsWith("image/")))
  const chat = useMemo(() => new ChatStore(client, workspace, sessionId), [client, workspace, sessionId])
  const onApprovalSettled = (result: import('../api/types').ApprovalResolveResultWire) => {
    setSettledApprovals(current => mergeApprovalViews(current, [result.approval]).slice(-20))
    void chat.refresh()
    void store.refreshPendingApprovals().catch(() => {})
  }
  const state = useSyncExternalStore(chat.subscribe, chat.getState)
  const globalState = useSyncExternalStore(store.subscribe.bind(store), store.getState.bind(store))
  const [text, setText] = useState(() => drafts.read(workspace,sessionId)); const textRef = useRef(text)
  const [draftError, setDraftError] = useState<string | null>(null); const [error, setError] = useState<string | null>(null)
  const [outbox] = useState(() => new OutboxStorage(sessionStorage))
  const [rejected, setRejected] = useState(false)
  const [pending, setPending] = useState(false); const [failed, setFailed] = useState<InteractionInput | null>(() => outbox.read(workspace,sessionId))
  const [settledApprovals, setSettledApprovals] = useState<ApprovalWire[]>([])
  const [session, setSession] = useState<SessionWire | null>(null)
  const [task, setTask] = useState<TaskRunWire | null>(null)
  const workflowEnabled = taskWorkflowAvailable(capabilities)
  const [workflowOn, setWorkflowOn] = useState(false)
  const [workflow, setWorkflow] = useState<WorkflowChoice | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [workflowLabel, setWorkflowLabel] = useState('')
  const attachmentsAvailable = !!capabilities.features.attachments?.available
  const fileInput = useRef<HTMLInputElement>(null)
  const [recoveryStatus, setRecoveryStatus] = useState<RecoveryStatus | null>(null)
  const recoveryProbe = useRef<string | null>(null)
  const [expandedContent, setExpandedContent] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  useEffect(() => {
    if (!notice || !['任务结果已接受。', '任务已放弃。', '任务已创建。', '正在取消…', '正在继续…', '正在停止…', '正在取消生成…', '正在暂停…'].includes(notice)) return
    const timer = window.setTimeout(() => setNotice(null), 5000)
    return () => window.clearTimeout(timer)
  }, [notice])
  // P10.1: workflow task cards come from the workflow-interactions page and
  // merge into the unified transcript anchored at their task's user message.
  const [workflowCards, setWorkflowCards] = useState<WorkflowCardView[]>([])
  const [cardsNext, setCardsNext] = useState<string | null>(null)
  const [cardsError, setCardsError] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    setCardsError(null)
    void client.chatWorkflows(workspace, sessionId).then(page => {
      if (!alive) return
      setWorkflowCards(page.items.slice(0, 500))
      setCardsNext(page.next_cursor)
    }, (e: Error) => { if (alive) setCardsError(e.message) })
    return () => { alive = false }
  }, [client, workspace, sessionId, globalState.cursor])
  const olderCards = async () => {
    if (!cardsNext) return
    try {
      const page = await client.chatWorkflows(workspace, sessionId, cardsNext)
      setWorkflowCards(old => [...old, ...page.items].slice(-500))
      setCardsNext(page.next_cursor)
    } catch (e) { setCardsError((e as Error).message) }
  }
  const alive = useRef(true); const scroll = useRef<HTMLDivElement>(null); const follow = useRef(true)
  const anchor = useRef<{id: string; offset: number} | null>(null); const [newMessages, setNewMessages] = useState(false)
  const taskGeneration = useRef(0)
  const [permissionFocus, setPermissionFocus] = useState(0)
  const [compactOpen, setCompactOpen] = useState(false)
  const [compactInstructions, setCompactInstructions] = useState('')
  const planState = useSyncExternalStore(planStore.subscribe, planStore.getState)
  const planPhase = planState.phase
  // 执行过程区（agent-transparency P2.5）：capability 未广告时退化为阶段兜底总览。
  const activityEnabled = capabilities.activity_stream?.schema === 1
  const activityStore = useMemo(() => activityEnabled
    ? new ActivityStore(client, workspace, sessionId) : null, [client, workspace, sessionId, activityEnabled])
  const activitySubscribe = activityStore?.subscribe ?? noopSubscribe
  const activityGetState = activityStore?.getState ?? (() => NO_ACTIVITY)
  const activityState = useSyncExternalStore(activitySubscribe, activityGetState)
  useEffect(() => {
    if (!activityStore) return
    void activityStore.start()
    return () => activityStore.stop()
  }, [activityStore])
  // Scenario state lives here so the plan panel and the run-record panel stay
  // in sync: the frozen run topology leads while no change candidate is open,
  // and the WorkflowPanel below then skips its duplicate canvas.
  const currentRunId = planState.view?.run?.workflow_run_id ?? null
  const [runPreferred, setRunPreferred] = useState<boolean | null>(null)
  useEffect(() => { setRunPreferred(null) }, [currentRunId])
  const planShowRun = planState.view !== null && planState.view.run !== null
    && (runPreferred ?? planRunTopologyDefault(planState.view))
  const planReceipts = planState.receipts
  useEffect(() => {
    void planStore.start()
    return () => planStore.stop()
  }, [planStore])
  // Completion returns the entry to ordinary chat; closing the panel or the
  // switch never cancels a running task.
  const previousPhase = useRef(planPhase)
  useEffect(() => {
    if (previousPhase.current !== 'completed' && planPhase === 'completed') {
      const run = planStore.getState().view?.run
      const needsRepair = run?.status === 'failed' || run?.status === 'cancelled' || run?.result_status === 'needs_revision'
      if (!needsRepair) {
        setWorkflowOn(false)
      }
    }
    previousPhase.current = planPhase
  }, [planPhase])
  const queue = state.snapshot?.queue; const active = queue?.active_agent_run_id ?? null
  // Chat settings load once here; the composer renders the permission entry
  // on the toolbar left and the model · effort entry on the toolbar right.
  const settings = useChatSettings({client, workspace, session: sessionId, active: !!active, snapshot: state.snapshot?.settings, onImages: setImageSupported, onPermission: setPermission})
  // Unified execution projection: one source for header, composer, controls
  // and input capability, covering chat, planning and Workflow runs alike.
  const execution = state.snapshot?.execution ?? null
  const planningGeneration = planningGenerationOf(planState.view)
  const planningNeedsRecovery = planState.view?.operations.some(operation =>
    operation.status === 'failed' && operation.error_code === 'needs_recovery',
  ) === true
  const busy = executionBusy(execution)
  const stopping = executionStopping(execution)
  const stopTarget = executionStopTarget(execution)
  const acceptsInput = executionAcceptsInput(execution)
  // P09.1: the same snapshot derives the primary pause/continue control and
  // the independent secondary cancel for every owner (chat/planning/workflow).
  const controls = executionControls(execution)
  // External control changes (another panel/tab, reconnect) carry no planning
  // event: reconcile the plan projection whenever the execution snapshot moves.
  useEffect(() => {
    void planStore.syncExternal()
  }, [planStore, execution?.revision, currentRunId])
  useEffect(() => {
    alive.current = true; void chat.start()
    const hide = () => chat.stop()
    const show = (event: PageTransitionEvent) => {if(event.persisted)void chat.start()}
    window.addEventListener('pagehide',hide);window.addEventListener('pageshow',show)
    return () => {alive.current=false;chat.stop();window.removeEventListener('pagehide',hide);window.removeEventListener('pageshow',show)}
  }, [chat])
  useEffect(() => {
    let cancelled = false
    void client.chatSession(workspace, sessionId).then(value => {if (!cancelled) {setSession(value.session); if (value.session.current_task_run_id) void selectTask(value.session.current_task_run_id, false)}}, () => {})
    return () => {cancelled = true}
  }, [client, workspace, sessionId, state.snapshot?.event_cursor, active, execution?.revision, state.items.at(-1)?.item_id])
  useEffect(() => {
    const projected = state.snapshot?.session
    if (projected) {setSession(projected); onSessionUpserted(projected)}
  }, [state.snapshot?.session?.metadata?.revision, state.snapshot?.session?.lifecycle])
  const refreshSession = async () => {const value=await client.chatSession(workspace,sessionId);if(alive.current){setSession(value.session);onSessionUpserted(value.session)}}
  useEffect(()=>{const guard=(event:Event)=>{if(draftError){event.preventDefault();setNotice('请先保存或清理本标签草稿，再切换工作区。')}};window.addEventListener('morrow:before-workspace-switch',guard);return()=>window.removeEventListener('morrow:before-workspace-switch',guard)},[draftError])
  const editMessage = async (recordId:string) => {
    if(draftError || failed){setError('请先处理当前草稿或未确认发送。');return}
    try {
      const original=await client.chatContent(workspace,sessionId,recordId)
      if(original.content.length>4096)throw new Error('原消息超过 4096 字符，请复制所需内容到新对话。')
      if(Object.keys(drafts.entries()).length>=20)throw new Error('请先清理一份草稿，再编辑到新对话。')
      const result=await client.sessionFork(workspace,sessionId,commandId('edit_fork'),{edit_record_id:recordId})
      drafts.write(workspace,result.session.session_id,original.content)
      const refs=state.items.find(item=>item.source.record_id===recordId)?.attachments ?? []
      const copied=[]
      for(const ref of refs){
        const key=commandId('edit_attachment')
        const row=await client.cloneAttachment(workspace,sessionId,result.session.session_id,ref.attachment_id,key)
        copied.push({key,name:row.name,row})
        new AttachmentDraftStorage(sessionStorage).write(workspace,result.session.session_id,copied)
      }
      onFork(result.session)
    } catch(e){setError((e as Error).message)}
  }
  const onText = (value: string) => {
    setText(value); textRef.current = value
    try {drafts.write(workspace,sessionId,value); setDraftError(null)} catch (e) {setDraftError((e as Error).message)}
  }
  useEffect(() => {
    const cleared = (event: Event) => {
      if ((event as CustomEvent<string>).detail === drafts.key(workspace,sessionId)) onText('')
      else if (draftError) onText(textRef.current)
    }
    window.addEventListener('morrow:draft-cleared',cleared)
    return () => window.removeEventListener('morrow:draft-cleared',cleared)
  }, [drafts,workspace,sessionId,draftError])
  useEffect(() => { const warn = (e: BeforeUnloadEvent) => {if (draftError || failed || pending) {e.preventDefault(); e.returnValue = ''}}; window.addEventListener('beforeunload',warn); return () => window.removeEventListener('beforeunload',warn) }, [draftError, failed, pending])
  useLayoutEffect(() => {
    const el = scroll.current; if (!el) return
    if (anchor.current) {const old = anchor.current; const node = [...el.querySelectorAll<HTMLElement>('[data-item-id]')].find(n=>n.dataset.itemId===old.id); if (node) el.scrollTop += node.getBoundingClientRect().top-el.getBoundingClientRect().top-old.offset; anchor.current=null}
    else if (follow.current) el.scrollTop = el.scrollHeight
    else setNewMessages(true)
  }, [state.items, state.snapshot?.draft?.text, queue?.items.length])
  const older = () => { const el=scroll.current; const node=el && [...el.querySelectorAll<HTMLElement>('[data-item-id]')].find(n=>n.getBoundingClientRect().bottom>el.getBoundingClientRect().top); if (el && node) anchor.current={id:node.dataset.itemId!,offset:node.getBoundingClientRect().top-el.getBoundingClientRect().top}; follow.current=false; void chat.older() }
  const selectTask = async (id: string, open = true) => {
    const generation = ++taskGeneration.current
    // Opening is user intent, independent of background refresh request ordering.
    if (open) { openInspector('artifacts', { taskRunId: id }); setTask(null) }
    try {
      const value = await client.getTask(id)
      if (alive.current && generation === taskGeneration.current && value.session_id === sessionId) setTask(value)
    } catch {
      if (alive.current && generation === taskGeneration.current) setError('任务详情读取失败')
    }
  }
  const currentTaskRunId = task?.task_run_id ?? session?.current_task_run_id ?? undefined
  const openInspector = (kind: InspectorKind, target: InspectorTarget = {}) => {
    inspector.openInspectorTab(kind, {
      ...(currentTaskRunId ? { taskRunId: currentTaskRunId } : {}),
      ...target,
    })
  }
  const submit = async (input: InteractionInput) => {
    setPending(true);setFailed(input);setError(null);setRejected(false);setNotice(null)
    try {
      outbox.write(workspace,sessionId,input)
      // Reconcile an uncertain submission first; retry preserves its immutable payload and ID.
      let receipt
      try {receipt = await client.chatReceipt(workspace,sessionId,input.client_message_id)} catch(e) {if (!(e instanceof ApiError) || e.status!==404) throw e}
      if (!receipt) receipt=await client.chatSend(workspace,sessionId,input)
      if (!alive.current) return
      outbox.write(workspace,sessionId,null); attachments.consume(input.attachments); setFailed(null); setHostAllowed(false); if (textRef.current===input.text) onText('')
      follow.current=true; await chat.refresh()
    } catch(e) {if(alive.current){setRejected(e instanceof ApiError && [400,403,413,415].includes(e.status));setError(e instanceof ApiError ? e.message : '接纳状态未知；可按原编号核对并重试。')}}
    finally {if(alive.current)setPending(false)}
  }
  useEffect(() => {
    const input = outbox.read(workspace,sessionId)
    if (!input) return
    let cancelled = false
    void client.chatReceipt(workspace,sessionId,input.client_message_id).then(() => {
      if (cancelled) return
      outbox.write(workspace,sessionId,null);attachments.consume(input.attachments);setFailed(null)
      if (textRef.current===input.text) onText('')
      void chat.refresh()
    }, () => {if(!cancelled)setError('上次发送尚待核对。请使用原编号重试，输入未被重新发送。')})
    return () => {cancelled=true}
  }, [client,workspace,sessionId,chat,outbox])
  /** One planning submission path for both the switch send and /workflow <目标>. */
  const planningSubmit = async (objective: string) => {
    if (!objective.trim()) { setError('请先描述任务目标，再生成工作流计划。'); return }
    // Shared unsaved-edit guard: an explicit chat start must respect the same
    // state as the panel button ("看到什么，就批准执行什么"). The server still
    // re-checks version and digest on every start request.
    if (planStore.hasUnsavedEdits && looksLikeStartCommand(objective)) {
      setError('有未保存的节点编辑；请先在右侧计划面板保存后再开始执行。')
      return
    }
    if (planStore.generating) { setError('已有生成正在进行；可在右侧面板取消后重试。'); return }
    const phase = planStore.getState().phase
    const terminalRun = planStore.getState().view?.run ?? null
    const needsRepair = terminalRun !== null && (
      terminalRun.status === 'failed' || terminalRun.status === 'cancelled' ||
      terminalRun.result_status === 'needs_revision'
    )
    if (phase === 'completed' && !needsRepair) {
      // After a successful completion the entry stays ordinary chat (D14): a
      // follow-up is a normal single-Agent send, never a hidden second workflow.
      setWorkflowOn(false)
      await submit({client_message_id:commandId('chat'),text:objective,intent:'send'})
      if (textRef.current === objective) onText('')
      return
    }
    openInspector('workflow')
    if (phase === 'running' || phase === 'pausing' || phase === 'paused') {
      const outcome = await planStore.control(objective)
      if (outcome.kind === 'steer') {
        const target = outcome.targetAgentRunId ?? active
        await submit({client_message_id:commandId('chat'),text:objective,intent:'steer',...(target?{target_agent_run_id:target}:{})})
        if (planStore.getState().error === null && textRef.current === objective) onText('')
      } else if (outcome.kind === 'handled' && planStore.getState().error === null && textRef.current === objective) {
        onText('')
      }
      return
    }
    if (phase === 'idle' || phase === 'error') {
      // A failed generation left no draft: the re-sent task description is the
      // documented regeneration path (the server advertises `generate`), never
      // a control instruction against a dead operation.
      await planStore.submit(objective, attachments.references)
      if (planStore.getState().error === null) {
        attachments.consume(attachments.references)
        if (textRef.current === objective) onText('')
        follow.current = true
      }
      return
    }
    const outcome = await planStore.control(objective)
    if (outcome.kind === 'handled' && planStore.getState().error === null && textRef.current === objective) onText('')
  }
  const send = (intent: InteractionInput['intent']) => {
    // P09.2 (D07): while an execution is paused, the paused task owns the
    // continuation — pure continue on a paused chat queue takes the durable
    // queue continuation, and text on a paused planning/workflow execution is
    // classified server-side. workflowOn never decides the control target.
    if (intent === 'send') {
      const pausedRoute = pausedSendRoute(execution, text, queue?.paused, isPureContinueCommand)
      if (pausedRoute === 'queue_continue') {
        control('continue_queue')
        if (textRef.current === text) onText('')
        return
      }
      if (pausedRoute === 'control') { void pausedControlSubmit(text); return }
      // BUG-GUI-001: an explicit pause word on a live planning/workflow run
      // takes the durable control channel (same as the pause button) instead
      // of being queued as an ordinary message.
      if (runningControlRoute(execution, text, isExplicitPauseCommand) === 'control') {
        void runningPauseSubmit(text)
        return
      }
    }
    // A paused chat queue owns its own continuation: "继续" must never be
    // rerouted into workflow resume, and it cannot revive a cancelled AgentRun.
    if (intent === 'send' && queue?.paused && isPureContinueCommand(text)) {
      control('continue_queue')
      if (textRef.current === text) onText('')
      return
    }
    if (intent !== 'send') { if (failed) {setError('先核对上一条待发送消息，避免重复提交。'); return} void submit({client_message_id:commandId('chat'),text,intent,...(attachments.references.length?{attachments:attachments.references}:{}),...(hostAllowed&&permission==='full-access-manual'?{allow_unconfined_host:true}:{}),...(active?{target_agent_run_id:active}:{})}); return }
    if (workflowEnabled && workflowOn) { void planningSubmit(text); return }
    if (workflow) {
      if (attachments.entries.length) { setError('指定 Workflow 接收文本任务；请先移除附件或使用普通对话发送附件。'); return }
      if (failed) { setError('先核对上一条待发送消息，避免重复提交。'); return }
      void submit({client_message_id:commandId('workflow_chat'),text,intent:'explicit_workflow',workflow}); return
    }
    if (failed) { setError('先核对上一条待发送消息，避免重复提交。'); return }
    void submit({client_message_id:commandId('chat'),text,intent,...(attachments.references.length?{attachments:attachments.references}:{}),...(hostAllowed&&permission==='full-access-manual'?{allow_unconfined_host:true}:{})})
  }
  const action = async (fn: () => Promise<unknown>) => {setError(null);try {await fn();if(alive.current)await chat.refresh()} catch(e){if(alive.current)setError(e instanceof ApiError ? e.message : '操作失败，请刷新后重试')}}
  const taskAction = async (operation: Extract<ChatOperation, { kind: 'accept-task' | 'cancel-task' | 'abandon-task' | 'resume-task' }>) => {
    if (!task) {
      setNotice('当前对话没有可操作的任务。')
      return
    }
    if (operation.kind === 'accept-task' && task.status !== 'ready_for_acceptance') {
      setNotice('当前任务还没有待验收结果。')
      return
    }
    await action(async () => {
      if (operation.kind === 'accept-task') {
        await client.acceptTask(task.task_run_id, task.row_version, commandId('accept'))
        setNotice('任务结果已接受。')
      } else if (operation.kind === 'cancel-task') {
        await client.cancelTask(task.task_run_id, task.row_version, commandId('cancel'))
        setNotice('正在取消…')
      } else if (operation.kind === 'abandon-task') {
        await client.operationCommand(`task-actions/${encodeURIComponent(task.task_run_id)}`, {
          action: 'abandon', expected_row_version: task.row_version, command_id: commandId('abandon'),
        })
        setNotice('任务已放弃。')
      } else {
        await client.resumeTask(task.task_run_id, task.row_version, commandId('resume'))
        setNotice('正在继续…')
      }
      await selectTask(task.task_run_id, false)
    })
  }
  const handleChatOperation = async (operation: ChatOperation) => {
    if (operation.kind === 'artifacts' || operation.kind === 'workflow' || operation.kind === 'context') {
      openInspector(operation.inspector)
      return
    }
    if (operation.kind === 'compact') {
      setCompactOpen(true)
      return
    }
    if (operation.kind === 'new-task') {
      await action(async () => {
        await client.operationCommand('tasks', { session_id: sessionId, command_id: commandId('task') })
        await refreshSession()
        setNotice('任务已创建。')
      })
      return
    }
    await taskAction(operation)
  }
  const submitCompact = () => void action(async () => {
    const result = await client.chatCommand(workspace, sessionId, 'compact', commandId('compact'), compactInstructions.trim())
    setNotice(result.message)
    setCompactInstructions('')
    setCompactOpen(false)
  })
  const loadRecoveryStatus = async (force = false) => {
    const health = session?.health ?? state.snapshot?.session?.health ?? ''
    const key = JSON.stringify({
      connection: state.connection,
      health,
      execution: execution?.revision ?? 0,
      executionState: execution?.state ?? 'idle',
      queue: queue?.revision ?? 0,
      planningGeneration: planningGeneration?.planning_operation_id ?? null,
      planningGenerationState: planningGeneration?.status ?? null,
      planningNeedsRecovery,
    })
    if (!force && recoveryProbe.current === key) return
    recoveryProbe.current = key
    try {
      const value = await client.chatRecoveryStatus(workspace, sessionId)
      if (alive.current) setRecoveryStatus(value)
    } catch (e) {
      if (alive.current && force) {
        setError(e instanceof ApiError ? e.message : '恢复状态读取失败，请刷新后重试')
      }
    }
  }
  // A status read is automatic only after a durable recovery signal. It is
  // keyed by the current snapshot revision so reconnects and repeated frames
  // cannot create duplicate discovery or recovery commands.
  useEffect(() => {
    const health = session?.health ?? state.snapshot?.session?.health
    const recoveryFact = state.connection === 'live' && (
      execution?.state === 'needs_recovery' ||
      queue?.paused === true || health === 'needs_recovery' || health === 'quarantined' ||
      planningGeneration?.status === 'paused' || planningNeedsRecovery
    )
    if (recoveryFact) void loadRecoveryStatus()
  }, [state.connection, state.snapshot?.session?.health, execution?.state, execution?.revision, queue?.paused, queue?.revision, session?.health, planningGeneration?.planning_operation_id, planningGeneration?.status, planningNeedsRecovery])
  const control = (kind: 'stop'|'continue_queue') => {if(!queue)return;void action(()=>client.chatControl(workspace,sessionId,{command_id:commandId(kind),action:kind,expected_revision:queue.revision,...(kind==='stop'?{target_agent_run_id:active}:{})}));if(kind==='stop')setNotice('正在停止…')}
  /** Stop routed by execution ownership; the button shows "stopping" until
   * the server settles the state, never claiming an immediate stop. */
  const stopExecution = () => {
    if (!stopTarget) return
    if (stopTarget.kind === 'chat') { control('stop'); return }
    if (stopTarget.kind === 'workflow') {
      void action(() => client.cancelRun(stopTarget.workflowRunId!, commandId('stop')))
      setNotice('正在停止…')
      return
    }
    void action(() => client.taskPlanCancel(workspace, sessionId, stopTarget.operationId!))
    setNotice('正在取消生成…')
  }
  /** P09.1: primary pause routed by execution ownership. Pause is the primary
   * action and never folds into cancel (D01); run- and generation-scoped
   * planning presses stay on their own store paths (B coordination), and the
   * chat turn pause carries the frozen GuiPauseRequest through the injected
   * transport. */
  const pauseExecution = () => {
    const target = executionControlTarget(execution, 'pause')
    if (!target) return
    if (target.kind === 'workflow') {
      void action(() => client.pauseRun(target.workflowRunId!, commandId('pause')))
      setNotice('正在暂停…')
      return
    }
    if (target.kind === 'planning') { void planStore.pause(); return }
    if (target.kind === 'planning_generation') { void planStore.pauseGeneration(); return }
    if (!onPauseTurn) return
    const request = guiPauseRequestBody({commandId: commandId('pause'), sessionId})
    void action(() => onPauseTurn!(workspace, sessionId, request))
    setNotice('正在暂停…')
  }
  /** P09.1: continue routed by execution ownership. A paused chat queue takes
   * its own durable continuation and never reroutes into workflow resume. */
  const resumeExecution = () => {
    const target = executionControlTarget(execution, 'resume')
    if (!target) return
    if (target.kind === 'workflow') {
      void action(() => client.resumeRun(target.workflowRunId!, commandId('resume')))
      setNotice('正在继续…')
      return
    }
    if (target.kind === 'planning') { void planStore.resumeRun(); return }
    if (target.kind === 'planning_generation') { void planStore.resumeGeneration(); return }
    control('continue_queue')
  }
  /** P09.2 (D07): text against a paused planning/workflow execution goes
   * through server-side control classification — pure continue and executable
   * corrections advance the task, pure questions are only answered. The
   * durable receipt surfaces in the transcript. */
  const pausedControlSubmit = async (value: string) => {
    const outcome = await planStore.control(value)
    if (outcome.kind === 'steer') {
      const target = outcome.targetAgentRunId ?? active
      await submit({client_message_id:commandId('chat'),text:value,intent:'steer',...(target?{target_agent_run_id:target}:{})})
      if (planStore.getState().error === null && textRef.current === value) onText('')
      return
    }
    if (outcome.kind === 'handled') {
      if (planStore.getState().error === null && textRef.current === value) onText('')
      return
    }
    // No planning binding: a paused run without one has no text-control
    // channel; keep the draft and point at the composer continue control.
    setError('当前任务已暂停；点击“继续”恢复后再发送，或保持文本待恢复后处理。')
  }
  /** BUG-GUI-001: an explicit pause word typed while a run is live goes
   * through the same state-aware control channel as the pause button. The
   * control command never carries attachments: their draft is always kept. */
  const runningPauseSubmit = async (value: string) => {
    const outcome = await planStore.control(value)
    if (outcome.kind === 'handled') {
      if (planStore.getState().error === null && textRef.current === value) {
        if (attachments.entries.length) {
          setNotice('已请求暂停；附件未随指令提交，草稿已保留。')
        }
        onText('')
      }
      return
    }
    setError('当前没有可暂停的执行；请使用运行卡或等待任务状态更新后重试。')
  }
  const inputLocked = pending || !!failed
  // ＋ menu entries owned here: the two workflow modes. Everything else in
  // the menu (uploads, @ references, commands) is composer-local.
  const plusItems = <>
    {workflowEnabled && <button role="menuitem" className="composer-menu-item" disabled={inputLocked}
      onClick={() => {const next = !workflowOn; setWorkflowOn(next); if (!next) setWorkflow(null); setPickerOpen(false)}}>
      <span>{workflowOn ? '✓ 工作流编排' : '工作流编排'}</span>
      <span className="menu-note">{workflowOn ? '发送后将生成当前任务计划' : '发送文本生成任务计划，右侧面板审阅执行'}</span>
    </button>}
    <button role="menuitem" className="composer-menu-item" disabled={inputLocked || workflowOn}
      onClick={() => {setWorkflowOn(false); setPickerOpen(true)}}>
      <span>使用已发布工作流</span>
      <span className="menu-note">{workflow ? '已指定发布版本，点击更换' : '按已发布的固定版本执行'}</span>
    </button>
  </>
  // On-demand chips: the chosen workflow mode stays visible inside the
  // composer; closing one restores the ordinary conversation.
  const tags = <>
    {workflowEnabled && workflowOn && <span className="composer-chip-wrap">
      <button className="composer-chip" title="已开启工作流编排；点击打开右侧计划面板" onClick={() => openInspector('workflow')}>工作流编排</button>
      <button className="composer-chip-close" aria-label="关闭工作流编排，恢复普通对话" disabled={inputLocked} onClick={() => {setWorkflowOn(false); setWorkflow(null)}}>×</button>
    </span>}
    {workflow && <span className="composer-chip-wrap">
      <button className="composer-chip" title="点击更换已发布工作流" onClick={() => setPickerOpen(true)}>{workflowLabel || '已发布工作流'}</button>
      <button className="composer-chip-close" aria-label="取消指定工作流，恢复普通对话" disabled={inputLocked} onClick={() => {setWorkflow(null); setWorkflowLabel('')}}>×</button>
    </span>}
    {pickerOpen && <PublishedWorkflowPicker client={client} disabled={inputLocked} label={workflowLabel}
      onChoice={(choice, label) => {setWorkflow(choice); setWorkflowLabel(choice ? label : ''); if (choice) setPickerOpen(false)}}
      onClose={() => setPickerOpen(false)}/>}
  </>
  const commands = [
    ...assetCommandEntries,
    ...(workflowEnabled ? [
      {name:'/plan',label:'生成当前任务计划（/workflow 别名）'},
      {name:'/workflow',label:'生成当前任务计划；可带目标文本'},
    ] : []),
    {name:'/new',label:'新建对话'}, {name:'/status',label:'会话与队列状态'},
    {name:'/recovery',label:'在对话底部检查恢复状态'},
    {name:'/recovery show',label:'在对话底部检查恢复状态'},
    ...chatOperationEntries,
    {name:'/grant',label:'打开输入栏权限与授权'}, {name:'/exit',label:'离开对话提示'},
    {name:'/accept',label:'接受当前任务结果',disabled:busy || task?.status!=='ready_for_acceptance'},

  ]
  const command = (name: string, fullText?: string) => {
    const given = (fullText ?? name).trim()
    const operation = chatOperation(name)
    if (operation) {
      void handleChatOperation(operation)
      if (text.startsWith('/')) onText('')
      return
    }
    if (name === '/recovery' || name === '/recovery show') {
      void loadRecoveryStatus(true)
      if (text.startsWith('/')) onText('')
      return
    }
    if(name==='/grant'){setPermissionFocus(value => value + 1);onText('');return}
    if(name==='/exit'){setNotice('可关闭当前浏览器标签离开；已有运行继续由 Core 管理。草稿保留到标签关闭，未发送内容可先复制。');onText('');return}
    if (applyAssetCommand(given, {
      navigate: onNavigate,
      openLearningReview: () => openInspector('learning'),
      notify: setNotice,
    })) { onText(''); return }
    if (name === '/workflow' || name === '/plan') {
      // The command only counts when it is the first token of the message;
      // quoted text, code blocks and paths never reach this branch.
      const target = workflowTarget(given)
      setWorkflowOn(true); setWorkflow(null)
      if (target) { onText(target); void planningSubmit(target) }
      else if (text.startsWith('/')) onText('')
      return
    }
    if (name==='/new') {void onNew();return}
    if (name==='/status') {openInspector('context');if(text.startsWith('/'))onText('');return}
    void action(async()=>{
      if(name==='/accept'&&task){await client.acceptTask(task.task_run_id,task.row_version,commandId('accept'));await selectTask(task.task_run_id,false);setNotice('任务结果已接受。')}
      else {const result=await client.chatCommand(workspace,sessionId,name.slice(1),commandId('command'));setNotice(result.message)}
      if(alive.current&&textRef.current.startsWith('/'))onText('')
    })
  }
  const inspectRecovery = () => void loadRecoveryStatus(true)
  const recoveryChanged = async () => {
    await chat.refresh()
    await loadRecoveryStatus(true)
  }
  const reviewRecovery = () => {
    if (recoveryStatus?.owner === 'workflow' && recoveryStatus.opaque_target) {
      openInspector('workflow', {workflowRunId: recoveryStatus.opaque_target})
    } else {
      setNotice(null)
    }
  }
  // Ownership-resolved scoping: an isolated workflow-node session shows the
  // run that owns it (and its approvals) instead of matching by root task,
  // which a leaf task can never satisfy.
  const ownership = planState.view?.ownership ?? null
  const ownedRunId = ownership?.role === 'node' ? ownership.workflow_run_id : null
  const scopedApprovals = [...globalState.pendingApprovals.values()].filter(a=>a.session_id===sessionId || (ownedRunId!==null && a.workflow_run_id===ownedRunId) || (!!a.workflow_run_id&&ownedRunId===null&&globalState.workflowRuns.get(a.workflow_run_id)?.run.root_task_run_id===task?.task_run_id))
  const settledForSession = settledApprovals.filter(a=>a.session_id===sessionId || (ownedRunId!==null && a.workflow_run_id===ownedRunId) || (!!a.workflow_run_id&&ownedRunId===null&&globalState.workflowRuns.get(a.workflow_run_id)?.run.root_task_run_id===task?.task_run_id))
  const approvals = mergeApprovalViews(uniqueApprovals(scopedApprovals), uniqueApprovals(settledForSession))
  const runs = ownedRunId!==null
    ? [...globalState.workflowRuns.values()].filter(r=>r.run.workflow_run_id===ownedRunId)
    : [...globalState.workflowRuns.values()].filter(r=>r.run.root_task_run_id===task?.task_run_id)
  // 执行过程区归属（P10.1）：整个轨迹来自一个统一混合渲染列表——消息、执行
  // 区域、任务卡、控制回执、草稿与审批按确定性规则合并排序；计划与结果锚定
  // 在其任务的用户消息之后，无归属运行按到达顺序排在尾部，不再有独立追加块。
  const stageFrames = liveStageFrames(state.snapshot?.stages ?? [], globalState.workflowRuns)
  const activityRuns = groupByRun(activityState.items)
  // 已启用活动流时，工作流审批由过程区总览直达；会话级审批保留消息卡片。
  const activityApprovalIds = new Set(activityState.items.flatMap(item => item.payload.kind === 'approval' ? [item.payload.approval_id] : []))
  const approvalCards = activityEnabled ? approvals.filter(a => a.workflow_run_id === null || !activityApprovalIds.has(a.approval_id)) : approvals
  // 阶段兜底/等待区域没有自己的活动 socket：同步信号退回 Chat 连接本身。
  const regionStatus = activityEnabled ? activityState.status : state.connection === 'live' ? 'live' as const : 'reconnecting' as const
  const ownerStatusOf = (run: ActivityRun): string | null => run.key.startsWith('wf:')
    ? globalState.workflowRuns.get(run.key.slice(3))?.run.status ?? null
    : null
  // P10.3: pausing / paused / needs-recovery execution freezes region clocks —
  // elapsed excludes the frozen span instead of running a ghost timer.
  const timingFrozen = executionTimingFrozen(execution)
  const renderRegion = (run: ActivityRun, stages: StageFrame[] = [], key = run.key) => <ActivityTimeline
    key={key} client={client} run={run} content={activityState.content} status={regionStatus}
    connection={state.connection}
    stages={run.key.startsWith('wf:') ? stageFrames.filter(s => s.workflow_run_id === run.key.slice(3)) : stages}
    busy={busy}
    frozen={timingFrozen}
    ownerStatus={ownerStatusOf(run)}
    approvals={run.key.startsWith('wf:') ? approvals.filter(a => a.workflow_run_id === run.key.slice(3)) : []}
    workspace={workspace}
    onApprovalSettled={onApprovalSettled}
    onSteer={async (identity, text) => (await client.chatNodeSteer(workspace, sessionId, {
      command_id: commandId('node-steer'), workflow_run_id: identity.workflow_run_id!,
      node_run_id: identity.node_run_id!, text,
    })).disposition}/>
  const transcript = buildTranscript({
    items: state.items,
    runs: activityRuns,
    stageFrames,
    busy,
    receipts: planReceipts,
    cards: workflowCards,
    draft: state.snapshot?.draft ?? null,
    approvals: approvalCards,
    acceptReady: !busy && task?.status === 'ready_for_acceptance',
    rootTaskOf: wfId => globalState.workflowRuns.get(wfId)?.run.root_task_run_id ?? null,
  })
  // 结果条目与其 Workflow 卡片按明确来源 ID 去重：同一运行已有可读结果
  // 条目时，卡片不再重复终态正文，也不隐藏其他节点的独立回答。
  const resultRuns = new Set(
    state.items.flatMap(item => item.kind === 'result' && item.result?.workflow_run_id ? [item.result.workflow_run_id] : []),
  )
  /** 结果列表、助手 Markdown 与产物面板共用的文件打开入口。 */
  const openFile = (target: FileTarget) => { inspector.openFile(target) }
  const renderEntry = (entry: TranscriptEntry) => {
    switch (entry.kind) {
      case 'item': {
        const item = entry.item
        const editState = item.kind==='user_message'&&item.source.record_id ? editForkState(item) : null
        return <div className={`chat-entry ${item.kind === 'user_message' ? 'chat-entry-user' : ''}`}>
          <ChatMessage item={item} client={client} onOpenFile={openFile} expanded={expandedContent===item.item_id} onExpand={()=>setExpandedContent(item.item_id)} onTask={id=>void selectTask(id)}/>
          {editState&&<button className="message-edit-button" disabled={busy||!!queue?.items.length||!editState.enabled} title={editState.enabled?'从此消息之前的闭合对话分叉；首条消息创建空对话。在新对话中编辑并发送。':(editState.hint??'')} onClick={()=>void editMessage(item.source.record_id!)}>编辑到新对话</button>}
          {editState&&!editState.enabled&&<p className="message-edit-hint text-xs text-secondary">{editState.hint}</p>}
        </div>
      }
      case 'region':
        return renderRegion(entry.run, entry.stages, entry.id)
      case 'card': {
        const card = entry.card
        return <article className="chat-message rounded-lg border border-subtle p-3" data-kind="workflow-card">
          <p className="text-xs text-secondary">Workflow 任务 · {card.workflow.workflow_definition_id} · {card.run_status ?? card.status}</p>
          <p className="whitespace-pre-wrap">{card.text}</p>
          {card.outcome && !(card.workflow_run_id !== null && resultRuns.has(card.workflow_run_id)) && <p>{outcomeSummary(card.outcome.summary, card.outcome.task_status)}</p>}
          {card.task_run_id && <><button type="button" className="editor-button" onClick={()=>{void selectTask(card.task_run_id!,false);openInspector('workflow',{taskRunId:card.task_run_id!})}}>查看计划、进度与结果</button>
            <button type="button" className="editor-button" onClick={()=>onText(`请基于任务 ${card.task_run_id} 的结果继续：`)}>追问此结果</button></>}
        </article>
      }
      case 'receipt':
        return <div className="chat-entry chat-entry-user" data-kind="control-input">
          <p>{entry.receipt.text}</p>
          <p className="text-xs text-secondary">控制指令 · {controlReceiptLabel(entry.receipt)}</p>
        </div>
      case 'draft':
        return <article className="chat-message" data-kind="reply-draft"><p className="mb-2 text-xs text-secondary">Morrow · 正在回复</p><Markdown text={entry.text} onOpenFile={openFile}/></article>
      case 'approval': {
        const a = entry.approval
        return <InlineApprovalCard approval={a} client={client} workspace={workspace} onSettled={onApprovalSettled}/>
      }
      case 'acceptance':
        return task && <div className="chat-message"><p>任务结果待验收</p><button className="editor-button" onClick={()=>command('/accept')}>接受结果</button> <button className="editor-button" onClick={()=>void action(async()=>{await client.resumeTask(task.task_run_id,task.row_version,commandId('resume'));await selectTask(task.task_run_id,false);setNotice('任务已重新打开，请在输入框说明修正要求。')})}>退回修正</button></div>
    }
  }
  // P09.1: composer primary control from the shared derivation. Missing
  // transport (chat turn pause) or owner identity renders disabled with the
  // honest reason — the control never pretends to have acted.
  const composerPrimaryControl = (() => {
    if (!controls.primary) return undefined
    const {kind, label, settling} = controls.primary
    const target = executionControlTarget(execution, kind)
    const disabledReason = target === null
      ? '暂时无法操作，请刷新后重试。'
      : kind === 'pause' && target.kind === 'chat' && !onPauseTurn
        ? '当前无法暂停。'
        : target.kind === 'planning_generation' && !planningGenerationTransport
          ? '当前无法暂停或继续生成。'
          : null
    return {kind, label, settling, disabledReason, onTrigger: kind === 'pause' ? pauseExecution : resumeExecution}
  })()
  return <section className="chat-session-workspace" aria-label="中心 Chat" data-session-id={sessionId}>
    <ChatColumns store={inspector} returnFocusRef={inspectorToggleRef} bodyProps={{
      client,
      workspaceId: workspace,
      sessionId,
      currentTaskRunId,
      syncStore: store,
      planStore,
      connection: state.connection,
      activityStore,
      runs,
      showRun: planShowRun,
      onPreferRun: setRunPreferred,
      onReturnToRoot: ownership?.root_session_id != null ? () => onSelectSession(ownership.root_session_id!) : null,
      onWorkflowEdit,
      focusNodeId: ownedRunId !== null ? ownership?.node_id ?? null : null,
      onNavigate: location => onNavigate(location, { kind: 'chat' }),
      onOpenFile: openFile,
      fileBuffers,
      registerGuard,
    }} center={(chatLayout) => <>
      <header className="chat-session-header">
        <div className="chat-session-title"><h2>{session?.metadata?.title || '新对话'}</h2>{(ownership?.role === 'node' || executionLabel(execution) || queue?.paused) && <p>{ownership?.role === 'node' ? '工作流执行节点 · 执行详情' : executionLabel(execution) ?? '队列已暂停'}</p>}</div>
        <div className="chat-header-actions">
          {session && <SessionActions client={client} workspace={workspace} session={session}
            active={busy || !!queue?.items.length} onChanged={refreshSession} onFork={onFork} />}
          <InspectorHeaderButtons store={inspector} toggleRef={inspectorToggleRef} layout={chatLayout}/>
        </div>
      </header>
      {session && (session.lifecycle!=='active'||session.health!=='ok') && <p role="status" className="p-3">此对话为 {session.lifecycle} / {session.health}，当前不可发送。可在“管理对话”中恢复归档，或检查恢复报告。</p>}

      {state.connection!=='live' && <div role="status" className="p-2 text-sm">{state.error ?? '连接中…'} <button className="editor-button" onClick={()=>chat.retry()}>重新连接</button></div>}
      <div className="chat-transcript" ref={scroll} aria-label="对话历史" onScroll={()=>{const el=scroll.current!;follow.current=el.scrollHeight-el.scrollTop-el.clientHeight<70;if(follow.current)setNewMessages(false)}}>
        <div className="chat-reading">
          {state.cursor && <button className="editor-button" disabled={state.loadingHistory} onClick={older}>{state.loadingHistory?'加载中…':'加载更早消息'}</button>}
          {state.trimmed && <p className="text-xs text-secondary"><button className="editor-button" onClick={()=>{follow.current=true;chat.latest()}}>返回最新历史</button></p>}
          {transcript.empty && <div className="chat-empty"><h3>今天想完成什么？</h3></div>}
          {transcript.entries.map(entry=><Fragment key={entry.id}>{renderEntry(entry)}</Fragment>)}
          {cardsNext && <button className="editor-button" onClick={()=>void olderCards()}>更早的 Workflow 任务</button>}
          {cardsError && <p role="alert" className="text-sm text-failed">{cardsError}</p>}
        </div>
      </div>
      {newMessages && <button className="chat-new-message editor-button" onClick={()=>{follow.current=true;setNewMessages(false);chat.latest()}}>有新消息 · 返回底部</button>}
      <div className="chat-bottom"><div className="chat-reading">
        <div className="chat-input-options">
        {queue?.items.map(item=><div className="chat-queued" key={item.client_message_id}><div><strong>{queue.paused?'暂停 · ':''}{item.status} · 排队 {item.queue_position ?? '—'} · {item.intent==='steer'?'运行中纠正':item.intent==='follow_up'?'完成后执行':'新输入'}</strong><p>{item.text}</p>{item.reason==='settings_unavailable'&&<p className="text-failed">绑定的模型或权限已不可用。撤回此输入，修复设置后重新发送。</p>}</div><button className="editor-button" onClick={()=>void action(()=>client.chatWithdraw(workspace,sessionId,item.client_message_id,item.revision,commandId('withdraw')))}>撤回</button></div>)}
        {queue?.paused && <div className="flex flex-wrap gap-2 p-2"><button className="editor-button" onClick={()=>control('continue_queue')}>继续队列</button><button className="editor-button" onClick={inspectRecovery}>查看恢复状态</button></div>}
        <RecoveryBanner client={client} workspace={workspace} session={sessionId} status={recoveryStatus} onChanged={recoveryChanged} onNewSession={onNew} onReview={reviewRecovery}/>
        {notice && <p role="status" className="text-xs">{notice} <button onClick={()=>setNotice(null)}>关闭提示</button></p>}
        {(error || draftError) && <p role="alert" className="text-sm text-failed">{error || draftError}</p>}
        {planState.error && <p role="alert" className="text-sm text-failed">计划控制：{planState.error}</p>}

        {failed && <div className="chat-queued"><span>{pending?'待发送 / 核对接纳':'接纳未知 / 待重试'}：{failed.text}</span><button className="editor-button" disabled={pending} onClick={()=>void submit(failed)}>核对并重试原消息</button>{rejected && <button className="editor-button" onClick={()=>{outbox.write(workspace,sessionId,null);setFailed(null);setRejected(false);setError(null)}}>编辑后重新发送</button>}</div>}



        {!settings.ready && settings.value && <button className="settings-warning" onClick={onSettings}>模型配置不可用；打开 Provider 设置</button>}

        </div>
        {workflow&&attachments.entries.length>0&&<p role="status" className="px-3 text-xs">此工作流不支持附件。移除附件或切换为普通对话。</p>}
        {!acceptsInput && <p role="status" className="px-3 text-xs">工作流执行详情 · 此对话不接受普通输入。{ownership?.root_session_id && <button className="editor-button" onClick={()=>onSelectSession(ownership.root_session_id!)}>返回主对话</button>}</p>}
        {compactOpen && <section className="chat-inline-action" aria-label="压缩对话上下文">
          <h3>压缩对话上下文</h3>
          <p>仅在会话空闲时执行。原始历史仍保留；可填写希望保留的重点。</p>
          <textarea aria-label="压缩重点" className="workspace-input" maxLength={512} rows={3}
            value={compactInstructions} onChange={event => setCompactInstructions(event.target.value)} />
          <div className="flex flex-wrap gap-2">
            <button type="button" className="editor-button" disabled={busy || pending} onClick={submitCompact}>确认压缩</button>
            <button type="button" className="editor-button" disabled={busy || pending} onClick={() => { setCompactOpen(false); setCompactInstructions('') }}>取消</button>
          </div>
        </section>}
        <input ref={fileInput} type="file" multiple hidden aria-hidden="true" onChange={e => {if (e.target.files) attachments.add(e.target.files); e.target.value = ''}}/>
        <ChatComposer
          accessories={attachmentsAvailable && <AttachmentComposer client={client} workspace={workspace} session={sessionId} text={text} onText={onText} attachments={attachments} imageSupported={imageSupported} disabled={inputLocked}/>}
          hasAttachments={attachments.entries.length>0} onFiles={files=>attachments.add(files)}
          canAttach={attachmentsAvailable} onPickFiles={() => fileInput.current?.click()}
          placeholder={planPhase === 'ready' ? '修改计划…' : '描述任务，或输入 / 查看命令'}
          plusItems={plusItems} tags={tags}
          permissionControl={<ChatPermissionControl settings={settings} client={client} workspace={workspace} session={sessionId}
            focusSignal={permissionFocus} hostAllowed={hostAllowed} onHostAllowedChange={setHostAllowed}/>}
          modelControl={<ChatModelControl settings={settings} onOpenSettings={onSettings}/>}
          text={text} onText={onText} onSend={send} active={!!active} running={busy} stopping={stopping} pending={inputLocked}
          primaryControl={composerPrimaryControl}
          ready={acceptsInput && attachments.ready && attachmentCompatible && settings.ready && state.connection==='live' && session?.lifecycle==='active' && session.health==='ok'}
          capabilities={capabilities} commands={commands} onCommand={command} onStop={stopExecution}/>
      </div></div>
    </>} />
  </section>
}
