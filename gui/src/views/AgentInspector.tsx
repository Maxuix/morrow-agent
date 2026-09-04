import { useEffect, useMemo, useState } from 'react'
import type { ApiClient } from '../api/client'
import type {
  AgentDefinitionSourceWire,
  AgentDefinitionViewWire,
  ProviderCatalogWire,
  SkillCatalogWire,
  ToolCatalogWire,
} from '../api/types'
import { commandId, structuralDiff } from './lib/editor'

export function AgentInspector({
  client,
  definitions,
  selected,
  providers,
  skills,
  tools,
  onRefresh,
}: {
  client: ApiClient
  definitions: AgentDefinitionViewWire[]
  selected: AgentDefinitionViewWire | null
  providers: ProviderCatalogWire[]
  skills: SkillCatalogWire[]
  tools: ToolCatalogWire[]
  onRefresh: (definitionId?: string) => Promise<void>
}) {
  const [source, setSource] = useState<AgentDefinitionSourceWire | null>(null)
  const [cloneId, setCloneId] = useState('')
  const [copying, setCopying] = useState(false)
  const [baseSource, setBaseSource] = useState<AgentDefinitionSourceWire | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    if (selected?.source === null || selected === null) {
      setCopying(false)
      setSource(null)
      setBaseSource(null)
      return
    }
    if (selected.origin === 'builtin') {
      setCopying(true)
      setCloneId(`${selected.definition_id.replace(/^builtin_/, '')}_copy`)
      setSource({
        ...structuredClone(selected.source),
        definition_id: `${selected.definition_id.replace(/^builtin_/, '')}_copy`,
        name: `${selected.source.name} Copy`,
        derived_from_version_id:
          selected.published_version?.content_hash === selected.source_hash
            ? selected.published_version.version_id
            : null,
        derived_from_definition_id: selected.definition_id,
        derived_from_source_hash: selected.source_hash,
      })
      setBaseSource(selected.source)
      return
    }
    setCopying(false)
    setCloneId('')
    setSource(structuredClone(selected.source))
    setBaseSource(selected.published_version?.source ?? null)
    const parentId = selected.source.derived_from_version_id
    if (parentId !== null) {
      client
        .getAgentVersion(parentId)
        .then((version) => setBaseSource(version.source))
        .catch(() => setBaseSource(selected.published_version?.source ?? null))
    }
  }, [client, selected])

  const diff = useMemo(() => structuralDiff(baseSource, source), [baseSource, source])
  if (selected === null || source === null) {
    return <p className="p-6 text-sm text-secondary">选择 AgentDefinition 以检查或复制。</p>
  }
  const currentSource = source
  const currentSelected = selected
  const cloning = selected.origin === 'builtin' || copying
  const currentSourceRevision = Math.max(
    0,
    ...definitions.map((item) => item.source_revision ?? 0),
  )
  const modelValue =
    source.model_selection === 'invoking_active'
      ? 'invoking_active'
      : `${source.model_selection.provider_id}/${source.model_selection.model_id}`

  async function save() {
    setBusy(true)
    setMessage(null)
    try {
      const target: AgentDefinitionSourceWire = cloning
        ? {
            ...currentSource,
            definition_id: cloneId,
            derived_from_version_id: currentSelected.published_version?.version_id ?? null,
            derived_from_definition_id: currentSelected.definition_id,
            derived_from_source_hash: currentSelected.source_hash,
          }
        : currentSource
      const value = cloning
        ? await client.createAgentDefinition(
            target,
            currentSourceRevision,
            commandId('agent_create'),
          )
        : await client.updateAgentDefinition(
            target.definition_id,
            target,
            currentSelected.source_revision ?? currentSourceRevision,
            commandId('agent_update'),
          )
      setMessage('已保存 desired source；尚未改变已发布版本。')
      await onRefresh(value.definition_id)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }

  async function publish() {
    if (cloning) return
    setBusy(true)
    setMessage(null)
    try {
      await client.publishAgentDefinition(
        currentSource.definition_id,
        currentSelected.head?.row_version ?? 0,
        commandId('agent_publish'),
      )
      setMessage('已发布新的不可变 AgentDefinitionVersion。')
      await onRefresh(currentSource.definition_id)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '发布失败')
    } finally {
      setBusy(false)
    }
  }

  function beginCopy() {
    const nextId = `${currentSelected.definition_id}_copy`
    setCopying(true)
    setCloneId(nextId)
    setSource({
      ...structuredClone(currentSource),
      definition_id: nextId,
      name: `${currentSource.name} Copy`,
      derived_from_version_id:
        currentSelected.published_version?.content_hash === currentSelected.source_hash
          ? currentSelected.published_version.version_id
          : null,
      derived_from_definition_id: currentSelected.definition_id,
      derived_from_source_hash: currentSelected.source_hash,
    })
    setBaseSource(currentSource)
  }

  return (
    <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_360px]">
      <section className="min-h-0 overflow-y-auto border-r border-subtle p-5">
        <div className="flex items-center gap-3">
          <h2 className="font-serif text-xl font-semibold">Agent Module Inspector</h2>
          <span className="rounded-[8px] border border-subtle px-2 py-0.5 text-xs text-secondary">
            {cloning ? '内置 · 复制为用户定义' : `用户定义 · v${selected.published_version?.version ?? '未发布'}`}
          </span>
        </div>
        <div className="mt-5 grid grid-cols-2 gap-3">
          <Field label="Definition ID">
            <input
              className="editor-input font-mono"
              disabled={!cloning}
              value={cloning ? cloneId : source.definition_id}
              onChange={(event) => {
                setCloneId(event.target.value)
                setSource({ ...source, definition_id: event.target.value })
              }}
            />
          </Field>
          <Field label="名称">
            <input className="editor-input" value={source.name} onChange={(event) => setSource({ ...source, name: event.target.value })} />
          </Field>
        </div>
        <Field label="描述" className="mt-3">
          <textarea className="editor-input min-h-16 resize-y" value={source.description} onChange={(event) => setSource({ ...source, description: event.target.value })} />
        </Field>
        <Field label="Role Prompt" className="mt-3">
          <textarea className="editor-input min-h-40 resize-y font-mono" value={source.role_prompt} onChange={(event) => setSource({ ...source, role_prompt: event.target.value })} />
        </Field>
        <div className="mt-3 grid grid-cols-3 gap-3">
          <Field label="Provider / Model">
            <select className="editor-input" value={modelValue} onChange={(event) => {
              const [provider_id, model_id] = event.target.value.split('/')
              setSource({ ...source, model_selection: event.target.value === 'invoking_active' ? 'invoking_active' : { provider_id, model_id } })
            }}>
              <option value="invoking_active">继承当前模型</option>
              {providers.flatMap((provider) => provider.models.map((model) => <option key={`${provider.provider_id}/${model.model_id}`} value={`${provider.provider_id}/${model.model_id}`}>{provider.provider_id}/{model.model_id}</option>))}
            </select>
          </Field>
          <Field label="权限上限">
            <select className="editor-input" value={source.access_mode_ceiling} onChange={(event) => setSource({ ...source, access_mode_ceiling: event.target.value as 'read' | 'write' })}>
              <option value="read">只读</option><option value="write">Writer</option>
            </select>
          </Field>
          <Field label="请求上限（可空）">
            <input className="editor-input" type="number" min={1} value={source.max_agent_generation_requests ?? ''} onChange={(event) => setSource({ ...source, max_agent_generation_requests: event.target.value === '' ? null : Number(event.target.value) })} />
          </Field>
        </div>
        <section className="mt-4 rounded-[10px] border border-subtle p-3">
          <h3 className="text-xs font-medium text-secondary">Skills（只读 Catalog 选择）</h3>
          <div className="mt-2 grid grid-cols-2 gap-2">
            {skills.map((skill) => {
              const version = skill.versions.find((item) => item.version_id === skill.binding?.pinned_version_id) ?? skill.versions.at(-1)
              if (version === undefined) return null
              const checked = source.skill_version_ids.includes(version.version_id)
              const available = skill.availability === 'available' && skill.binding?.enabled === true
              return <label key={skill.skill_id} className="flex items-center gap-2 text-xs"><input type="checkbox" disabled={!available} checked={checked} onChange={() => setSource({ ...source, skill_version_ids: checked ? source.skill_version_ids.filter((id) => id !== version.version_id) : [...source.skill_version_ids, version.version_id] })} /><span>{skill.name}</span>{!available && <span className="text-failed">不可用</span>}</label>
            })}
          </div>
        </section>
        <section className="mt-4 rounded-[10px] border border-subtle p-3">
          <h3 className="text-xs font-medium text-secondary">Tool / Capability Policy</h3>
          <div className="mt-2 grid grid-cols-2 gap-2">
            {tools.map((tool) => {
              const current = source.tool_requirements.find((item) => item.name === tool.name)?.requirement ?? 'absent'
              return <label key={tool.name} className="grid grid-cols-[1fr_110px] items-center gap-2 text-xs"><span title={tool.description}>{tool.name}</span><select className="editor-input" value={current} onChange={(event) => setSource({ ...source, tool_requirements: [...source.tool_requirements.filter((item) => item.name !== tool.name), ...(event.target.value === 'absent' ? [] : [{ name: tool.name, requirement: event.target.value as 'required' | 'optional' | 'forbidden' }])] })}><option value="absent">未声明</option><option value="optional">optional</option><option value="required">required</option><option value="forbidden">forbidden</option></select></label>
            })}
          </div>
        </section>
        <div className="mt-4 flex gap-2">
          {!cloning && <button type="button" className="editor-button" disabled={busy} onClick={beginCopy}>复制为新定义</button>}
          <button type="button" className="editor-button" disabled={busy || (cloning && cloneId === '')} onClick={() => void save()}>{cloning ? '创建用户副本' : '保存 Source'}</button>
          {!cloning && <button type="button" className="editor-button border-accent text-accent" disabled={busy || !selected.desired_ahead_of_published} onClick={() => void publish()}>发布 Version</button>}
          {message !== null && <span role="status" className="self-center text-xs text-secondary">{message}</span>}
        </div>
        <p className="mt-4 rounded-[8px] border border-subtle bg-base p-3 text-xs leading-relaxed text-secondary">
          固定安全边界、Credential 原文、路径越界、审计隐藏、reasoning 记录和高风险免审批均不属于此 schema，无法由此表单覆盖。节点合同与输入/输出绑定在 Workflow Inspector 中配置。
        </p>
      </section>
      <aside className="min-h-0 overflow-y-auto p-4">
        <h3 className="text-xs font-medium tracking-wide text-secondary">Definition Diff</h3>
        <p className="mt-1 text-xs text-secondary">{source.derived_from_version_id !== null ? `parent ${source.derived_from_version_id}` : '相对当前已发布版本'}</p>
        {diff.length === 0 ? <p className="mt-4 text-sm text-secondary">无差异。</p> : <ul className="mt-3 flex flex-col gap-2">{diff.map((line) => <li key={line.path} className="rounded-[8px] border border-subtle p-2 font-mono text-[11px]"><div className="text-secondary">{line.path}</div><div className="mt-1 text-failed">− {line.before}</div><div className="text-completed">+ {line.after}</div></li>)}</ul>}
      </aside>
    </div>
  )
}

function Field({ label, children, className = '' }: { label: string; children: React.ReactNode; className?: string }) {
  return <label className={`flex flex-col gap-1 text-xs text-secondary ${className}`}><span>{label}</span>{children}</label>
}
