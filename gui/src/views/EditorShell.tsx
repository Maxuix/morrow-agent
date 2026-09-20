import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { ApiError, type ApiClient } from '../api/client'
import type {
  AgentDefinitionViewWire,
  ArtifactContractCatalogWire,
  ProviderCatalogWire,
  SkillCatalogWire,
  ToolCatalogWire,
  WorkflowDefinitionSourceWire,
  WorkflowDefinitionViewWire,
  WorkflowDraftDiagnosticWire,
  WorkflowDraftViewWire,
} from '../api/types'
import type { DirtyGuard } from '../state/navigation'
import { WorkflowDraftController } from '../state/workflowDraft'
import { DefinitionLifecycle } from './DefinitionLifecycle'
import { GraphPlanner, PlannerExplanation } from './GraphPlanner'
import { LeaveGuardDialog, useLeaveGuard, useLeavePrompt } from './management/LeaveGuard'
import { WorkflowEditor } from './WorkflowEditor'
import { WorkflowLibrary } from './editor/WorkflowLibrary'
import { PublishReview } from './editor/PublishReview'
import { safeErrorMessage } from './editor/diagnostics'
import {
  cloneWorkflowSource,
  commandId,
  draftId,
  draftStalenessBlocksFreeze,
  newSingleNodeWorkflow,
  sourceFromRevision,
  structuralDiff,
} from './lib/editor'

interface EditorCatalogs {
  providers: ProviderCatalogWire[]
  active_model: { provider_id: string; model_id: string } | null
  skills: SkillCatalogWire[]
  tools: ToolCatalogWire[]
  contracts: ArtifactContractCatalogWire[]
}

const EMPTY_CATALOGS: EditorCatalogs = {
  providers: [],
  active_model: null,
  skills: [],
  tools: [],
  contracts: [],
}

function generatedDefinitionId(): string {
  return `workflow_${crypto.randomUUID().replaceAll('-', '_').slice(0, 16)}`
}

function selectedDraftStorageKey(workspaceId: string | null | undefined): string {
  return `morrow.editor.draft.${workspaceId ?? ''}`
}

