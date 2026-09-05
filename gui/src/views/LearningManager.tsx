import { useState } from 'react'
import type { ApiClient } from '../api/client'
import type { LearningPage, Preference, ResolvedContext, Scope } from '../api/management'
import { buttonClass, Card, Facts, fieldClass, Pager, useManagement } from './ContextDrawer'
import type { Mutate } from './ContextDrawer'

export interface ManagerProps { client: ApiClient; scope: Scope; refresh: number; mutate: Mutate }
function Loading({ error }: { error: string }) { return <p role="status">{error || '加载中…'}</p> }
export function ViewFilter({ value, onChange, proposed = false }: { value: string; onChange: (s: string) => void; proposed?: boolean }) {
  return <nav className="mb-4 flex gap-2" aria-label="记录状态">{(proposed ? ['Proposed', 'History'] : ['Active', 'History']).map(s => <button key={s} className={buttonClass} aria-pressed={value === s} onClick={() => onChange(s)}>{s}</button>)}</nav>
}
export function PreferenceManager({ client, scope, refresh, mutate, context }: ManagerProps & { context: ResolvedContext | null }) {
  const { data, error } = useManagement(client, 'preferences', scope, refresh)
  const [filter, setFilter] = useState('Active')
  const [statement, setStatement] = useState('')
  const [editing, setEditing] = useState<Preference | null>(null)
  const [baseRevision, setBaseRevision] = useState<number | null>(null)
  if (!data) return <Loading error={error} />
  const document = data.document
  const write = async (operation: string, preference?: Preference) => {
    const ok = await mutate('preferences', { arguments: { scope, expected_revision: baseRevision ?? document.revision,
      operations: [{ operation, preference_id: preference?.preference_id ?? null,
        statement: ['add', 'replace'].includes(operation) ? statement : null }] } })
    if (ok) { setEditing(null); setStatement(''); setBaseRevision(null) }
  }
  return <div className="space-y-4">
    <ViewFilter value={filter} onChange={setFilter} />
    <p className="text-xs text-secondary">{scope} · 文档修订 {document.revision}。停用或删除保留历史，替换生成新修订。</p>
    <Card title={editing ? '编辑偏好' : '新增偏好'}>
      <label className="block text-sm">规则<textarea className={fieldClass} maxLength={512} value={statement} onChange={e => { setStatement(e.target.value); if (baseRevision === null) setBaseRevision(document.revision) }} /></label>
      {editing && <p className="text-xs text-secondary">当前值：{editing.statement} · r{editing.revision} · 来源：{editing.evidence_ids?.join(', ') || '用户直接编辑'}</p>}
      <p className="text-xs text-secondary">影响：之后的 {scope === 'global' ? '所有工作空间' : '当前工作空间'} 上下文解析。</p>
      <div className="flex gap-2"><button className={buttonClass} disabled={!statement.trim()} onClick={() => void write(editing ? 'replace' : 'add', editing ?? undefined)}>保存偏好</button>
        {editing && <button className={buttonClass} onClick={() => { setEditing(null); setStatement(''); setBaseRevision(null) }}>取消编辑</button>}</div>
    </Card>
    {document.entries.filter(p => filter === 'Active' ? p.status === 'active' : p.status !== 'active').map(p => <Card key={p.preference_id} title={p.statement}>
      <p className="text-xs text-secondary">{p.scope} · {p.status} · r{p.revision} · {p.preference_id}</p>
      <p className="text-xs text-secondary">来源：{p.evidence_ids?.join(', ') || '用户直接编辑'} · {context?.preferences.some(r => r.preference_id === p.preference_id && r.revision === p.revision) ? '此修订已用于所选运行' : '此修订未用于所选运行'}</p>
      {p.status !== 'deleted' && <div className="flex flex-wrap gap-2">
        <button className={buttonClass} onClick={() => { setEditing(p); setStatement(p.statement); setBaseRevision(document.revision) }}>编辑</button>
        <button className={buttonClass} onClick={() => void write(p.status === 'active' ? 'disable' : 'enable', p)}>{p.status === 'active' ? '停用' : '启用'}</button>
        <button className={buttonClass} onClick={() => void write('remove', p)}>删除</button>
      </div>}
    </Card>)}
    {filter === 'History' && <Card title="修订历史">{data.history.map(h => <details key={h.batch_id}><summary className="py-1 text-xs">r{h.expected_document_revision} → r{h.expected_document_revision + 1} · {h.status} · {h.created_at}</summary><Facts value={{ operations: h.operations, before: h.before, after: h.after }} /></details>)}</Card>}
  </div>
}

