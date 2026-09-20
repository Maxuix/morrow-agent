import { useEffect, useMemo, useState } from 'react'
import type { ApiClient } from '../api/client'
import type {
  AgentDefinitionViewWire,
  AgentPresetCatalogWire,
  ModelRefWire,
  ProviderCatalogWire,
  SelectionChoiceWire,
  ToolCatalogWire,
} from '../api/types'
import { commandId } from './lib/editor'

const EFFORT_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: '继承（会话设置）' },
  { value: 'model_default', label: '模型默认' },
  { value: 'low', label: '显式 · 低' },
  { value: 'medium', label: '显式 · 中' },
  { value: 'high', label: '显式 · 高' },
]

export function effortChoice(value: string): SelectionChoiceWire | null {
  if (value === '') return null
  if (value === 'model_default') return { mode: 'model_default' }
  return { mode: 'explicit', value }
}

export function parseModelRef(value: string): ModelRefWire | null {
  if (value === '') return null
  const [provider_id, model_id] = value.split('/', 2)
  if (!provider_id || !model_id) throw new Error('模型格式应为 provider/model')
  return { provider_id, model_id }
}

export function generationKey(choice: SelectionChoiceWire | null): string {
  if (choice === null) return ''
  if (choice.mode === 'model_default') return 'model_default'
  return choice.mode === 'explicit' ? choice.value : ''
}

export interface AgentPrefill {
  name: string
  purpose: string
  prompt: string
  tools: 'all' | 'list'
  selected: string[]
  model: string
  effort: string
  expected_head_revision: number
}

export function presetPrefill(row: AgentPresetCatalogWire['presets'][number]): AgentPrefill {
  // Copying keeps the fixed preset prompt out of the editable field on
  // purpose: the copy starts from the preset's role text supplied by the
  // server source, and the user edits from there.
  return {
    name: `${row.name} 自定义`,
    purpose: row.description,
    prompt: '',
    tools: row.access === 'read' ? 'list' : 'all',
    selected: row.access === 'read' ? ['read', 'ls', 'find', 'grep'] : [],
    model: '',
    effort: '',
    expected_head_revision: 0,
  }
}

export function agentPrefill(agent: AgentDefinitionViewWire): AgentPrefill {
  const source = agent.source
  const requirements = source?.tool_requirements ?? []
  const required = requirements.filter((item) => item.requirement === 'required').map((item) => item.name)
  return {
    name: source?.name ?? agent.definition_id,
    purpose: source?.description ?? '',
    prompt: source?.role_prompt ?? '',
    tools: required.length === 0 ? 'all' : 'list',
    selected: required,
    model:
      source && source.model_selection !== 'invoking_active'
        ? `${source.model_selection.provider_id}/${source.model_selection.model_id}`
        : '',
    effort: '',
    expected_head_revision: agent.head?.row_version ?? 0,
  }
}

/**
 * Preset model/thinking preferences plus the few-field custom agent save.
 * Preset prompts and tool policy are fixed (D04); prompt/tool edits require
 * copying into a custom agent. Advanced fields stay on the old editor page.
 */
