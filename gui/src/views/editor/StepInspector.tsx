import { useMemo } from 'react'
import type {
  AgentDefinitionViewWire,
  AgentNodeSourceWire,
  ArtifactContractCatalogWire,
  WorkflowDefinitionSourceWire,
  WorkflowDraftDiagnosticWire,
} from '../../api/types'
import { nodeIsLocked } from '../lib/editor'
import { buildStepDisplayModels, UNNAMED_AGENT_TITLE } from './display'
import { presentDiagnostic, uniqueDiagnostics } from './diagnostics'
import { NullableNumberField } from './RunSettings'

function Field({ label, children, className = '' }: { label: string; children: React.ReactNode; className?: string }) {
  return <label className={`workflow-field ${className}`}><span>{label}</span>{children}</label>
}

function agentAvailabilityLabel(agent: AgentDefinitionViewWire | undefined): string {
  if (agent === undefined) return '找不到该助手定义；原引用已保留。'
  if (agent.revoked) return '该助手版本已撤销；原精确引用已保留。'
  if (agent.published_version === null) return '该助手尚未发布可用版本；原精确引用已保留。'
  return '使用当前目录中的已发布助手版本。'
}

function normalizedList(value: string): string[] {
  return value.split(/\r?\n|\r/u).map(item => item.trim()).filter(Boolean)
}

function listText(values: string[]): string {
  return values.join('\n')
}

function customCount(node: AgentNodeSourceWire): number {
  return [
    node.max_agent_generation_requests,
    node.conversation_scope === 'isolated' ? null : node.conversation_scope,
    node.tool_requirements,
    node.task_contract.scope.length > 0 ? node.task_contract.scope : null,
    node.task_contract.constraints.length > 0 ? node.task_contract.constraints : null,
  ].filter(value => value !== null).length
}

