import { useState } from 'react'
import type { ApiClient } from '../api/client'
import type { GraphPlanningRequestWire, PlannerMetadataWire, TaskGraphDraftWire, WorkflowDraftViewWire } from '../api/types'
import { commandId, draftId } from './lib/editor'
import { OrchestrationSettings } from './OrchestrationSettings'

function lines(value: string): string[] {
  return [...new Set(value.split(/[\n,，]/).map((line) => line.trim()).filter(Boolean))]
}

export function optionalRequestCap(value: string): number | null {
  if (value.trim() === '') return null
  const cap = Number(value)
  if (!Number.isSafeInteger(cap) || cap < 1) throw new Error('请求上限必须是正整数，或留空。')
  return cap
}

export function plannerApprovalText(reason: 'approval_only' | 'paired_evidence_missing' | 'paired_benefit'): string {
  if (reason === 'paired_benefit') return '生成时已满足配对收益与用户策略要求；可在编辑器中冻结和运行，策略变更后需重新评估。'
  return reason === 'paired_evidence_missing'
    ? '尚无此任务类型的 Direct/Multi 对照收益证据，本次仍需手工确认。可以编辑、冻结并手工运行。'
    : '请检查并编辑 Draft，确认后冻结、手工运行。'
}

export function GraphPlanner({ client, definitionId, name, onDraft }: {
  client: ApiClient; definitionId: string; name: string; onDraft: (draft: WorkflowDraftViewWire) => void
}) {
  const [objective, setObjective] = useState('')
  const [scope, setScope] = useState('')
  const [constraints, setConstraints] = useState('')
  const [roles, setRoles] = useState('')
  const [excluded, setExcluded] = useState('')
  const [cap, setCap] = useState('')
  const [model, setModel] = useState(true)
  const [scout, setScout] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<TaskGraphDraftWire | null>(null)
  const [pending, setPending] = useState<{ key: string; request: GraphPlanningRequestWire; command: string } | null>(null)

  async function generate() {
    setError(null)
    setBusy(true)
    try {
      const limit = optionalRequestCap(cap)
      const facts = {
        workflow_definition_id: definitionId, name,
        task: { objective, scope: lines(scope), constraints: lines(constraints), source_refs: [] },
        requested_roles: lines(roles), excluded_roles: lines(excluded),
        budget: limit === null ? null : {
          max_agent_generation_requests: limit, default_node_max_agent_generation_requests: null,
          admission_timeout_seconds: null, max_concurrency: 1,
        },
        use_model: model, scout,
      }
      const key = JSON.stringify(facts)
      // Keep identity after a lost response; Core reopens the saved Draft on retry.
      const next = pending?.key === key ? pending : {
        key, request: { ...facts, draft_id: draftId() }, command: commandId('plan'),
      }
      setPending(next)
      const value = await client.planWorkflow(next.request, next.command)
      setResult(value)
      if (value.workflow_draft !== null) onDraft(value.workflow_draft)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Draft 生成失败')
    } finally { setBusy(false) }
  }

  return <section className="mt-6 border-t border-subtle pt-5" aria-label="任务规划">
    <h3 className="font-serif text-xl">根据任务生成 Draft</h3>
    <p className="mt-1 text-xs leading-relaxed text-secondary">简单任务优先 Direct。复杂任务根据范围、风险与审查价值组合普通节点，之后可继续编辑。</p>
    <label className="mt-3 block text-xs text-secondary">任务<textarea className="editor-input mt-1 min-h-24" value={objective} onChange={(e) => setObjective(e.target.value)} placeholder="例如：重构 API 与持久化模块，并独立检查恢复语义" maxLength={4096} /></label>
    <details className="mt-3 text-xs text-secondary"><summary className="cursor-pointer">范围、角色与显式限制</summary>
      <div className="mt-2 grid grid-cols-2 gap-3">
        <label>任务范围（每行一项）<textarea className="editor-input mt-1" value={scope} onChange={(e) => setScope(e.target.value)} /></label>
        <label>约束（每行一项）<textarea className="editor-input mt-1" value={constraints} onChange={(e) => setConstraints(e.target.value)} /></label>
        <label>需要的角色<input className="editor-input mt-1" value={roles} onChange={(e) => setRoles(e.target.value)} placeholder="reviewer, planner" /></label>
        <label>排除的角色<input className="editor-input mt-1" value={excluded} onChange={(e) => setExcluded(e.target.value)} placeholder="reviewer" /></label>
        <label>Workflow 请求上限<input className="editor-input mt-1" type="number" min="1" step="1" value={cap} onChange={(e) => setCap(e.target.value)} placeholder="留空：无上限" /></label>
      </div>
    </details>
    <div className="mt-3 flex flex-col gap-2 text-xs text-secondary">
      <label><input type="checkbox" checked={model} onChange={(e) => setModel(e.target.checked)} /> 使用当前模型分类（最多一次请求）</label>
      <label><input type="checkbox" checked={scout} onChange={(e) => setScout(e.target.checked)} /> 只读 Scout（一次有界项目目录检查）</label>
    </div>
    <button type="button" className="editor-button mt-4 border-accent text-accent" disabled={busy || !objective.trim() || !definitionId || !name} onClick={() => void generate()}>{busy ? '正在生成 Draft…' : '生成任务 Draft'}</button>
    {error && <p role="alert" className="mt-3 text-xs text-failed">{error}</p>}
    {result?.workflow_draft === null && <div role="status" className="mt-3 rounded-[8px] border border-blocked p-3 text-xs"><p>需要补充信息或调整策略后才能生成。</p><ul className="mt-2 space-y-1">{result.diagnostics.map((item) => <li key={item}>{item}</li>)}</ul></div>}
    <OrchestrationSettings client={client} />
  </section>
}

export function PlannerExplanation({ metadata, edited }: { metadata: PlannerMetadataWire; edited: boolean }) {
  const explanation = metadata.explanation
  const cap = explanation.budget.max_agent_generation_requests
  return <details open className="shrink-0 border-b border-subtle px-4 py-2 text-xs" aria-label="Draft 规划说明">
    <summary className="cursor-pointer font-medium">规划说明 · {explanation.mode === 'direct' ? 'Direct' : '多节点工作流'}{edited ? '（生成时说明，图已编辑）' : ''}</summary>
    <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-secondary">
      <p>任务：{metadata.features.task_type} · 范围 {metadata.features.number_of_areas} · 风险 {metadata.features.risk_level}</p>
      <p>起点：{explanation.starting_point} · 策略 {metadata.policy_scope}/{metadata.policy_id} v{metadata.policy_revision}</p>
      <p>节点 {explanation.node_count} · 请求上限 {cap === null ? '无上限' : cap} · 串行执行</p>
      <p>写入节点：{explanation.writing_nodes.join(', ') || '无'}</p>
      <p>默认节点请求上限：{explanation.budget.default_node_max_agent_generation_requests ?? '无上限'}</p>
      <p>准入超时：{explanation.budget.admission_timeout_seconds === null ? '无上限' : `${explanation.budget.admission_timeout_seconds} 秒`}</p>
      <p className="col-span-2">模型：{explanation.models.map((model) => `${model.provider_id}/${model.model_id}`).join(', ')}</p>
    </div>
    <ul className="mt-2 max-h-24 space-y-1 overflow-y-auto text-secondary">{explanation.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
    {metadata.diagnostics.length > 0 && <p className="mt-2 text-blocked">{metadata.diagnostics.join('；')}</p>}
    <p className="mt-2 text-accent">{plannerApprovalText(explanation.auto_run_reason)}</p>
  </details>
}