export function ProfileManager({ client, scope, refresh, mutate }: ManagerProps) {
  const { data, error } = useManagement(client, 'profile', scope, refresh)
  const [path, setPath] = useState('name')
  const [operation, setOperation] = useState('set')
  const [value, setValue] = useState('')
  const [revision, setRevision] = useState<number | null>(null)
  if (!data) return <Loading error={error} />
  const list = ['goals', 'tech_stack', 'constraints', 'conventions'].includes(path)
  const save = async (reset = false) => {
    const ok = await mutate('profile', { expected_revision: revision ?? data.revision, command: {
      scope: 'workspace', target: 'profile', operation: reset ? 'reset' : operation,
      path: reset ? null : path, value: reset || operation === 'unset' ? null : value,
    } })
    if (ok) { setValue(''); setRevision(null) }
  }
  return <div className="space-y-4"><Card title="当前 Profile"><p className="text-xs text-secondary">Workspace · r{data.revision}</p><Facts value={data.profile ?? '尚未创建：先设置名称'} /></Card>
    <Card title="编辑 Profile">
      <label className="block text-sm">字段<select className={fieldClass} value={path} onChange={e => { setPath(e.target.value); setOperation(['name', 'summary'].includes(e.target.value) ? 'set' : 'append'); setValue(''); setRevision(data.revision) }}>
        {Object.entries({ name: '名称', summary: '摘要', goals: '目标', tech_stack: '技术栈', constraints: '约束', conventions: '约定' }).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      <label className="block text-sm">操作<select className={fieldClass} value={operation} onChange={e => setOperation(e.target.value)}>
        {(list ? [['append', '添加'], ['remove', '移除']] : path === 'summary' ? [['set', '设置'], ['unset', '清除']] : [['set', '设置']]).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      {operation !== 'unset' && <label className="block text-sm">内容<textarea className={fieldClass} value={value} maxLength={list ? 512 : 2048} onChange={e => { setValue(e.target.value); if (revision === null) setRevision(data.revision) }} /></label>}
      <div className="flex gap-2"><button className={buttonClass} disabled={!value.trim() && operation !== 'unset'} onClick={() => void save()}>保存 Profile</button><button className={buttonClass} disabled={!data.profile} onClick={() => void save(true)}>清空 Profile</button></div>
    </Card></div>
}

export function KnowledgeManager({ client, scope, refresh, mutate }: ManagerProps) {
  const [page, setPage] = useState(0)
  const { data, error } = useManagement(client, 'knowledge', scope, refresh, page)
  const [filter, setFilter] = useState('Active')
  if (!data) return <Loading error={error} />
  return <div className="space-y-4"><Pager page={page} next={data.next_cursor} onChange={setPage} /><ViewFilter value={filter} onChange={setFilter} />
    <p className="text-sm text-secondary">新增或替换 Knowledge：在 Learning 中检查来源，编辑候选后接受。每次变更保留证据和被替代修订。</p>
    {data.items.filter(k => filter === 'Active' ? k.head.status === 'active' : true).map(k => <Card key={k.head.knowledge_id} title={k.head.semantic_key}>
      <p>{k.revision?.statement}</p><p className="text-xs text-secondary">Workspace · {k.head.category} · {k.head.status} · r{k.revision?.revision} · {k.head.knowledge_id}</p>
      <details><summary className="text-sm">来源与修订历史</summary><Facts value={{ evidence: k.evidence, revisions: k.timeline }} /></details>
      <div className="flex gap-2">{(k.head.status === 'active' ? ['disable', 'dispute', 'delete'] : k.head.status === 'disabled' ? ['enable', 'delete'] : k.head.status === 'disputed' ? ['delete'] : []).map(action => <button key={action} className={buttonClass} onClick={() => void mutate('knowledge', { action, expected_row_version: k.head.row_version }, k.head.knowledge_id)}>{{ enable: '启用', disable: '停用', dispute: '标记争议', delete: '删除' }[action]}</button>)}</div>
    </Card>)}
    {!data.items.length && <p className="text-sm text-secondary">尚无 Knowledge。Learning 候选经确认后会出现在这里。</p>}
  </div>
}

function CandidateCard({ item, mutate }: { item: LearningPage['candidates'][number]; mutate: Mutate }) {
  const c = item.candidate
  const payload = c.proposed_payload
  const editableKey = typeof payload.statement === 'string' ? 'statement' : 'value' in payload ? 'value' : null
  const original = editableKey ? payload[editableKey] : ''
  const [edit, setEdit] = useState(Array.isArray(original) ? original.join('\n') : String(original))
  const [resolution, setResolution] = useState('none')
  const [editing, setEditing] = useState(false)
  const decide = (action: string) => mutate('learning-decision', {
    action, expected_row_version: c.row_version, scope: 'workspace', conflict_resolution: resolution,
    final_payload: action === 'accept' && editing && editableKey ? { ...payload, [editableKey]: Array.isArray(original) ? edit.split('\n').filter(Boolean) : edit } : null,
  }, c.candidate_id)
  return <Card title={c.semantic_key}><p className="text-xs text-secondary">{c.candidate_type} · {c.proposed_scope} · {c.status} · r{c.row_version}</p>
    <p className="text-sm">当前值：{item.target.statement ?? item.target.reason ?? '无'}</p>
    <Facts value={payload} /><details><summary>来源与冲突</summary><Facts value={{ evidence: item.evidence, conflicts: item.conflicts }} /></details>
    {item.expired && c.status === 'proposed' && <p className="text-sm text-secondary">候选已过期，不能再接受。</p>}
    {c.status === 'proposed' && !item.expired && <>
      {editableKey && <label className="block text-sm"><input type="checkbox" checked={editing} onChange={e => setEditing(e.target.checked)} /> 编辑候选后接受{editing && <textarea className={fieldClass} value={edit} maxLength={4096} onChange={e => setEdit(e.target.value)} />}</label>}
      {c.candidate_type === 'project_knowledge' && <label className="block text-sm">现有记录处理<select className={fieldClass} value={resolution} onChange={e => setResolution(e.target.value)}>{Object.entries({ none: '新增（已有记录时要求解决冲突）', confirm: '确认现有记录', replace: '替换现有修订', merge: '合并为编辑后的内容', re_enable: '重新启用', resolve_dispute: '解决争议' }).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>}
      <div className="flex gap-2"><button className={buttonClass} onClick={() => void decide('accept')}>{editing ? '编辑并接受' : '接受候选'}</button><button className={buttonClass} onClick={() => void decide('reject')}>拒绝</button><button className={buttonClass} onClick={() => void decide('suppress')}>拒绝且不再建议</button></div>
    </>}
    {c.candidate_type === 'skill_candidate' && ['accepted', 'edited_and_accepted'].includes(c.status) && <button className={buttonClass} onClick={() => void mutate('skill-draft-create', { candidate_id: c.candidate_id })}>生成 Skill Draft</button>}
  </Card>
}
function ProposalCard({ p, mutate }: { p: LearningPage['proposals'][number]; mutate: Mutate }) {
  const [statement, setStatement] = useState(p.operation.statement ?? '')
  const decide = (action: string) => mutate('preference-decision', { action, expected_row_version: p.row_version, expected_document_revision: p.expected_document_revision, expected_target_revision: p.expected_target_revision, edit: action === 'accept' && statement !== p.operation.statement && p.operation.operation !== 'remove' ? statement : null }, p.proposal_id)
  return <Card title={`偏好候选 · ${p.operation.scope}`}><p className="text-xs text-secondary">{p.status} · {p.operation.operation} · 目标文档 r{p.expected_document_revision} · {p.operation.preference_id ?? '新增规则'}</p>
    <p className="text-sm">来源：{p.evidence_source_kind} · {p.evidence_excerpt ?? p.evidence_id}</p>
    {p.status === 'proposed' && p.operation.operation !== 'remove' ? <label className="block text-sm">提议规则<textarea className={fieldClass} value={statement} maxLength={512} onChange={e => setStatement(e.target.value)} /></label> : <p>{p.operation.statement}</p>}
    {p.stale && <p className="text-sm text-secondary">候选已过期：{p.stale_reason}。可拒绝后重新学习。</p>}
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
