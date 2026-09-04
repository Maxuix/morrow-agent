import { useEffect, useMemo, useState } from 'react'
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
import { AgentInspector } from './AgentInspector'
import { WorkflowEditor } from './WorkflowEditor'
import { GraphPlanner, PlannerExplanation } from './GraphPlanner'
import {
  cloneWorkflowSource,
  commandId,
  draftStalenessBlocksFreeze,
  draftId,
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

export function EditorShell({ client }: { client: ApiClient }) {
  const [section, setSection] = useState<'workflow' | 'agent'>('workflow')
  const [agents, setAgents] = useState<AgentDefinitionViewWire[]>([])
  const [workflows, setWorkflows] = useState<WorkflowDefinitionViewWire[]>([])
  const [drafts, setDrafts] = useState<WorkflowDraftViewWire[]>([])
  const [catalogs, setCatalogs] = useState<EditorCatalogs>(EMPTY_CATALOGS)
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null)
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null)
  const [targetId, setTargetId] = useState('my_workflow')
  const [targetName, setTargetName] = useState('My Workflow')
  const [draft, setDraft] = useState<WorkflowDraftViewWire | null>(null)
  const [localSource, setLocalSource] = useState<WorkflowDefinitionSourceWire | null>(null)
  const [savedSource, setSavedSource] = useState<WorkflowDefinitionSourceWire | null>(null)
  const [baseSource, setBaseSource] = useState<WorkflowDefinitionSourceWire | null>(null)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [freezeDiagnostics, setFreezeDiagnostics] = useState<WorkflowDraftDiagnosticWire[]>([])

  async function refresh(selectedId?: string) {
    const [nextAgents, nextWorkflows, nextDrafts, nextCatalogs] = await Promise.all([
      client.listAgentDefinitions(),
      client.listWorkflowDefinitions(),
      client.listWorkflowDrafts(),
      client.editorCatalogs(),
    ])
    setAgents(nextAgents)
    setWorkflows(nextWorkflows)
    setDrafts(nextDrafts)
    setCatalogs(nextCatalogs)
    setSelectedAgentId((current) =>
      selectedId ?? current ?? nextAgents[0]?.definition_id ?? null,
    )
    setSelectedWorkflowId((current) => current ?? nextWorkflows[0]?.workflow_definition_id ?? null)
  }

  useEffect(() => {
    void refresh().catch((error: unknown) => {
      setMessage(error instanceof Error ? error.message : 'Editor Catalog 加载失败')
    })
  }, [])

  const selectedWorkflow = workflows.find(
    (item) => item.workflow_definition_id === selectedWorkflowId,
  )
  const selectedAgent = agents.find((item) => item.definition_id === selectedAgentId) ?? null
  const sourceRevision = Math.max(0, ...workflows.map((item) => item.source_revision ?? 0))
  const dirty =
    localSource !== null &&
    savedSource !== null &&
    JSON.stringify(localSource) !== JSON.stringify(savedSource)

  function rememberDraft(value: WorkflowDraftViewWire) {
    setDrafts((current) => [
      value,
      ...current.filter((item) => item.draft.draft_id !== value.draft.draft_id),
    ])
  }

  useEffect(() => {
    if (
      draft === null ||
      localSource === null ||
      !dirty ||
      saving ||
      draft.draft.status === 'frozen' ||
      draft.draft.status === 'rejected'
    ) {
      return
    }
    const source = structuredClone(localSource)
    const rowVersion = draft.draft.row_version
    const timer = window.setTimeout(() => {
      setSaving(true)
      setMessage('正在通过 Core Compiler 校验…')
      client
        .updateWorkflowDraft(
          draft.draft.draft_id,
          source,
          rowVersion,
          commandId('draft_update'),
        )
        .then((view) => {
          setFreezeDiagnostics([])
          setDraft(view)
          rememberDraft(view)
          setSavedSource(source)
          setMessage(view.draft.status === 'valid' ? 'Draft 已校验并保存。' : 'Draft 已保存；请修复编译错误。')
        })
        .catch((error: unknown) => {
          setMessage(
            error instanceof Error
              ? `${error.message}；若另一标签页已编辑，请重新打开 Draft。`
              : 'Draft 保存失败',
          )
        })
        .finally(() => setSaving(false))
    }, 400)
    return () => window.clearTimeout(timer)
  }, [client, dirty, draft, localSource, saving])

  function chooseWorkflow(value: WorkflowDefinitionViewWire) {
    setSelectedWorkflowId(value.workflow_definition_id)
    setDraft(null)
    setLocalSource(null)
    setSavedSource(null)
    setFreezeDiagnostics([])
    const copy = value.origin === 'builtin'
    setTargetId(copy ? `${value.workflow_definition_id.replace(/^builtin_/, '')}_copy` : value.workflow_definition_id)
    setTargetName(copy ? `${value.source?.name ?? value.workflow_definition_id} Copy` : value.source?.name ?? value.workflow_definition_id)
  }

  function chooseDraft(value: WorkflowDraftViewWire) {
    const workflow = workflows.find(
      (item) => item.workflow_definition_id === value.draft.source.workflow_definition_id,
    )
    setSelectedWorkflowId(value.draft.source.workflow_definition_id)
    setDraft(value)
    setLocalSource(structuredClone(value.draft.source))
    setSavedSource(structuredClone(value.draft.source))
    setFreezeDiagnostics([])
    setBaseSource(
      workflow?.published_revision === null || workflow === undefined
        ? null
        : sourceFromRevision(workflow.published_revision),
    )
    setMessage('已重新打开持久化 Draft。')
  }

  async function createDraft() {
    setMessage(null)
    const version = agents.find((item) => item.published_version !== null)?.published_version
    let source: WorkflowDefinitionSourceWire
    if (selectedWorkflow?.source !== null && selectedWorkflow?.source !== undefined) {
      source = cloneWorkflowSource(selectedWorkflow.source, targetId, targetName)
    } else if (version !== null && version !== undefined) {
      source = newSingleNodeWorkflow(targetId, targetName, version)
    } else {
      setMessage('请先创建并发布至少一个 AgentDefinition。')
      return
    }
    setSaving(true)
    try {
      const view = await client.createWorkflowDraft(
        source,
        sourceRevision,
        commandId('draft_create'),
        draftId(),
      )
      setDraft(view)
      rememberDraft(view)
      setLocalSource(view.draft.source)
      setSavedSource(view.draft.source)
      setFreezeDiagnostics([])
      setBaseSource(
        selectedWorkflow?.published_revision === null || selectedWorkflow === undefined
          ? null
          : sourceFromRevision(selectedWorkflow.published_revision),
      )
      setMessage(view.draft.status === 'valid' ? 'Draft 已创建并通过 Compiler。' : 'Draft 已创建，存在编译错误。')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Draft 创建失败')
    } finally {
      setSaving(false)
    }
  }

  async function freeze() {
    if (draft === null) return
    setSaving(true)
    setMessage(null)
    try {
      const value = await client.freezeWorkflowDraft(
        draft.draft.draft_id,
        draft.draft.row_version,
        commandId('draft_freeze'),
      )
      setDraft(value.workflow_draft)
      rememberDraft(value.workflow_draft)
      setSavedSource(value.workflow_draft.draft.source)
      setFreezeDiagnostics([])
      setMessage(`已冻结 Revision ${String(value.workflow_revision.workflow_revision_id)}`)
      await refresh()
    } catch (error) {
      if (error instanceof ApiError) setFreezeDiagnostics(error.diagnostics)
      setMessage(error instanceof Error ? error.message : 'Freeze 失败')
    } finally {
      setSaving(false)
    }
  }

  const revisionDiff = useMemo(
    () => structuralDiff(baseSource, localSource),
    [baseSource, localSource],
  )

  return (
    <main className="grid min-h-0 flex-1 grid-cols-[260px_minmax(0,1fr)]">
      <aside className="min-h-0 overflow-y-auto border-r border-subtle p-3">
        <div className="grid grid-cols-2 gap-1 rounded-[8px] border border-subtle bg-base p-1">
          <button type="button" className={`rounded-[6px] px-2 py-1.5 text-xs ${section === 'workflow' ? 'bg-raised text-accent' : 'text-secondary'}`} onClick={() => setSection('workflow')}>Workflows</button>
          <button type="button" className={`rounded-[6px] px-2 py-1.5 text-xs ${section === 'agent' ? 'bg-raised text-accent' : 'text-secondary'}`} onClick={() => setSection('agent')}>Agents</button>
        </div>
        {section === 'workflow' ? (
          <>
            <button type="button" className="editor-button mt-3 w-full" onClick={() => { setSelectedWorkflowId(null); setDraft(null); setTargetId('my_workflow'); setTargetName('My Workflow') }}>＋ 空白工作流</button>
            {drafts.length > 0 && (
              <>
                <h2 className="mt-4 text-xs font-medium tracking-wide text-secondary">Durable Drafts</h2>
                <ul className="mt-2 flex flex-col gap-1">
                  {drafts.map((value) => <li key={value.draft.draft_id}><button type="button" onClick={() => chooseDraft(value)} className={`w-full rounded-[8px] border px-2 py-2 text-left ${draft?.draft.draft_id === value.draft.draft_id ? 'border-accent bg-raised' : 'border-transparent'}`}><div className="truncate text-sm">{value.draft.source.name}</div><div className="mt-0.5 font-mono text-[10px] text-secondary">{value.draft.status} · row {value.draft.row_version}</div></button></li>)}
                </ul>
              </>
            )}
            <h2 className="mt-4 text-xs font-medium tracking-wide text-secondary">Workflow Catalog</h2>
            <ul className="mt-2 flex flex-col gap-1">
              {workflows.map((workflow) => <li key={workflow.workflow_definition_id}><button type="button" onClick={() => chooseWorkflow(workflow)} className={`w-full rounded-[8px] border px-2 py-2 text-left ${selectedWorkflowId === workflow.workflow_definition_id ? 'border-accent bg-raised' : 'border-transparent'}`}><div className="text-sm">{workflow.source?.name ?? workflow.workflow_definition_id}</div><div className="mt-0.5 font-mono text-[10px] text-secondary">{workflow.origin} · {workflow.head === null ? '未发布' : `row ${workflow.head.row_version}`}</div></button></li>)}
            </ul>
          </>
        ) : (
          <>
            <h2 className="mt-4 text-xs font-medium tracking-wide text-secondary">Agent Catalog</h2>
            <ul className="mt-2 flex flex-col gap-1">{agents.map((agent) => <li key={agent.definition_id}><button type="button" onClick={() => setSelectedAgentId(agent.definition_id)} className={`w-full rounded-[8px] border px-2 py-2 text-left ${selectedAgentId === agent.definition_id ? 'border-accent bg-raised' : 'border-transparent'}`}><div className="text-sm">{agent.source?.name ?? agent.definition_id}</div><div className="mt-0.5 font-mono text-[10px] text-secondary">{agent.origin} · {agent.head?.enabled === false ? 'disabled' : agent.published_version === null ? '未发布' : `v${agent.published_version.version}`}</div></button></li>)}</ul>
          </>
        )}
      </aside>
      <div className="flex min-h-0 flex-col">
        {section === 'agent' ? (
          <AgentInspector client={client} definitions={agents} selected={selectedAgent} providers={catalogs.providers} skills={catalogs.skills} tools={catalogs.tools} onRefresh={async (definitionId) => { await refresh(definitionId) }} />
        ) : draft === null || localSource === null ? (
          <section className="mx-auto w-full max-w-2xl overflow-y-auto p-6">
            <h2 className="font-serif text-2xl font-semibold">创建 Workflow Draft</h2>
            <p className="mt-2 text-sm leading-relaxed text-secondary">从选中的建议复制，或从已发布 Agent 创建单节点图。编辑期间不会产生 Revision。</p>
            <div className="mt-5 grid grid-cols-2 gap-3"><Field label="Definition ID"><input className="editor-input font-mono" value={targetId} onChange={(event) => setTargetId(event.target.value)} /></Field><Field label="名称"><input className="editor-input" value={targetName} onChange={(event) => setTargetName(event.target.value)} /></Field></div>
            <button type="button" className="editor-button mt-4 border-accent text-accent" disabled={saving || targetId === '' || targetName === ''} onClick={() => void createDraft()}>创建 Draft</button>
            {message !== null && <p role="status" className="mt-3 text-xs text-secondary">{message}</p>}
            <GraphPlanner client={client} definitionId={targetId} name={targetName} onDraft={(value) => { rememberDraft(value); chooseDraft(value) }} />
          </section>
        ) : (
          <>
            <header className="flex items-center gap-3 border-b border-subtle px-4 py-2">
              <span className={`rounded-[8px] border px-2 py-1 text-xs ${draft.draft.status === 'valid' ? 'border-completed text-completed' : draft.draft.status === 'invalid' ? 'border-failed text-failed' : 'border-subtle text-secondary'}`}>{draft.draft.status}</span>
              <span className="font-mono text-xs text-secondary">row {draft.draft.row_version}</span>
              {dirty && <span className="text-xs text-blocked">待保存</span>}
              {draft.stale_reasons.length > 0 && <span className="text-xs text-blocked">Catalog/Head 已变化：{draft.stale_reasons.join(', ')}</span>}
              {message !== null && <span role="status" className="truncate text-xs text-secondary">{message}</span>}
              <button type="button" className="editor-button ml-auto border-accent text-accent" disabled={saving || dirty || draft.draft.status !== 'valid' || draftStalenessBlocksFreeze(draft.stale_reasons)} onClick={() => void freeze()}>Freeze Revision</button>
            </header>
            {draft.draft.planner && <PlannerExplanation metadata={draft.draft.planner} edited={dirty || draft.draft.planner.source_hash !== draft.draft.source_hash} />}
            <WorkflowEditor source={localSource} diagnostics={[...draft.draft.diagnostics, ...freezeDiagnostics]} agents={agents} contracts={catalogs.contracts} disabled={draft.draft.status === 'frozen'} onChange={(source) => { setFreezeDiagnostics([]); setLocalSource(source) }} />
            <details className="border-t border-subtle px-4 py-2 text-xs"><summary className="cursor-pointer text-secondary">Revision Diff · {revisionDiff.length} 项</summary><ul className="mt-2 grid max-h-40 grid-cols-2 gap-2 overflow-y-auto">{revisionDiff.map((line) => <li key={line.path} className="rounded-[8px] border border-subtle p-2 font-mono"><div className="text-secondary">{line.path}</div><div className="text-failed">− {line.before}</div><div className="text-completed">+ {line.after}</div></li>)}</ul></details>
          </>
        )}
      </div>
    </main>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="flex flex-col gap-1 text-xs text-secondary"><span>{label}</span>{children}</label>
}
