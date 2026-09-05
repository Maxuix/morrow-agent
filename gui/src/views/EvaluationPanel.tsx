import { useRef, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { EvaluationPage, EvaluationRun } from '../api/evaluation'
import { feedbackLabels } from '../api/evaluation'
import type { Mutate } from './ContextDrawer'
import { buttonClass, Card, Facts, fieldClass, Pager, useManagement } from './ContextDrawer'
import { WorkflowPolicyReview } from './WorkflowPolicyReview'

export function ratio(value: number | null) { return value === null ? '暂无评价' : `${Math.round(value * 100)}%` }
export function EvaluationMetrics({ data }: { data: EvaluationPage }) {
  const m = data.metrics
  return <>
    <div className="grid gap-4 md:grid-cols-3">
      <Card title="工作流编辑频率"><p className="text-2xl">{ratio(m.edit_frequency)}</p><p className="text-xs text-secondary">{m.edited_task_count} / {m.root_task_count} 个根任务被用户修改 · 草稿编辑 {m.draft_edit_count} 次 · 运行编辑 {m.run_edit_count} 次</p></Card>
      <Card title="Reviewer 价值"><p className="text-2xl">{ratio(m.reviewer_value)}</p><p className="text-xs text-secondary">{m.reviewer_useful_tasks} / {m.reviewer_rated_tasks} 个已评价任务认为有价值；每个任务采用最近一次评价。</p></Card>
      <Card title="对照口径"><p className="text-sm">质量由用户按 0–4 分评价，请求数来自实际运行。估算单独标记，费用和 token 缺失时保留“不可用”。</p></Card>
    </div>
    <Card title="任务类型推广状态"><p className="text-xs text-secondary">至少两组独立配对且全部有收益：质量提高，或质量相同且请求减少。估算不计入；无收益仍可编辑和手工运行。Replan 继续检查权限、约束和风险。</p>
      <div className="overflow-auto"><table className="w-full text-left text-sm"><thead><tr><th>任务类型</th><th>有收益 / 配对</th><th>收益门槛</th><th>自动运行资格</th><th>类型级低风险 Replan</th></tr></thead><tbody>{data.promotion.map(p => <tr key={p.task_type} className="border-t border-subtle"><td className="py-2">{p.task_type}</td><td>{p.beneficial_count} / {p.paired_count}</td><td>{p.promoted ? '满足' : p.reason === 'no_benefit' ? '发现无收益' : '证据不足'}</td><td>{p.auto_run_eligible ? '已授权' : '待证据或授权'}</td><td>{p.task_class_replan_eligible ? '已授权' : '需批准'}</td></tr>)}</tbody></table></div>
    </Card>
  </>
}

export function RunEvaluation({ run, mutate }: { run: EvaluationRun; mutate: Mutate }) {
  const [kind, setKind] = useState('too_complex')
  const [template, setTemplate] = useState('direct')
  const settled = ['completed', 'failed', 'cancelled'].includes(run.status)
  const reviewer = run.reviewer_findings.length > 0
  const kinds = ['too_complex', 'missing_exploration', ...(reviewer ? ['reviewer_useful', 'reviewer_not_useful'] : []), 'model_expensive', 'prefer_template', 'avoid_template']
  return <Card title={`${run.mode === 'direct' ? 'Direct' : 'Multi'} · ${run.task_type} · ${run.status}`}>
    <p className="break-all font-mono text-xs text-secondary">{run.workflow_run_id}</p>
    <p className="text-sm">{run.node_count} 个节点 · 本次 {run.requests} 次请求 · lineage 共 {run.lineage_requests} 次 · {run.user_modified ? '用户修改过工作流' : '无用户编辑记录'} · usage {run.usage_availability === 'available' ? '可用' : '不可用'}</p>
    <p>{run.outcome?.summary ?? '尚无绑定此运行的 TaskOutcome'}</p>
    <p className="text-xs text-secondary">未用于最终输出的已完成只读节点：{run.dead_node_ids.join(', ') || '无'}。此指标不判断写入节点的价值。</p>
    <details><summary className="text-sm">验证结果与节点耗用</summary><Facts value={{ verification: run.verification_results, nodes: run.nodes }} /></details>
    {run.reviewer_findings.map(r => <div key={`${r.node_id}:${r.artifact_id}`} className="text-sm"><p className="font-medium">Reviewer · {r.verdict ?? '文本结论'}</p>{r.findings.length ? r.findings.map((f, i) => <p key={i} className="whitespace-pre-wrap break-words">{f}</p>) : <p>结论不可用</p>}</div>)}
    {settled && <div className="flex flex-wrap items-end gap-2">
      <label className="grow text-sm">运行反馈<select className={fieldClass} value={kind} onChange={e => setKind(e.target.value)}>{kinds.map(k => <option key={k} value={k}>{feedbackLabels[k]}</option>)}</select></label>
      {kind.endsWith('_template') && <label className="text-sm">模板<select className={fieldClass} value={template} onChange={e => setTemplate(e.target.value)}><option value="direct">Direct</option><option value="explore_implement_verify">Explore / Implement / Verify</option></select></label>}
      <button className={buttonClass} onClick={() => void mutate('workflow-feedback', { workflow_run_id: run.workflow_run_id, kind, template: kind.endsWith('_template') ? template : null })}>记录反馈</button>
    </div>}
  </Card>
}

function PairForm({ runs, mutate }: { runs: EvaluationRun[]; mutate: Mutate }) {
  const [multi, setMulti] = useState('')
  const [direct, setDirect] = useState('')
  const [mode, setMode] = useState('paired')
  const [directQuality, setDirectQuality] = useState('')
  const [multiQuality, setMultiQuality] = useState('')
  const [estimate, setEstimate] = useState('')
  const selected = runs.find(r => r.workflow_run_id === multi)
  const submit = () => mutate('workflow-evaluation', { multi_run_id: multi,
    direct_run_id: mode === 'paired' ? direct : null, direct_estimated_requests: mode === 'estimate' ? Number(estimate) : null,
    direct_quality: mode === 'paired' ? Number(directQuality) : null, multi_quality: mode === 'paired' ? Number(multiQuality) : null })
  return <Card title="添加 Direct / Multi 对照">
    <p className="text-xs text-secondary">配对需要同一任务输入的独立已完成运行，每次运行只能参与一组配对。0 分表示未达目标，4 分表示完整达成。估算只作参考。</p>
    <div className="grid gap-3 md:grid-cols-2">
      <label className="text-sm">Multi 运行<select className={fieldClass} value={multi} onChange={e => setMulti(e.target.value)}><option value="">选择运行</option>{runs.filter(r => r.mode === 'multi' && r.status === 'completed').map(r => <option key={r.workflow_run_id} value={r.workflow_run_id}>{r.workflow_run_id}</option>)}</select></label>
      <label className="text-sm">对照类型<select className={fieldClass} value={mode} onChange={e => setMode(e.target.value)}><option value="paired">实际配对结果</option><option value="estimate">Direct 请求数估算</option></select></label>
      {mode === 'paired' ? <><label className="text-sm">Direct 运行 ID<input className={fieldClass} list="direct-runs" value={direct} onChange={e => setDirect(e.target.value)} /><datalist id="direct-runs">{runs.filter(r => r.mode === 'direct' && r.status === 'completed' && r.task_type === selected?.task_type).map(r => <option key={r.workflow_run_id} value={r.workflow_run_id} />)}</datalist></label>
        <div className="flex gap-3">{([['Direct 质量', directQuality, setDirectQuality], ['Multi 质量', multiQuality, setMultiQuality]] as const).map(([label, value, change]) => <label className="grow text-sm" key={label}>{label}<select className={fieldClass} value={value} onChange={e => change(e.target.value)}><option value="">请选择</option>{[0, 1, 2, 3, 4].map(n => <option key={n} value={n}>{n}</option>)}</select></label>)}</div></>
        : <label className="text-sm">预计 Direct 请求数<input type="number" min={0} step={1} className={fieldClass} value={estimate} onChange={e => setEstimate(e.target.value)} /></label>}
    </div>
    <button className={buttonClass} disabled={!multi || (mode === 'paired' ? !direct || !directQuality || !multiQuality : !estimate || !Number.isInteger(Number(estimate)) || Number(estimate) < 0)} onClick={() => void submit()}>保存对照</button>
  </Card>
}

export function EvaluationPanel({ client, connected }: { client: ApiClient; connected: boolean }) {
  const [page, setPage] = useState(0)
  const [refresh, setRefresh] = useState(0)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const retry = useRef<{ key: string; id: string } | null>(null)
  const { data, error } = useManagement(client, 'workflow-evaluation', 'workspace', refresh, page)
  const reviews = useManagement(client, 'workflow-policy-candidates', 'workspace', refresh, page)
  const mutate: Mutate = async (kind, body, target) => {
    if (busy || !connected) return false
    const key = JSON.stringify({ kind, body, target })
    if (retry.current?.key !== key) retry.current = { key, id: `cmd_${crypto.randomUUID().replaceAll('-', '')}` }
    setBusy(true); setMessage('')
    try { await client.managementCommand(kind, { ...body, command_id: typeof body.command_id === "string" ? body.command_id : retry.current.id }, target)
      retry.current = null; setRefresh(r => r + 1); setMessage('已保存。反馈仅供审核，接受候选后才修改策略。'); return true
    } catch { setMessage('保存失败。请检查运行、配对条件和修订号；连接失败可重试原操作。'); return false }
    finally { setBusy(false) }
  }
  return <main className="min-h-0 flex-1 overflow-auto p-6" aria-label="反馈与评估"><div className="mx-auto max-w-6xl space-y-5">
    <div className="flex items-center gap-4"><h2 className="font-serif text-2xl">反馈与评估</h2><button className={buttonClass} onClick={() => setRefresh(r => r + 1)}>刷新评估</button></div>
    <p className="text-sm text-secondary">编辑和运行判断形成当前工作空间的学习证据。一次修改不会永久改变路由。</p>
    {(message || error || reviews.error) && <p role="status">{message || error || reviews.error}</p>}
    {data ? <><EvaluationMetrics data={data} /><Pager page={page} next={data.next_cursor || reviews.data?.next_cursor || null} onChange={setPage} />
      <fieldset disabled={busy || !connected} className="space-y-5"><PairForm runs={data.runs} mutate={mutate} />
        {data.runs.map(r => <RunEvaluation key={r.workflow_run_id} run={r} mutate={mutate} />)}
        {!data.runs.length && <p className="text-secondary">尚无运行记录；可先在编辑器冻结并运行工作流。</p>}
        {reviews.data?.items.map(c => <WorkflowPolicyReview key={`${c.candidate_id}:${c.row_version}`} candidate={c} mutate={mutate} />)}
      </fieldset>
      <Card title="对照记录">{data.evaluations.length ? data.evaluations.map(e => <div key={e.evaluation_id} className="border-b border-subtle py-2 text-sm"><p>{e.task_type} · {e.kind === 'paired' ? '实际配对' : '用户估算，不计入推广'} · Direct {e.direct_requests} / Multi {e.multi_requests} 次请求{e.kind === 'paired' && ` · 质量 ${e.direct_quality} / ${e.multi_quality} · ${e.benefit ? '有收益' : '无收益'}`}</p><p className="break-all font-mono text-xs text-secondary">{e.direct_run_id ?? '估算'} → {e.multi_run_id}</p></div>) : <p className="text-sm text-secondary">尚无对照记录。</p>}</Card>
      <details><summary className="text-sm">反馈历史</summary>{data.feedback.map(f => <p key={f.feedback_id} className="py-1 text-xs">{feedbackLabels[f.kind]} · {f.subject_id} · {f.created_at}</p>)}</details>
    </> : <p role="status">{error || '加载评估中…'}</p>}
  </div></main>
}
