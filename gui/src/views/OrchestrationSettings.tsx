import { useEffect, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { OrchestrationPoliciesWire, OrchestrationPolicyWire, PlanningTaskType } from '../api/types'
import { commandId } from './lib/editor'

export function defaultOrchestrationPolicy(scope: 'global' | 'workspace', matcher: PlanningTaskType | '*'): OrchestrationPolicyWire {
  return {
    policy_id: matcher === '*' ? 'default' : matcher, scope, task_matcher: matcher,
    preferred_template: null, excluded_templates: [], required_roles: [], excluded_roles: [],
    model_preferences_by_role: {}, budget_limits: null, review_requirement: 'adaptive',
    multi_agent: true, parallelism_limit: 1, auto_run_mode: 'approval_only',
    auto_replan_mode: 'approval_only', source: 'user', evidence: [], status: 'active', revision: 0,
  }
}

export function OrchestrationEligibility({ eligibility }: Pick<OrchestrationPoliciesWire, 'eligibility'>) {
  const types = (key: 'promoted' | 'auto_run_eligible' | 'auto_replan_eligible') => eligibility.filter(row => row[key]).map(row => row.task_type).join('、') || '无'
  return <div className="mt-2 space-y-1" role="status">
    <p>已推广任务类型：{types('promoted')}</p>
    <p>当前已保存策略允许自动运行：{types('auto_run_eligible')}</p>
    <p>当前已保存策略允许低风险自动重规划：{types('auto_replan_eligible')}</p>
    <p>自动运行需要收益证据与显式授权同时满足。类型专属自动重规划也需要收益证据；“所有任务”的显式低风险策略无需类型推广。</p>
  </div>
}

export function OrchestrationSettings({ client }: { client: ApiClient }) {
  const [data, setData] = useState<OrchestrationPoliciesWire | null>(null)
  const [scope, setScope] = useState<'global' | 'workspace'>('workspace')
  const [matcher, setMatcher] = useState<PlanningTaskType | '*'>('*')
  const [policy, setPolicy] = useState(defaultOrchestrationPolicy('workspace', '*'))
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  async function refresh() {
    try { setData(await client.orchestrationPolicies()) }
    catch (error) { setMessage(error instanceof Error ? error.message : '策略加载失败') }
  }
  useEffect(() => { void refresh() }, [])
  useEffect(() => {
    const existing = data?.[scope].policies.find((value) => value.task_matcher === matcher && value.status === 'active')
    setPolicy(existing ?? defaultOrchestrationPolicy(scope, matcher))
  }, [data, scope, matcher])

  async function save() {
    if (!data) return
    setBusy(true)
    try {
      const next = await client.putOrchestrationPolicy({ ...policy, source: 'user' }, data[scope].revision, commandId('policy'))
      setData(next)
      setMessage('策略已保存。自动运行仍需对应任务类型的对照收益证据。')
    } catch (error) { setMessage(`${error instanceof Error ? error.message : '策略保存失败'}；版本冲突时请重新加载。`) }
    finally { setBusy(false) }
  }

  return <details className="mt-5 border-t border-subtle pt-3 text-xs text-secondary">
    <summary className="cursor-pointer">编排策略与自动运行设置</summary>
    <p className="mt-2 leading-relaxed">工作空间策略完整覆盖匹配的全局策略。影响后续规划与重规划提案；现有 Draft 仍可手工编辑和运行。</p>
    <div className="mt-3 grid grid-cols-2 gap-3">
      <label>策略范围<select className="editor-input mt-1" value={scope} onChange={(e) => setScope(e.target.value as typeof scope)}><option value="workspace">当前工作空间</option><option value="global">全局</option></select></label>
      <label>任务类型<select className="editor-input mt-1" value={matcher} onChange={(e) => setMatcher(e.target.value as typeof matcher)}><option value="*">所有任务</option>{(['implementation', 'refactor', 'research', 'explanation', 'diagnosis', 'general'] as const).map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
      <label>多 Agent<select className="editor-input mt-1" value={String(policy.multi_agent)} onChange={(e) => setPolicy({ ...policy, multi_agent: e.target.value === 'true' })}><option value="true">按任务特征建议</option><option value="false">优先 Direct</option></select></label>
      <label>审查要求<select className="editor-input mt-1" value={policy.review_requirement} onChange={(e) => setPolicy({ ...policy, review_requirement: e.target.value as OrchestrationPolicyWire['review_requirement'] })}><option value="adaptive">按风险与价值</option><option value="required">需要 Reviewer</option><option value="skip">跳过 Reviewer</option></select></label>
      <label className="col-span-2">自动运行偏好<select className="editor-input mt-1" value={policy.auto_run_mode} onChange={(e) => setPolicy({ ...policy, auto_run_mode: e.target.value as OrchestrationPolicyWire['auto_run_mode'] })}><option value="approval_only">每次确认</option><option value="allow_promoted">对应任务类型有收益证据后允许自动运行</option></select></label>
      <label className="col-span-2">重规划策略<select className="editor-input mt-1" value={policy.auto_replan_mode} onChange={(e) => setPolicy({ ...policy, auto_replan_mode: e.target.value as OrchestrationPolicyWire['auto_replan_mode'] })}><option value="approval_only">每个提案均需批准</option><option value="allow_low_risk">允许低风险提案自动应用</option></select></label>
    </div>
    <p className="mt-2">自动应用仍需完整暂停，且不能扩大权限或放宽显式约束。</p>
    {data && <OrchestrationEligibility eligibility={data.eligibility} />}
    <div className="mt-3 flex gap-2"><button type="button" className="editor-button" disabled={busy || !data} onClick={() => void save()}>保存策略</button><button type="button" className="editor-button" disabled={busy} onClick={() => void refresh()}>重新加载策略</button></div>
    {message && <p role="status" className="mt-2">{message}</p>}
  </details>
}