export function StepInspector({
  node,
  source,
  agents,
  contracts,
  diagnostics,
  disabled,
  nodeStatuses,
  onUpdate,
  onOpenConnection,
  onDelete,
}: {
  node: AgentNodeSourceWire
  source: WorkflowDefinitionSourceWire
  agents: AgentDefinitionViewWire[]
  contracts: ArtifactContractCatalogWire[]
  diagnostics: WorkflowDraftDiagnosticWire[]
  disabled: boolean
  nodeStatuses: Record<string, import('../../api/types').WorkflowStatus>
  onUpdate: (node: AgentNodeSourceWire) => void
  onOpenConnection: (fromNodeId: string | null, toNodeId: string) => void
  onDelete: () => void
}) {
  const display = buildStepDisplayModels(source, agents).find(item => item.nodeId === node.node_id)
  const selectedAgent = agents.find(item => item.definition_id === node.agent_definition_ref.definition_id)
  const definition = selectedAgent?.published_version?.source ?? selectedAgent?.source
  const locked = disabled || nodeIsLocked(nodeStatuses[node.node_id])
  const contractKinds = useMemo(
    () => [...new Set([
      ...contracts.map(item => item.kind).filter(kind => kind !== 'TaskContract' && kind !== 'ChangeCapture'),
      ...node.output_contracts.map(item => item.kind),
      ...node.input_bindings.filter(item => item.source === 'node_output').map(item => item.accepts.kind),
    ])].sort(),
    [contracts, node.input_bindings, node.output_contracts],
  )
  const overlays = new Map((node.tool_requirements ?? []).map(item => [item.name, item.requirement]))
  const currentAgentLabel = display?.agentName ?? definition?.name ?? UNNAMED_AGENT_TITLE
  const exactRef = node.agent_definition_ref
  const localDiagnostics = uniqueDiagnostics(diagnostics)

  function update(patch: Partial<AgentNodeSourceWire>) {
    if (locked) return
    onUpdate({ ...node, ...patch })
  }

  function updateTask(patch: Partial<AgentNodeSourceWire['task_contract']>) {
    update({ task_contract: { ...node.task_contract, ...patch } })
  }

  function updateOutput(slot: string, patch: Partial<AgentNodeSourceWire['output_contracts'][number]>) {
    update({ output_contracts: node.output_contracts.map(output => output.slot === slot ? { ...output, ...patch } : output) })
  }

  return (
    <section id="workflow-step-inspector" className="workflow-step-inspector" aria-label="步骤详情">
      <div className="workflow-inspector-heading">
        <div>
          <span className="workflow-kicker">步骤 {display?.ordinal ?? '—'}</span>
          <h2>{display?.cardTitle ?? node.node_id}</h2>
        </div>
        <span className="workflow-inspector-state">{locked ? '只读' : '草稿'}</span>
      </div>

      <section className="workflow-inspector-section">
        <h3>这一步要做什么</h3>
        <Field label="任务目标">
          <textarea
            aria-label="任务目标"
            className="editor-input workflow-objective-input"
            value={node.task_contract.objective}
            disabled={locked}
            maxLength={4096}
            onChange={event => updateTask({ objective: event.target.value })}
          />
        </Field>

      </section>

      <section className="workflow-inspector-section">
        <h3>由谁完成</h3>
        <Field label="助手定义">
          <select
            aria-label="助手定义"
            className="editor-input"
            value={node.agent_definition_ref.definition_id}
            disabled={locked}
            onChange={event => {
              const next = agents.find(item => item.definition_id === event.target.value)?.published_version
              if (next === null || next === undefined) return
              update({
                agent_definition_ref: {
                  definition_id: next.source.definition_id,
                  version_id: next.version_id,
                  content_hash: next.content_hash,
                },
                access_mode: next.source.access_mode_ceiling === 'read' ? 'read' : node.access_mode,
              })
            }}
          >
            {agents.length === 0 && <option value={node.agent_definition_ref.definition_id}>{currentAgentLabel}</option>}
            {agents.map(agent => <option key={agent.definition_id} value={agent.definition_id}>{agent.source?.name ?? agent.published_version?.source.name ?? UNNAMED_AGENT_TITLE}{agent.revoked ? ' · 已撤销' : agent.published_version === null ? ' · 未发布' : ''}</option>)}
            {!agents.some(agent => agent.definition_id === node.agent_definition_ref.definition_id) && <option value={node.agent_definition_ref.definition_id}>{UNNAMED_AGENT_TITLE} · 当前引用</option>}
          </select>
        </Field>
        <p className={`workflow-field-help ${display?.agentAvailability === 'available' ? '' : 'workflow-field-warning'}`}>{agentAvailabilityLabel(selectedAgent)}</p>
        <details className="workflow-technical-details">
          <summary>精确版本与技术标识</summary>
          <dl>
            <dt>node_id</dt><dd>{node.node_id}</dd>
            <dt>version_id</dt><dd>{exactRef.version_id}</dd>
            <dt>content_hash</dt><dd>{exactRef.content_hash}</dd>
          </dl>
        </details>
      </section>

      <section className="workflow-inspector-section">
        <h3>使用的信息</h3>
        {node.input_bindings.length === 0 && <p className="workflow-field-help">未配置输入</p>}
        <ul className="workflow-binding-list">
          {node.input_bindings.map(binding => <li key={binding.input_name}>
            <div>
              <strong>{binding.source === 'workflow_input' ? '用户原始任务' : `${binding.node_output.node_id}.${binding.node_output.output_slot}`}</strong>
              <span>{binding.input_name} · {binding.accepts.kind} v{binding.accepts.version}</span>
            </div>
            {!locked && <div className="workflow-binding-actions"><button type="button" className="workflow-link-button" onClick={() => update({ input_bindings: node.input_bindings.filter(item => item.input_name !== binding.input_name) })}>移除</button>{binding.source === 'node_output' && <button type="button" className="workflow-link-button" onClick={() => onOpenConnection(binding.node_output.node_id, node.node_id)}>修复连接</button>}</div>}
          </li>)}
        </ul>

        {!locked && <button type="button" className="editor-button" onClick={() => onOpenConnection(null, node.node_id)}>添加前序步骤</button>}
      </section>

      <section className="workflow-inspector-section">
        <h3>产生的结果</h3>
        <div className="workflow-output-list">
          {node.output_contracts.map(output => <div key={output.slot} className="workflow-output-row">
            <Field label="结果名称">
              <input aria-label={`结果名称 ${output.slot}`} className="editor-input" value={output.slot} disabled={locked} onChange={event => updateOutput(output.slot, { slot: event.target.value })} />
            </Field>
            <Field label="结果类型">
              <select aria-label={`结果类型 ${output.slot}`} className="editor-input" value={output.kind} disabled={locked} onChange={event => updateOutput(output.slot, { kind: event.target.value })}>
                {contractKinds.map(kind => <option key={kind} value={kind}>{kind}</option>)}
              </select>
            </Field>
            <Field label="类型版本">
              <input aria-label={`类型版本 ${output.slot}`} className="editor-input" type="number" min="1" step="1" value={output.version} disabled={locked} onChange={event => { const value = Number(event.target.value); if (Number.isSafeInteger(value) && value > 0) updateOutput(output.slot, { version: value }) }} />
            </Field>
            <label className="workflow-checkbox-field"><input type="checkbox" checked={output.required_for_node_completion} disabled={locked} onChange={event => updateOutput(output.slot, { required_for_node_completion: event.target.checked })} />完成所需结果</label>
          </div>)}
        </div>

      </section>

      <section className="workflow-inspector-section">
        <h3>文件操作权限</h3>
        <Field label="此步骤可写入">
          <select aria-label="文件操作权限" className="editor-input" value={node.access_mode} disabled={locked} onChange={event => update({ access_mode: event.target.value as 'read' | 'write' })}>
            <option value="read">只读</option>
            <option value="write" disabled={definition?.access_mode_ceiling === 'read'}>读写（仍受助手上限约束）</option>
          </select>
        </Field>
        {node.access_mode === 'write' && <details><summary>写入权限</summary><p>可写入操作仍受权限与审批限制。</p></details>}
        {definition?.tool_requirements && definition.tool_requirements.length > 0 && <div className="workflow-tool-policy-list">
          <h4>工具策略（来自助手定义）</h4>
          {definition.tool_requirements.map(tool => <label key={tool.name} className="workflow-tool-policy-row">
            <span>{tool.name}<small>默认 {tool.requirement}</small></span>
            <select aria-label={`工具策略 ${tool.name}`} className="editor-input" value={overlays.get(tool.name) ?? 'inherit'} disabled={locked} onChange={event => {
              const next = (node.tool_requirements ?? []).filter(item => item.name !== tool.name)
              if (event.target.value !== 'inherit') next.push({ name: tool.name, requirement: event.target.value as 'required' | 'optional' | 'forbidden' })
              update({ tool_requirements: next.length === 0 ? null : next })
            }}>
              <option value="inherit">继承</option><option value="required">必须</option><option value="optional">可选</option><option value="forbidden">禁止</option>
            </select>
          </label>)}
        </div>}
      </section>

      <details className="workflow-inspector-section workflow-advanced-section" open={customCount(node) > 0}>
        <summary><span>高级设置</span><span className="workflow-summary-hint">{customCount(node) === 0 ? '使用默认设置' : `已自定义 ${customCount(node)} 项`}</span></summary>
        <div className="workflow-advanced-grid">
          <Field label="会话范围">
            <select aria-label="会话范围" className="editor-input" value={node.conversation_scope} disabled={locked} onChange={event => update({ conversation_scope: event.target.value as AgentNodeSourceWire['conversation_scope'] })}>
              <option value="isolated">独立对话</option>
              <option value="invoking_session">发起任务的对话（仅单节点）</option>
            </select>
          </Field>
          <NullableNumberField
            label="本步骤模型调用上限"
            value={node.max_agent_generation_requests}
            kind="positive-integer"
            help="留空表示继承流程默认，仍受助手上限约束。"
            disabled={locked}
            onChange={value => update({ max_agent_generation_requests: value })}
          />
          <Field label="任务范围（每行一项）">
            <textarea aria-label="任务范围" className="editor-input" value={listText(node.task_contract.scope)} disabled={locked} onChange={event => updateTask({ scope: normalizedList(event.target.value) })} />
          </Field>
          <Field label="显式限制（每行一项）">
            <textarea aria-label="显式限制" className="editor-input" value={listText(node.task_contract.constraints)} disabled={locked} onChange={event => updateTask({ constraints: normalizedList(event.target.value) })} />
          </Field>
        </div>
        <details className="workflow-technical-details">
          <summary>保留的技术字段</summary>

          <pre>{JSON.stringify(node.task_contract.source_refs)}</pre>
        </details>
      </details>

      {localDiagnostics.length > 0 && <section className="workflow-inspector-section workflow-local-diagnostics" aria-label="步骤问题">
        <h3>这一步的问题</h3>
        <ul>{localDiagnostics.map(item => {
          const view = presentDiagnostic(item)
          return <li key={`${item.code}:${item.edge_id ?? ''}`}>
            <strong>{view.severityLabel} · {view.title}</strong>
            <p className="workflow-field-help">{view.guidance}</p>
            <details className="workflow-technical-details"><summary>Core 说明</summary><p>{view.detail}</p></details>
          </li>
        })}</ul>
      </section>}
      <button type="button" className="editor-button workflow-danger-button" disabled={locked || source.nodes.length <= 1} onClick={onDelete}>删除步骤</button>
    </section>
  )
}
