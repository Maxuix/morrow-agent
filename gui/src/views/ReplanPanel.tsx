import { useEffect, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { ReplanViewWire, WorkflowRunWire } from '../api/types'
import { commandId } from './lib/editor'

const reasons: Record<string, string> = {
  approval_only: '当前策略要求批准', approval_required: '涉及权限或约束变更，需要批准',
  low_risk_policy: '根据低风险自动重规划策略应用', user_approved: '用户已批准',
  user_rejected: '用户已拒绝，运行保持暂停，可继续原计划', stale_base: '基线已变化，请重新生成提案或手工编辑',
  compile_failed: '编译未通过，请手工修正', future_only_validation_failed: '提案试图修改已执行节点',
}

const statuses = { pending: '待批准提案', applied: '已应用', rejected: '已拒绝', conflict: '基线冲突', invalid: '提案无效' }
const risks: Record<string, string> = {
  cap_or_deadline_relaxed: '提高或移除显式请求上限 / 截止时间',
  role_added: '新增执行角色', role_replaced: '替换执行角色',
  node_removed: '删除节点', review_or_test_gate_removed: '移除审查或测试门禁',
  required_outputs_retargeted: '改变必需输出', control_edge_removed: '删除执行顺序约束',
  permission_widened: '扩大工具或写入权限', provider_model_boundary_changed: '改变 Provider / Model 数据边界',
  conversation_scope_changed: '改变会话范围', output_contract_relaxed: '改变输出合同',
  report_dependency_removed: '移除测试或报告依赖', writer_order_changed: '改变写入节点顺序',
  input_dependency_removed: '移除或替换节点输入证据',
  explicit_constraint_removed: '移除显式任务约束', task_scope_changed: '改变任务范围',
  unclassified_change: '无法确定风险的变化',
}

export function ReplanReview({ view, paused, stale = false, busy, decide }: {
  view: ReplanViewWire; paused: boolean; stale?: boolean; busy: boolean; decide: (approved: boolean) => void
}) {
  const p = view.proposal
  return <article className="rounded-[10px] border border-subtle p-3 text-xs">
    <h4 className="font-medium">{p.auto_applied ? '已自动应用' : stale && p.status === 'pending' ? '基线已变化' : statuses[p.status]} · {p.risk_level === 'low' ? '低风险' : '需升级审批'}</h4>
    <p className="text-secondary">{stale && p.status === 'pending' ? '此提案基于旧运行状态；请拒绝后重新生成，或手工编辑当前运行。' : reasons[p.disposition_reason] ?? p.disposition_reason}</p>
    <p className="font-mono text-secondary">{p.proposal_id} · 策略 {p.policy_id} v{p.policy_revision}</p>
    {p.risk_reasons.length > 0 && <ul className="my-2 list-inside list-disc">{p.risk_reasons.map(reason => <li key={reason} title={reason}>{risks[reason] ?? reason}</li>)}</ul>}
    <p>修改节点：{view.diff.changed_node_ids.join(', ') || '无'}；新增：{view.diff.added_node_ids.join(', ') || '无'}；删除：{view.diff.removed_node_ids.join(', ') || '无'}</p>
    <details className="my-2"><summary className="cursor-pointer">查看完整差异（应用前 / 提案）</summary>
      <div className="grid gap-2 lg:grid-cols-2"><pre className="max-h-72 overflow-auto whitespace-pre-wrap">{JSON.stringify(view.before, null, 2)}</pre><pre className="max-h-72 overflow-auto whitespace-pre-wrap">{JSON.stringify(view.after, null, 2)}</pre></div>
    </details>
    {p.child_run_id && <p className="font-mono">后续运行：{p.child_run_id}</p>}
    {p.decided_at && <p>{p.decided_by} · {p.decided_at}</p>}
    {p.status === 'pending' && <div className="flex items-center gap-3">
      <button disabled={busy || !paused || stale} onClick={() => decide(true)} className="rounded border border-subtle px-3 py-1 disabled:opacity-40">批准并应用</button>
      <button disabled={busy} onClick={() => decide(false)} className="rounded border border-subtle px-3 py-1 disabled:opacity-40">拒绝</button>
      {!paused && !stale && <span>须完成暂停与恢复后才能应用</span>}
    </div>}
  </article>
}

export function ReplanPanel({ client, run }: { client: ApiClient; run: WorkflowRunWire }) {
  const [views, setViews] = useState<ReplanViewWire[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let disposed = false
    let loading = false
    const refresh = async () => {
      if (loading) return
      loading = true
      try { const result = await client.listReplans(run.workflow_run_id); if (!disposed) { setViews(result); setError(null) } }
      catch { if (!disposed) setError('重规划记录读取失败，请刷新重试') }
      finally { loading = false }
    }
    setViews([])
    void refresh()
    const timer = setInterval(() => { void refresh() }, 3000)
    return () => { disposed = true; clearInterval(timer) }
  }, [client, run.workflow_run_id, run.row_version])
  const decide = async (view: ReplanViewWire, approved: boolean) => {
    setBusy(true); setError(null)
    try {
      const result = await client.decideReplan(view.proposal.proposal_id, approved, view.proposal.row_version, commandId('replan'))
      setViews(current => current.map(item => item.proposal.proposal_id === result.proposal.proposal_id ? result : item))
    } catch { setError('操作未完成。请刷新记录；陈旧基线或未解决的恢复状态不能应用。') }
    finally { setBusy(false) }
  }
  return <section className="flex flex-col gap-2" aria-label="全局重规划">
    <h3 className="text-sm font-medium">全局重规划</h3>
    {error && <p role="alert" className="text-xs text-failed">{error}</p>}
    {!views.length && !error && <p className="text-xs text-secondary">暂无提案。节点完成时提交的信号会在此显示。</p>}
    {views.map(view => <ReplanReview key={view.proposal.proposal_id} view={view} paused={run.status === 'paused'} stale={view.proposal.patch.expected_parent_row_version !== run.row_version || view.proposal.patch.base_workflow_revision_id !== run.workflow_revision_id} busy={busy} decide={approved => { void decide(view, approved) }} />)}
  </section>
}