export function EditorShell({
  client,
  onManageTools,
  onManageAgents,
  registerGuard,
}: {
  client: ApiClient
  onManageTools?: () => void
  onManageAgents?: () => void
  registerGuard?: (guard: DirtyGuard) => () => void
}) {
  const [agents, setAgents] = useState<AgentDefinitionViewWire[]>([])
  const [workflows, setWorkflows] = useState<WorkflowDefinitionViewWire[]>([])
  const [drafts, setDrafts] = useState<WorkflowDraftViewWire[]>([])
  const [catalogs, setCatalogs] = useState<EditorCatalogs>(EMPTY_CATALOGS)
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null)
  const [libraryOpen, setLibraryOpen] = useState(true)
  const [targetId, setTargetId] = useState(generatedDefinitionId)
  const [targetName, setTargetName] = useState('My Workflow')
  const [baseSource, setBaseSource] = useState<WorkflowDefinitionSourceWire | null>(null)
  const [more, setMore] = useState({ agents: false, workflows: false, drafts: false })
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [freezeDiagnostics, setFreezeDiagnostics] = useState<WorkflowDraftDiagnosticWire[]>([])
  const draftLoadEpoch = useRef(0)
  const freezeCommand = useRef<string | null>(null)
  const freezeInFlight = useRef(false)
  const cloneIntent = useRef<{ draftId: string; commandId: string } | null>(null)
  const cloneInFlight = useRef(false)
  const [controller] = useState(() => new WorkflowDraftController(client))
  const controllerState = useSyncExternalStore(
    controller.subscribe,
    controller.getState,
    controller.getState,
  )
  const { open: guardOpen, confirmLeave: promptLeave, settle: settleLeave } = useLeavePrompt()

  const draft = controllerState.serverSnapshot
  const localSource = controllerState.localSource
  const dirty = controller.dirty
  const saving = busy || controllerState.saveState === 'saving' || controllerState.saveState === 'scheduled'

  useLeaveGuard(registerGuard, dirty, promptLeave)

  useEffect(() => () => controller.dispose(), [controller])

  async function refresh() {
    try {
      const [nextAgents, nextWorkflows, nextDrafts, nextCatalogs] = await Promise.all([
        client.listAgentDefinitions(),
        client.listWorkflowDefinitions(),
        client.listWorkflowDrafts(),
        client.editorCatalogs(),
      ])
      setMore({
        agents: nextAgents.length === 100,
        workflows: nextWorkflows.length === 100,
        drafts: nextDrafts.length === 100,
      })
      setAgents(nextAgents)
      setWorkflows(nextWorkflows)
      setDrafts(nextDrafts)
      setCatalogs(nextCatalogs)
      setSelectedWorkflowId(current => current ?? nextWorkflows[0]?.workflow_definition_id ?? null)
      const draftKey = selectedDraftStorageKey(client.workspaceId)
      const requested = sessionStorage.getItem(draftKey)
      const selected = requested === null
        ? undefined
        : nextDrafts.find(item => item.draft.draft_id === requested)
      if (selected !== undefined) {
        const currentDraftId = controller.getState().scope?.draftId
        if (currentDraftId !== selected.draft.draft_id) void loadDraft(selected, nextWorkflows)
        sessionStorage.removeItem(draftKey)
      } else if (requested !== null) {
        sessionStorage.removeItem(draftKey)
      }
    } catch (error: unknown) {
      setMessage(safeErrorMessage(error, 'Editor Catalog 加载失败'))
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  const selectedWorkflow = workflows.find(
    item => item.workflow_definition_id === selectedWorkflowId,
  )
  const sourceRevision = Math.max(0, ...workflows.map(item => item.source_revision ?? 0))

  async function loadMore(kind: 'agents' | 'workflows' | 'drafts') {
    try {
      if (kind === 'agents') {
        const page = await client.listAgentDefinitions(agents.at(-1)?.definition_id)
        setAgents(old => [...old, ...page])
        setMore(old => ({ ...old, agents: page.length === 100 }))
      }
      if (kind === 'workflows') {
        const page = await client.listWorkflowDefinitions(workflows.at(-1)?.workflow_definition_id)
        setWorkflows(old => [...old, ...page])
        setMore(old => ({ ...old, workflows: page.length === 100 }))
      }
      if (kind === 'drafts') {
        const page = await client.listWorkflowDrafts(drafts.at(-1)?.draft.draft_id)
        setDrafts(old => [...old, ...page])
        setMore(old => ({ ...old, drafts: page.length === 100 }))
      }
    } catch (error: unknown) {
      setMessage(safeErrorMessage(error, '目录加载失败'))
    }
  }

  function rememberDraft(value: WorkflowDraftViewWire) {
    setDrafts(current => [
      value,
      ...current.filter(item => item.draft.draft_id !== value.draft.draft_id),
    ])
  }

  async function saveSource() {
    if (localSource === null || draft === null) return
    const saved = await controller.saveNow()
    if (!saved.ok) {
      setMessage('请先解决 Draft 保存问题，再更新可复用定义。')
      return
    }
    const state = controller.getState()
    if (state.serverSnapshot === null || state.localSource === null) return
    setBusy(true)
    try {
      const workflow = workflows.find(item => item.workflow_definition_id === state.localSource?.workflow_definition_id)
      await client.writeWorkflowSource(
        state.localSource,
        workflow?.source_revision ?? sourceRevision,
        commandId('workflow_source'),
        workflow === undefined,
      )
      await refresh()
      setMessage('定义已更新。')
    } catch (error: unknown) {
      setMessage(safeErrorMessage(error, '更新可复用定义失败'))
    } finally {
      setBusy(false)
    }
  }

  function showDraft(value: WorkflowDraftViewWire, workflowList = workflows) {
    setMessage(null)
    sessionStorage.setItem(selectedDraftStorageKey(client.workspaceId), value.draft.draft_id)
    const workflow = workflowList.find(
      item => item.workflow_definition_id === value.draft.source.workflow_definition_id,
    )
    setSelectedWorkflowId(value.draft.source.workflow_definition_id)
    setLibraryOpen(false)
    controller.load(
      { workspaceId: client.workspaceId ?? value.draft.workspace_id, draftId: value.draft.draft_id },
      value,
    )
    setFreezeDiagnostics([])
    setBaseSource(
      workflow?.published_revision === null || workflow === undefined
        ? null
        : sourceFromRevision(workflow.published_revision),
    )
  }

  async function loadDraft(value: WorkflowDraftViewWire, workflowList = workflows) {
    const epoch = ++draftLoadEpoch.current
    setMessage('正在读取 Draft 服务器事实…')
    try {
      const latest = await client.getWorkflowDraft(value.draft.draft_id)
      if (epoch !== draftLoadEpoch.current) return
      showDraft(latest, workflowList)
    } catch (error: unknown) {
      if (epoch === draftLoadEpoch.current) setMessage(safeErrorMessage(error, '读取 Draft 失败'))
    }
  }

  async function checkDraft() {
    if (draft === null) return
    if (controller.dirty) {
      setBusy(true)
      try {
        const saved = await controller.saveNow()
        setMessage(saved.ok ? '草稿已保存并检查。' : '检查未完成；请先处理保存问题。')
      } finally {
        setBusy(false)
      }
      return
    }
    setBusy(true)
    try {
      const latest = await client.getWorkflowDraft(draft.draft.draft_id)
      const local = controller.getState().localSource
      if (local !== null && JSON.stringify(local) !== JSON.stringify(latest.draft.source)) {
        setMessage('服务器版本已变化；本地内容仍保留，请先核对后再检查。')
        return
      }
      showDraft(latest)
      setMessage('已重新读取 Draft；检查状态已更新。')
    } catch (error: unknown) {
      setMessage(safeErrorMessage(error, 'Draft 检查失败'))
    } finally {
      setBusy(false)
    }
  }

  async function reconcileDraft() {
    const result = await controller.reconcile()
    setMessage(result === 'matched'
      ? '已核对服务器结果，保存状态已恢复。'
      : result === 'conflict'
        ? '服务器版本与本地修改不同；本地内容仍保留。'
        : '无法核对服务器结果，请稍后重试。')
  }

  async function useServerVersion() {
    if (draft === null) return
    const value = draft
    setBusy(true)
    try {
      const latest = await client.getWorkflowDraft(value.draft.draft_id)
      showDraft(latest)
      setMessage('已采用服务器版本；本地未确认修改已放弃，持久化 Draft 未删除。')
    } catch (error: unknown) {
      setMessage(safeErrorMessage(error, '读取服务器版本失败'))
    } finally {
      setBusy(false)
    }
  }

  async function createNewDraftFromCurrent() {
    if (draft === null || busy || cloneInFlight.current) return
    const intent = cloneIntent.current ?? { draftId: draftId(), commandId: commandId('draft_clone') }
    cloneIntent.current = intent
    const source = cloneWorkflowSource(draft.draft.source, draft.draft.source.workflow_definition_id, draft.draft.source.name)
    const expectedRevision = workflows.find(item => item.workflow_definition_id === source.workflow_definition_id)?.source_revision ?? sourceRevision
    cloneInFlight.current = true
    setBusy(true)
    setMessage(null)
    try {
      const value = await client.createWorkflowDraft(source, expectedRevision, intent.commandId, intent.draftId)
      rememberDraft(value)
      showDraft(value)
      cloneIntent.current = null
      setMessage('新草稿已创建。')
    } catch (error: unknown) {
      if (error instanceof ApiError && error.status === 0) {
        try {
          const readback = await client.getWorkflowDraft(intent.draftId)
          rememberDraft(readback)
          showDraft(readback)
          cloneIntent.current = null
          setMessage('新 Draft 创建结果已核对；原发布版本仍保持只读。')
          return
        } catch {
          setMessage('新 Draft 创建结果未知；请稍后核对目录，不会重复创建。')
          return
        }
      }
      setMessage(safeErrorMessage(error, '创建新 Draft 失败'))
    } finally {
      setBusy(false)
      cloneInFlight.current = false
    }
  }

  async function leaveDraft(action: () => void) {
    if (!dirty) {
      action()
      return
    }
    if (await promptLeave()) action()
  }

  function chooseWorkflow(value: WorkflowDefinitionViewWire) {
    void leaveDraft(() => {
      draftLoadEpoch.current += 1
      cloneIntent.current = null
      freezeCommand.current = null
      sessionStorage.removeItem(selectedDraftStorageKey(client.workspaceId))
      setSelectedWorkflowId(value.workflow_definition_id)
      setLibraryOpen(true)
      controller.clear()
      setFreezeDiagnostics([])
      const copy = value.origin === 'builtin'
      setTargetId(copy ? `${value.workflow_definition_id.replace(/^builtin_/, '')}_copy` : value.workflow_definition_id)
      setTargetName(copy ? `${value.source?.name ?? value.workflow_definition_id} Copy` : value.source?.name ?? value.workflow_definition_id)
      setBaseSource(null)
      setMessage(null)
    })
  }

  function chooseDraft(value: WorkflowDraftViewWire) {
    if (draft?.draft.draft_id !== value.draft.draft_id) {
      cloneIntent.current = null
      freezeCommand.current = null
    }
    void leaveDraft(() => loadDraft(value))
  }

  function chooseBlank() {
    void leaveDraft(() => {
      draftLoadEpoch.current += 1
      cloneIntent.current = null
      freezeCommand.current = null
      sessionStorage.removeItem(selectedDraftStorageKey(client.workspaceId))
      setSelectedWorkflowId(null)
      setLibraryOpen(true)
      controller.clear()
      setTargetId(generatedDefinitionId())
      setTargetName('My Workflow')
      setBaseSource(null)
      setFreezeDiagnostics([])
      setMessage(null)
    })
  }

  async function createDraft() {
    setMessage(null)
    const version = agents.find(item => item.published_version !== null)?.published_version
    let source: WorkflowDefinitionSourceWire
    if (selectedWorkflow?.source !== null && selectedWorkflow?.source !== undefined) {
      source = cloneWorkflowSource(selectedWorkflow.source, targetId, targetName)
    } else if (version !== null && version !== undefined) {
      source = newSingleNodeWorkflow(targetId, targetName, version)
    } else {
      setMessage('请先创建并发布至少一个 AgentDefinition。')
      return
    }
    setBusy(true)
    try {
      const view = await client.createWorkflowDraft(
        source,
        sourceRevision,
        commandId('draft_create'),
        draftId(),
      )
      rememberDraft(view)
      showDraft(view)
      cloneIntent.current = null
      freezeCommand.current = null
      setMessage(view.draft.status === 'valid' ? '草稿已创建，检查通过。' : '草稿已创建，请处理检查问题。')
    } catch (error: unknown) {
      setMessage(safeErrorMessage(error, 'Draft 创建失败'))
    } finally {
      setBusy(false)
    }
  }

  async function freeze() {
    if (draft === null || busy || freezeInFlight.current) return
    freezeInFlight.current = true
    setBusy(true)
    let latest: WorkflowDraftViewWire | null = null
    try {
      const saved = await controller.saveNow()
      if (!saved.ok) {
        setMessage('发布前必须先保存最新修改；本地内容仍保留。')
        return
      }
      latest = controller.getState().serverSnapshot
      if (latest === null) return
      if (latest.draft.status !== 'valid' || draftStalenessBlocksFreeze(latest.stale_reasons)) {
        setMessage('当前 Draft 尚未满足发布条件；请先处理检查问题或目录变化。')
        return
      }
      const freezeKey = freezeCommand.current ?? commandId('draft_freeze')
      freezeCommand.current = freezeKey
      setMessage('正在发布…')
      const value = await client.freezeWorkflowDraft(
        latest.draft.draft_id,
        latest.draft.row_version,
        freezeKey,
      )
      rememberDraft(value.workflow_draft)
      showDraft(value.workflow_draft)
      freezeCommand.current = null
      setFreezeDiagnostics([])
      setMessage('版本已发布。')
      await refresh()
    } catch (error: unknown) {
      if (error instanceof ApiError) setFreezeDiagnostics(error.diagnostics)
      if (error instanceof ApiError && error.status === 0) {
        try {
          const readback = await client.getWorkflowDraft(latest?.draft.draft_id ?? draft.draft.draft_id)
          if (readback.draft.status === 'frozen' && readback.draft.frozen_workflow_revision_id !== null) {
            rememberDraft(readback)
            showDraft(readback)
            freezeCommand.current = null
            setFreezeDiagnostics([])
            setMessage('版本已发布。')
            await refresh()
            return
          }
        } catch {
          // Keep the same command identity for an explicit later retry/readback.
        }
        setMessage('发布结果未知；已读取失败，保留本地内容，请稍后核对服务器状态。')
      } else {
        setMessage(safeErrorMessage(error, '发布版本失败'))
      }
    } finally {
      setBusy(false)
      freezeInFlight.current = false
    }
  }

  const revisionDiff = useMemo(
    () => structuralDiff(baseSource, localSource),
    [baseSource, localSource],
  )
  const currentMessage = message ?? controllerState.message

  return (
    <main className="workflow-editor-shell min-h-0 flex-1">
      <WorkflowLibrary
        open={libraryOpen}
        drafts={drafts}
        workflows={workflows}
        selectedDraftId={draft?.draft.draft_id ?? null}
        selectedWorkflowId={selectedWorkflowId}
        more={more}
        onToggle={() => setLibraryOpen(value => !value)}
        onNew={chooseBlank}
        onDraft={chooseDraft}
        onWorkflow={chooseWorkflow}
        onLoadMore={kind => { void loadMore(kind) }}
        onManageAgents={onManageAgents}
        onManageTools={onManageTools}
      />
      <div className="workflow-editor-content min-h-0">
        {draft === null || localSource === null ? (
          <section className="mx-auto w-full max-w-3xl overflow-y-auto p-6">
            {selectedWorkflow && <DefinitionLifecycle key={selectedWorkflow.workflow_definition_id} client={client} kind="workflow" id={selectedWorkflow.workflow_definition_id} revision={selectedWorkflow.head?.row_version ?? 0} enabled={selectedWorkflow.head?.enabled ?? false} version={selectedWorkflow.head?.workflow_revision_id ?? null} readOnly={selectedWorkflow.origin === 'builtin' || selectedWorkflow.revoked} onRefresh={() => refresh()} />}
            <h2 className="font-serif text-2xl font-semibold">创建 Workflow Draft</h2>

            <div className="mt-5 grid grid-cols-2 gap-3"><Field label="Definition ID（自动生成后可在详情查看）"><input aria-label="Definition ID" className="editor-input font-mono" value={targetId} onChange={event => setTargetId(event.target.value)} /></Field><Field label="名称"><input aria-label="工作流名称" className="editor-input" value={targetName} onChange={event => setTargetName(event.target.value)} /></Field></div>
            <button type="button" className="editor-button mt-4 border-accent text-accent" disabled={saving || targetId.trim() === '' || targetName.trim() === ''} onClick={() => void createDraft()}>创建 Draft</button>
            {currentMessage !== null && <p role="status" className="mt-3 text-xs text-secondary">{currentMessage}</p>}
            <GraphPlanner client={client} definitionId={targetId} name={targetName} onDraft={value => { rememberDraft(value); showDraft(value) }} />
          </section>
        ) : (
          <>
            <PublishReview
              status={draft.draft.status}
              frozenRevisionId={draft.draft.frozen_workflow_revision_id}
              saveState={controllerState.saveState}
              checkState={controllerState.checkState}
              dirty={dirty}
              staleReasons={draft.stale_reasons}
              diagnostics={[...draft.draft.diagnostics, ...freezeDiagnostics]}
              busy={busy}
              unresolved={controllerState.unresolvedCommand !== null}
              message={currentMessage}
              onCheck={() => { void checkDraft() }}
              onUpdateDefinition={() => { void saveSource() }}
              onPublish={() => { void freeze() }}
              onReconcile={() => { void reconcileDraft() }}
              onUseServer={() => { void useServerVersion() }}
              onNewDraft={() => { void createNewDraftFromCurrent() }}
            />
            {draft.draft.planner && <PlannerExplanation metadata={draft.draft.planner} edited={dirty || draft.draft.planner.source_hash !== draft.draft.source_hash} />}
            <WorkflowEditor
              source={localSource}
              diagnostics={[...draft.draft.diagnostics, ...freezeDiagnostics]}
              agents={agents}
              contracts={catalogs.contracts}
              disabled={draft.draft.status === 'frozen' || draft.draft.status === 'rejected'}
              onChange={source => { setFreezeDiagnostics([]); setMessage(null); controller.edit(source) }}
              scope={controllerState.scope ?? undefined}
              libraryOpen={libraryOpen}
              onToggleLibrary={() => setLibraryOpen(value => !value)}
            />
            <details className="border-t border-subtle px-4 py-2 text-xs"><summary className="cursor-pointer text-secondary">版本与变更详情 · {revisionDiff.length} 项</summary><ul className="mt-2 grid max-h-40 grid-cols-2 gap-2 overflow-y-auto">{revisionDiff.map(line => <li key={line.path} className="rounded-[8px] border border-subtle p-2 font-mono"><div className="text-secondary">{line.path}</div><div className="text-failed">− {line.before}</div><div className="text-completed">+ {line.after}</div></li>)}</ul></details>
          </>
        )}
      </div>
      <LeaveGuardDialog
        open={guardOpen}
        title="工作流草稿有未保存修改"
        description="离开前保存修改，或放弃未保存的修改。"
        saveLabel="保存后离开"
        discardLabel="放弃未保存修改并离开"
        stayLabel="继续编辑"
        onSave={() => { void controller.saveNow().then(result => { settleLeave(result.ok) }) }}
        onDiscard={() => { controller.discardLocal(); settleLeave(true) }}
        onStay={() => settleLeave(false)}
      />
    </main>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="flex flex-col gap-1 text-xs text-secondary"><span>{label}</span>{children}</label>
}
