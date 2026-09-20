import { useCallback, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { LearningPage, Scope } from '../api/management'
import type { DirtyGuard } from '../state/navigation'
import { Card, FieldList, Pager } from './management/components'
import { LeaveGuardDialog, useLeaveGuard, useLeavePrompt } from './management/LeaveGuard'
import { useManagement } from './management/hooks'
import { buttonClass, fieldClass } from './management/styles'
import type { Mutate } from './management/types'

export interface ManagerProps { client: ApiClient; scope: Scope; refresh: number; mutate: Mutate }
function Loading({ error }: { error: string }) { return <p role="status">{error || '加载中…'}</p> }
export function ViewFilter({ value, onChange, proposed = false }: { value: string; onChange: (s: string) => void; proposed?: boolean }) {
  return <nav className="mb-4 flex gap-2" aria-label="记录状态">{(proposed ? ['Proposed', 'History'] : ['Active', 'History']).map(s => <button key={s} className={buttonClass} aria-pressed={value === s} onClick={() => onChange(s)}>{s}</button>)}</nav>
}
export function KnowledgeManager({ client, scope, refresh, mutate }: ManagerProps) {
  const [page, setPage] = useState(0)
  const { data, error } = useManagement(client, 'knowledge', scope, refresh, page)
  const [filter, setFilter] = useState('Active')
  if (!data) return <Loading error={error} />
  return <div className="space-y-4"><Pager page={page} next={data.next_cursor} onChange={setPage} /><ViewFilter value={filter} onChange={setFilter} />
    <p className="text-sm text-secondary">新增或替换 Knowledge：在 Learning 中检查来源，编辑候选后接受。每次变更保留证据和被替代修订。</p>
    {data.items.filter(k => filter === 'Active' ? k.head.status === 'active' : true).map(k => <Card key={k.head.knowledge_id} title={k.head.semantic_key}>
      <p>{k.revision?.statement}</p><p className="text-xs text-secondary">本项目 · {k.head.category} · {k.head.status}</p>
      <details><summary className="text-sm">来源与修订历史</summary>
        <FieldList items={[
          { label: '来源', value: k.evidence.map(row => row.source_kind).join('、') || '尚无来源' },
          { label: '修订数', value: String(k.timeline.length) },
        ]} />
        {k.evidence.length > 0 && <ul className="asset-evidence">{k.evidence.map(row => <li key={row.evidence_id}>{row.source_kind}{row.excerpt_redacted ? ` · ${row.excerpt_redacted}` : ''}</li>)}</ul>}
      </details>
      <div className="flex gap-2">{(k.head.status === 'active' ? ['disable', 'dispute', 'delete'] : k.head.status === 'disabled' ? ['enable', 'delete'] : k.head.status === 'disputed' ? ['delete'] : []).map(action => <button type="button" key={action} className={buttonClass} onClick={() => void mutate('knowledge', { action, expected_row_version: k.head.row_version }, k.head.knowledge_id)}>{{ enable: '启用', disable: '停用', dispute: '标记争议', delete: '删除' }[action]}</button>)}</div>
    </Card>)}
    {!data.items.length && <p className="text-sm text-secondary">尚无 Knowledge。Learning 候选经确认后会出现在这里。</p>}
  </div>
}

export function CandidateCard({ item, mutate, registerGuard }: {
  item: LearningPage['candidates'][number]
  mutate: Mutate
  registerGuard?: (guard: DirtyGuard) => () => void
}) {
  const c = item.candidate
  const payload = c.proposed_payload
  const editableKey = typeof payload.statement === 'string' ? 'statement' : 'value' in payload ? 'value' : null
  const original = editableKey ? payload[editableKey] : ''
  const [edit, setEdit] = useState(Array.isArray(original) ? original.join('\n') : String(original))
  const [resolution, setResolution] = useState('none')
  const [editing, setEditing] = useState(false)
  const { open: guardOpen, confirmLeave, settle } = useLeavePrompt()
  const promptLeave = useCallback(() => editing ? confirmLeave() : Promise.resolve(true), [confirmLeave, editing])
  useLeaveGuard(registerGuard, editing, promptLeave)
  const [reason,setReason]=useState('rejected_by_user');const [semantic,setSemantic]=useState(String(payload.semantic_key??''));const [category,setCategory]=useState(String(payload.category??'other'));const [path,setPath]=useState(String(payload.path??''))
  const decide = (action: string) => mutate('learning-decision', {
    action, reason, expected_row_version: c.row_version, scope: 'workspace', conflict_resolution: resolution,
    final_payload: action === 'accept' && editing && editableKey ? { ...payload, ...(c.candidate_type==='project_knowledge'?{semantic_key:semantic,category}:{}), ...(c.candidate_type==='profile'?{path}:{}), [editableKey]: c.candidate_type==='profile'?(['goals','tech_stack','constraints','conventions'].includes(path)?edit.split('\n').filter(Boolean):edit):Array.isArray(original)?edit.split('\n').filter(Boolean):edit } : null,
  }, c.candidate_id)
  const payloadText = editableKey ? String(Array.isArray(original) ? original.join('\n') : original) : ''
  return <Card title={c.semantic_key}><p className="text-xs text-secondary">{c.candidate_type} · {c.proposed_scope === 'global' ? '全局' : '本项目'} · {c.status}</p>
    <p className="text-sm">当前值：{item.target.statement ?? item.target.reason ?? '无'}</p>
    <FieldList items={[
      { label: '建议正文', value: payloadText || undefined },
      { label: '语义键', value: typeof payload.semantic_key === 'string' ? payload.semantic_key : undefined },
      { label: '类别', value: typeof payload.category === 'string' ? payload.category : undefined },
    ]} />
    <details><summary>来源与冲突</summary>
      <p className="text-xs text-secondary">{item.evidence.length ? item.evidence.map(row => row.source_kind).join('、') : '来源未知'} · 冲突 {item.conflicts.length} 项</p>
      {item.evidence.length > 0 && <ul className="asset-evidence">{item.evidence.map(row => <li key={row.evidence_id}>{row.source_kind}{row.excerpt_redacted ? ` · ${row.excerpt_redacted}` : ''}</li>)}</ul>}
    </details>
    {item.expired && c.status === 'proposed' && <p className="text-sm text-secondary">候选已过期，不能再接受。</p>}
    {c.status === 'proposed' && !item.expired && <>
      {editableKey && <label className="block text-sm"><input type="checkbox" checked={editing} onChange={e => setEditing(e.target.checked)} /> 编辑候选后接受{editing && <textarea className={fieldClass} value={edit} maxLength={4096} onChange={e => setEdit(e.target.value)} />}</label>}
      {editing&&c.candidate_type==='project_knowledge'&&<><label className="block text-sm">语义键<input className={fieldClass} value={semantic} onChange={e=>setSemantic(e.target.value)}/></label><label className="block text-sm">类别<select className={fieldClass} value={category} onChange={e=>setCategory(e.target.value)}>{['architecture','convention','decision','environment','domain','other'].map(c=><option key={c}>{c}</option>)}</select></label></>}
      {editing&&c.candidate_type==='profile'&&<label className="block text-sm">Profile 字段<select className={fieldClass} value={path} onChange={e=>setPath(e.target.value)}>{['name','summary','goals','tech_stack','constraints','conventions'].map(p=><option key={p}>{p}</option>)}</select></label>}
      <label className="block text-sm">拒绝原因<input className={fieldClass} value={reason} maxLength={256} onChange={e=>setReason(e.target.value)}/></label>
      {c.candidate_type === 'project_knowledge' && <label className="block text-sm">现有记录处理<select className={fieldClass} value={resolution} onChange={e => setResolution(e.target.value)}>{Object.entries({ none: '新增（已有记录时要求解决冲突）', confirm: '确认现有记录', replace: '替换现有修订', merge: '合并为编辑后的内容', re_enable: '重新启用', resolve_dispute: '解决争议' }).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>}
      <div className="flex gap-2"><button className={buttonClass} onClick={() => void decide('accept')}>{editing ? '编辑并接受' : '接受候选'}</button><button className={buttonClass} onClick={() => void decide('reject')}>拒绝</button><button className={buttonClass} onClick={() => void decide('suppress')}>拒绝且不再建议</button></div>
    </>}
    {c.candidate_type === 'skill_candidate' && ['accepted', 'edited_and_accepted'].includes(c.status) && <button className={buttonClass} onClick={() => void mutate('skill-draft-create', { candidate_id: c.candidate_id })}>生成 Skill Draft</button>}
    <LeaveGuardDialog
      open={guardOpen}
      title="候选编辑尚未提交"
      description="编辑后的候选还没有接受。可以放弃后离开，或留在此页继续。"
      onDiscard={() => { setEditing(false); settle(true) }}
      onStay={() => settle(false)}
    />
  </Card>
}
export function ProposalCard({ p, mutate }: { p: LearningPage['proposals'][number]; mutate: Mutate }) {
  const [statement, setStatement] = useState(p.operation.statement ?? '');const [reason,setReason]=useState('')
  const decide = (action: string) => mutate('preference-decision', { action, reason:reason||null, expected_row_version: p.row_version, expected_document_revision: p.expected_document_revision, expected_target_revision: p.expected_target_revision, edit: action === 'accept' && statement !== p.operation.statement && p.operation.operation !== 'remove' ? statement : null }, p.proposal_id)
  return <Card title={`偏好候选 · ${p.operation.scope === 'global' ? '全局' : '本项目'}`}><p className="text-xs text-secondary">{p.status} · {p.operation.operation} · {p.operation.preference_id ? '替换现有规则' : '新增规则'}</p>
    <p className="text-sm">来源：{p.evidence_source_kind} · {p.evidence_excerpt ?? p.evidence_id}</p>
    {p.status === 'proposed' && p.operation.operation !== 'remove' ? <label className="block text-sm">提议规则<textarea className={fieldClass} value={statement} maxLength={512} onChange={e => setStatement(e.target.value)} /></label> : <p>{p.operation.statement}</p>}
    {p.stale && <p className="text-sm text-secondary">候选已过期：{p.stale_reason}。可拒绝后重新学习。</p>}
    {p.status==='proposed'&&<label className="block text-sm">拒绝原因<input className={fieldClass} value={reason} maxLength={256} onChange={e=>setReason(e.target.value)}/></label>}
    {p.status === 'proposed' && <div className="flex gap-2"><button className={buttonClass} disabled={p.stale} onClick={() => void decide('accept')}>接受偏好</button><button className={buttonClass} onClick={() => void decide('reject')}>拒绝</button><button className={buttonClass} onClick={() => void decide('suppress')}>拒绝且不再建议</button></div>}
  </Card>
}
export function LearningManager({ client, scope, refresh, mutate }: ManagerProps) {
  const [page, setPage] = useState(0)
  const { data, error } = useManagement(client, 'learning', scope, refresh, page)
  const [filter, setFilter] = useState('Proposed')
  if (!data) return <Loading error={error} />
  const show = (status: string, expired = false) => filter === 'Proposed' ? status === 'proposed' && !expired : status !== 'proposed' || expired
  return <div className="space-y-4"><Pager page={page} next={data.next_cursor} onChange={setPage} /><ViewFilter value={filter} onChange={setFilter} proposed />
    <p className="text-sm text-secondary">候选经确认后才会改变偏好或 Knowledge。Skill 候选确认后仍需单独审阅 Draft 和启用。</p>
    {data.proposals.filter(p => show(p.status)).map(p => <ProposalCard key={`${p.proposal_id}:${p.row_version}`} p={p} mutate={mutate} />)}
    {data.candidates.filter(c => show(c.candidate.status, c.expired)).map(c => <CandidateCard key={`${c.candidate.candidate_id}:${c.candidate.row_version}`} item={c} mutate={mutate} />)}
    {!data.proposals.some(p => show(p.status)) && !data.candidates.some(c => show(c.candidate.status, c.expired)) && <p className="text-sm text-secondary">此视图没有候选。</p>}
  </div>
}