export function AgentPresetsPanel({client, workspace, connection, workspaceName, onDirtyChange, onOpenDefinition}: {
  client: ApiClient; workspace: string; connection: string
  workspaceName?: string
  onDirtyChange?: (dirty: boolean) => void
  onOpenDefinition?: (definitionId: string) => void
}) {
  const [catalog, setCatalog] = useState<AgentPresetCatalogWire | null>(null)
  const [agents, setAgents] = useState<AgentDefinitionViewWire[]>([])
  const [providers, setProviders] = useState<ProviderCatalogWire[]>([])
  const [tools, setTools] = useState<ToolCatalogWire[]>([])
  const [edits, setEdits] = useState<Record<string, {model: string; effort: string}>>({})
  const [prefill, setPrefill] = useState<AgentPrefill | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  const applyCatalog = (
    presets: AgentPresetCatalogWire,
    definitions: AgentDefinitionViewWire[],
    catalogs: { providers: ProviderCatalogWire[]; tools: ToolCatalogWire[] },
  ) => {
    setCatalog(presets)
    setAgents(definitions)
    setProviders(catalogs.providers)
    setTools(catalogs.tools)
    setEdits((current) => {
      const next: Record<string, {model: string; effort: string}> = {}
      for (const row of presets.presets) {
        next[row.definition_id] = current[row.definition_id] ?? {
          model: row.preference?.model ? `${row.preference.model.provider_id}/${row.preference.model.model_id}` : '',
          effort: generationKey(row.preference?.generation ?? null),
        }
      }
      return next
    })
  }

  const load = async () => {
    try {
      const [presets, definitions, catalogs] = await Promise.all([
        client.agentPresets(workspace),
        client.listAgentDefinitions(),
        client.editorCatalogs(),
      ])
      applyCatalog(presets, definitions, catalogs)
    } catch (e) { setError((e as Error).message) }
  }

  useEffect(() => {
    let cancelled = false
    setCatalog(null)
    setAgents([])
    setProviders([])
    setTools([])
    setEdits({})
    setPrefill(null)
    setError('')
    setNotice('')
    onDirtyChange?.(false)
    void (async () => {
      try {
        const [presets, definitions, catalogs] = await Promise.all([
          client.agentPresets(workspace),
          client.listAgentDefinitions(),
          client.editorCatalogs(),
        ])
        if (cancelled) return
        applyCatalog(presets, definitions, catalogs)
      } catch (e) {
        if (!cancelled) setError((e as Error).message)
      }
    })()
    return () => { cancelled = true }
  }, [client, workspace, onDirtyChange])

  const modelOptions = useMemo(
    () => providers.flatMap((provider) => provider.models.map((model) => ({
      value: `${provider.provider_id}/${model.model_id}`,
      label: `${provider.provider_id}/${model.model_id}${model.active ? '（当前）' : ''}`,
    }))),
    [providers],
  )

  const savePreset = async (definitionId: string) => {
    if (catalog === null) return
    const edit = edits[definitionId]
    if (edit === undefined) return
    setBusy(true); setError(''); setNotice('')
    try {
      const model = parseModelRef(edit.model)
      const generation = effortChoice(edit.effort)
      const value = await client.putAgentPreset(workspace, definitionId, {
        command_id: commandId('preset_pref'),
        definition_id: definitionId,
        preference: { definition_id: definitionId, ...(model !== null ? { model } : {}), ...(generation !== null ? { generation } : {}) },
        expected_revision: catalog.revision,
      })
      setNotice(`已保存 ${definitionId} 的偏好（版本 ${value.revision}）；下次执行该预设时生效。`)
      await load()
    } catch (e) {
      setError(`${(e as Error).message}；已刷新为服务端当前状态，请重试。`)
      await load()
    } finally { setBusy(false) }
  }

  const customAgents = agents.filter((agent) => agent.origin === 'user')
  // The desired-source YAML OCC is document-level: the current store revision
  // is the max across definitions, like the old editor.
  const sourceRevision = Math.max(0, ...agents.map((agent) => agent.source_revision ?? 0))
  return (
    <section className="flex h-full flex-col overflow-y-auto p-3 text-sm" aria-label="Agent 预设与自定义">
      {workspaceName && <p role="status" className="text-xs text-secondary">应用于项目 {workspaceName}</p>}
      {error && <p role="alert" className="text-xs text-failed">{error}</p>}
      {notice && <p role="status" className="text-xs text-secondary">{notice}</p>}
      {catalog === null && !error && <p role="status" className="text-xs text-secondary">正在读取 Agent 目录…</p>}
      {connection !== 'live' && <p role="status" className="text-xs text-blocked">连接中断；显示最近一次目录状态。</p>}
      <h3 className="text-xs font-medium tracking-wide text-secondary">预设</h3>
      {catalog?.presets.map((row) => {
        const edit = edits[row.definition_id] ?? { model: '', effort: '' }
        return (
          <article key={row.definition_id} className="mt-2 rounded-[10px] border border-subtle bg-raised p-3 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <strong>{row.name}</strong>
              <span className="rounded-[8px] border border-subtle px-1.5 py-0.5 text-secondary">{row.role}</span>
              <span className="rounded-[8px] border border-subtle px-1.5 py-0.5 text-secondary">{row.access === 'read' ? '只读' : '可写'}</span>
              <span className={row.available ? 'text-completed' : 'text-blocked'}>{row.available ? '可用' : '未启用'}</span>
              <span className="text-secondary">
                模型：{row.preference_source === 'preset_preference' && row.preference?.model
                  ? `${row.preference.model.provider_id}/${row.preference.model.model_id}（预设偏好）`
                  : '继承会话设置'}
              </span>
            </div>
            <p className="mt-2 text-secondary">{row.description}</p>

            <div className="mt-2 grid grid-cols-2 gap-2">
              <label className="flex flex-col gap-1 text-secondary">模型偏好
                <select aria-label={`${row.name} 模型偏好`} className="editor-input" value={edit.model} disabled={busy}
                  onChange={(event) => setEdits((current) => ({ ...current, [row.definition_id]: { ...edit, model: event.target.value } }))}>
                  <option value="">继承会话设置</option>
                  {modelOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-secondary">思考程度
                <select aria-label={`${row.name} 思考程度`} className="editor-input" value={edit.effort} disabled={busy}
                  onChange={(event) => setEdits((current) => ({ ...current, [row.definition_id]: { ...edit, effort: event.target.value } }))}>
                  {EFFORT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
              </label>
            </div>
            <div className="mt-2 flex gap-2">
              <button type="button" className="editor-button border-accent text-accent" disabled={busy}
                onClick={() => void savePreset(row.definition_id)}>保存偏好</button>
              <button type="button" className="editor-button" disabled={busy}
                onClick={() => setPrefill(presetPrefill(row))}>复制为自定义</button>
            </div>
          </article>
        )
      })}
      <h3 className="mt-4 text-xs font-medium tracking-wide text-secondary">自定义 Agent（保存即可用）</h3>
      {customAgents.map((agent) => (
        <article key={agent.definition_id} className="mt-2 rounded-[10px] border border-subtle p-3 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <strong>{agent.source?.name ?? agent.definition_id}</strong>
            <span className={agent.head?.enabled && agent.published_version && !agent.revoked ? 'text-completed' : 'text-blocked'}>
              {agent.head?.enabled && agent.published_version && !agent.revoked ? '可用' : '未启用'}
            </span>
            {agent.source && (
              <span className="text-secondary">
                {agent.source.access_mode_ceiling === 'read' ? '只读' : '可写'} · 模型：
                {agent.source.model_selection === 'invoking_active'
                  ? '继承会话'
                  : `${agent.source.model_selection.provider_id}/${agent.source.model_selection.model_id}（Agent 设置）`}
              </span>
            )}
          </div>
          <p className="mt-1 text-secondary">{agent.source?.description || '（无用途说明）'}</p>
          <button type="button" className="editor-button mt-2" onClick={() => {
            setPrefill(agentPrefill(agent))
            onOpenDefinition?.(agent.definition_id)
          }}>
            编辑（少字段保存，高级字段在下方定义编辑器）
          </button>
        </article>
      ))}
      <CustomAgentForm client={client} providers={providers} tools={tools} prefill={prefill} sourceRevision={sourceRevision} busy={busy} setBusy={setBusy}
        onSaved={async (message) => { setNotice(message); setPrefill(null); onDirtyChange?.(false); await load() }}
        onError={(message) => setError(message)}
        onDirtyChange={onDirtyChange} />
    </section>
  )
}

function CustomAgentForm({client, providers, tools, prefill, sourceRevision, busy, setBusy, onSaved, onError, onDirtyChange}: {
  client: ApiClient
  providers: ProviderCatalogWire[]
  /** Document-level desired-source revision for the OCC write. */
  sourceRevision: number
  tools: ToolCatalogWire[]
  prefill: AgentPrefill | null
  busy: boolean
  setBusy: (value: boolean) => void
  onSaved: (message: string) => Promise<void> | void
  onError: (message: string) => void
  onDirtyChange?: (dirty: boolean) => void
}) {
  const [name, setName] = useState('')
  const [purpose, setPurpose] = useState('')
  const [prompt, setPrompt] = useState('')
  const [allTools, setAllTools] = useState(true)
  const [selectedTools, setSelectedTools] = useState<string[]>([])
  const [model, setModel] = useState('')
  const [effort, setEffort] = useState('')
  useEffect(() => {
    if (prefill === null) return
    setName(prefill.name); setPurpose(prefill.purpose); setPrompt(prefill.prompt)
    setAllTools(prefill.tools === 'all'); setSelectedTools(prefill.selected)
    setModel(prefill.model); setEffort(prefill.effort)
  }, [prefill])
  const modelOptions = providers.flatMap((provider) => provider.models.map((item) => ({
    value: `${provider.provider_id}/${item.model_id}`,
    label: `${provider.provider_id}/${item.model_id}${item.active ? '（当前）' : ''}`,
  })))
  const save = async () => {
    if (!name.trim()) { onError('请填写名称。'); return }
    if (!allTools && selectedTools.length === 0) { onError('自选工具时至少选择一个工具。'); return }
    setBusy(true); onError('')
    try {
      const modelRef = parseModelRef(model)
      const generation = effortChoice(effort)
      const value = await client.quickSaveAgent({
        command_id: commandId('agent_quick_save'),
        name: name.trim(),
        purpose: purpose.trim(),
        prompt: prompt.trim(),
        tools: allTools ? 'all' : selectedTools,
        ...(modelRef !== null ? { model: modelRef } : {}),
        ...(generation !== null ? { generation } : {}),
        expected_source_revision: sourceRevision,
        expected_head_revision: prefill?.expected_head_revision ?? 0,
      })
      await onSaved(`已保存并可用：${value.definition_id}。`)
    } catch (e) { onError((e as Error).message) } finally { setBusy(false) }
  }
  return (
    <div className="mt-2 rounded-[10px] border border-subtle bg-raised p-3 text-xs">
      <label className="flex flex-col gap-1 text-secondary">名称（改名会创建新的自定义 Agent）
        <input aria-label="自定义 Agent 名称" className="editor-input" value={name} maxLength={128} onChange={(event) => { setName(event.target.value); onDirtyChange?.(true) }} />
      </label>
      <label className="mt-2 flex flex-col gap-1 text-secondary">用途
        <input aria-label="自定义 Agent 用途" className="editor-input" value={purpose} maxLength={2048} onChange={(event) => setPurpose(event.target.value)} />
      </label>
      <label className="mt-2 flex flex-col gap-1 text-secondary">提示词
        <textarea aria-label="自定义 Agent 提示词" className="editor-input mt-1 min-h-16" value={prompt} onChange={(event) => setPrompt(event.target.value)} />
      </label>
      <fieldset className="mt-2 text-secondary">
        <legend>工具</legend>
        <label className="flex items-center gap-2"><input type="checkbox" checked={allTools} onChange={(event) => setAllTools(event.target.checked)} />全部可用工具（运行时仍按任务审批收窄）</label>
        {!allTools && (
          <div className="mt-1 flex flex-wrap gap-2">
            {tools.map((tool) => (
              <label key={tool.name} className="flex items-center gap-1">
                <input type="checkbox" aria-label={`工具 ${tool.name}`} checked={selectedTools.includes(tool.name)}
                  onChange={(event) => setSelectedTools((current) => event.target.checked ? [...current, tool.name] : current.filter((item) => item !== tool.name))} />
                {tool.name}
              </label>
            ))}
            {selectedTools.length === 0 && <span className="text-failed">自选时至少选择一个工具。</span>}
          </div>
        )}
      </fieldset>
      <div className="mt-2 grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-1 text-secondary">模型
          <select aria-label="自定义 Agent 模型" className="editor-input" value={model} onChange={(event) => setModel(event.target.value)}>
            <option value="">继承会话设置</option>
            {modelOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-secondary">思考程度
          <select aria-label="自定义 Agent 思考程度" className="editor-input" value={effort} onChange={(event) => setEffort(event.target.value)}>
            {EFFORT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
      </div>
      <button type="button" className="editor-button mt-2 border-accent text-accent" disabled={busy || !name.trim()} onClick={() => void save()}>
        {busy ? '保存中…' : '保存并启用'}
      </button>

    </div>
  )
}
