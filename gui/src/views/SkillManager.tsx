import { useState } from 'react'
import type { ManagedSkill, SkillDraft } from '../api/management'
import { buttonClass, Card, Facts, fieldClass, Pager, useManagement } from './ContextDrawer'
import type { Mutate } from './ContextDrawer'
import type { ManagerProps } from './LearningManager'

export function partitionSkills(skills: ManagedSkill[]) {
  return { active: skills.filter(s => s.enabled), inactive: skills.filter(s => !s.enabled) }
}
function SkillCard({ item, digest, scope, mutate }: { item: ManagedSkill; digest: string; scope: string; mutate: Mutate }) {
  const s = item.status
  const [version, setVersion] = useState(s.binding?.pinned_version_id ?? item.versions[0]?.version.version_id ?? '')
  const details = item.versions.find(v => v.version.version_id === version)
  const change = (action: string) => mutate('skill-binding', { action, scope, expected_digest: digest, version_id: ['pin', 'update'].includes(action) ? version : null }, s.skill_id)
  return <Card title={s.name}>
    <p className="text-xs text-secondary">{scope} · {s.source_kind} · {item.enabled ? '已启用' : '未启用'} · {s.availability}</p>
    <p className="text-sm">{details?.summary ?? '未提供 SKILL.md 摘要'}</p>
    <p className="text-xs text-secondary">实际信任：{s.effective_trust} · 请求信任：{s.requested_trust ?? '未声明'}。声明不会授予权限。</p>
    <label className="block text-sm">版本<select className={fieldClass} value={version} onChange={e => setVersion(e.target.value)}>{item.versions.map(v => <option key={v.version.version_id} value={v.version.version_id}>{v.version.display_version ?? v.version.version_id} · {v.version.version_id}</option>)}</select></label>
    <p className="break-all text-xs text-secondary">当前 Pin：{s.binding?.pinned_version_id ?? '自动选择可用版本'} · {details?.version.tree_digest}</p>
    {details?.inspection_error && <p role="status">包内容校验失败，详情不可用。</p>}
    <details><summary>请求的工具、权限与脚本</summary><Facts value={{ tools: details?.manifest?.required_tools, capabilities: details?.manifest?.requested_permissions, mcp: details?.manifest?.required_mcp_servers, scripts: details?.scripts }} /></details>
    <details><summary>测试与运行证据（{item.usage.length}）</summary>{item.usage.length ? <Facts value={item.usage} /> : <p className="py-2 text-xs text-secondary">尚无测试或评估记录；没有推断质量结论。</p>}</details>
    <div className="flex flex-wrap gap-2">
      <button className={buttonClass} disabled={!item.enabled && s.availability !== 'available'} onClick={() => void change(item.enabled ? 'disable' : 'enable')}>{item.enabled ? '停用 Skill' : '启用 Skill'}</button>
      <button className={buttonClass} disabled={!version || s.availability !== 'available'} onClick={() => void change('pin')}>Pin 所选版本</button>
      <button className={buttonClass} disabled={!version || s.availability !== 'available'} onClick={() => void change('update')}>更新至所选版本</button>
      <button className={buttonClass} disabled={item.versions.length < 2 || s.availability !== 'available'} onClick={() => void change('rollback')}>回滚</button>
    </div>
  </Card>
}
export function DraftCard({ item, mutate }: { item: SkillDraft; mutate: Mutate }) {
  const d = item.draft
  const [editing, setEditing] = useState(false)
  const [content, setContent] = useState(item.skill_md ?? '')
  const change = (action: string) => mutate('skill-draft', { action, expected_row_version: d.row_version, skill_md: action === 'edit' ? content : null }, d.draft_id)
  const mutable = ['draft', 'validated'].includes(d.status)
  return <Card title={`${d.name} · Draft`}>
    <p className="text-xs text-secondary">Workspace · generated · {d.status} · r{d.revision}</p>
    <p className="text-sm text-secondary">接受只发布不可变版本，仍需在未启用列表中单独启用。</p>
    <p className="text-xs">{item.inspection_error ? '内容校验失败，请检查 Draft 包。' : item.editable === false ? '敏感内容已遮盖，不能用遮盖后的文本覆盖原文件。' : ''}</p>
    <p className="text-xs">校验：{item.validation?.valid ? '通过' : item.validation ? '未通过' : '尚无结果'}</p>
    <details><summary>来源与校验结果</summary><Facts value={{ evidence: d.evidence_refs, validation: item.validation }} /></details>
    <details><summary>SKILL.md 内容</summary><pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs">{item.skill_md ?? '文档不可用'}</pre></details>
    <details><summary>Draft Diff</summary>{item.text_diff && <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs">{item.text_diff}</pre>}{item.diff ? <Facts value={item.diff} /> : <p className="py-2 text-xs text-secondary">初始 Draft，尚无父修订。</p>}</details>
    {mutable && <><div className="flex gap-2">
      <button className={buttonClass} disabled={item.editable === false} onClick={() => setEditing(v => !v)}>编辑 SKILL.md</button>
      <button className={buttonClass} onClick={() => void change('validate')}>重新校验</button>
      <button className={buttonClass} disabled={!item.validation?.valid || d.status !== 'validated'} onClick={() => void change('accept')}>接受并发布版本</button>
      <button className={buttonClass} onClick={() => void change('reject')}>拒绝 Draft</button>
    </div>{editing && <div className="space-y-2"><label className="block text-sm">新的完整 SKILL.md<textarea className={`${fieldClass} min-h-40 font-mono`} value={content} maxLength={65536} onChange={e => setContent(e.target.value)} /></label><p className="text-xs text-secondary">保存为新的 Draft 修订；保留原有脚本和资源，并重新校验。</p><button className={buttonClass} disabled={!content.trim()} onClick={() => void change('edit')}>保存 Draft 修订</button></div>}</>}
  </Card>
}
export function SkillManager({ client, scope, refresh, mutate }: ManagerProps) {
  const { data, error } = useManagement(client, 'skills', scope, refresh)
  const [page, setPage] = useState(0)
  const drafts = useManagement(client, 'skill-drafts', scope, refresh, page)
  const [view, setView] = useState('Active')
  if (!data || !drafts.data) return <p role="status">{error || drafts.error || '加载中…'}</p>
  const groups = partitionSkills(data.skills)
  return <div className="space-y-4">
    <nav className="flex flex-wrap gap-2" aria-label="Skill 状态">{['Active', '未启用', 'Drafts', 'History'].map(v => <button key={v} className={buttonClass} aria-pressed={view === v} onClick={() => setView(v)}>{v}</button>)}</nav>
    <p className="text-xs text-secondary">Active 只显示已启用的包。自动生成内容始终先进入 workspace Drafts。</p>
    {(view === 'Active' ? groups.active : view === '未启用' ? groups.inactive : []).map(item => <SkillCard key={`${scope}:${item.status.skill_id}`} item={item} digest={data.binding_digest} scope={scope} mutate={mutate} />)}
    {view === 'Active' && groups.active.length === 0 && <p className="text-sm text-secondary">此作用域没有已启用 Skill。</p>}
    {view === '未启用' && groups.inactive.length === 0 && <p className="text-sm text-secondary">此作用域没有未启用的包。</p>}
    {['Drafts', 'History'].includes(view) && <Pager page={page} next={drafts.data.next_cursor} onChange={setPage} />}
    {['Drafts', 'History'].includes(view) && drafts.data.drafts.filter(d => view === 'Drafts' ? ['draft', 'validated'].includes(d.draft.status) : !['draft', 'validated'].includes(d.draft.status)).map(d => <DraftCard key={`${d.draft.draft_id}:${d.draft.row_version}`} item={d} mutate={mutate} />)}
    {view === 'Drafts' && !drafts.data.drafts.some(d => ['draft', 'validated'].includes(d.draft.status)) && <p className="text-sm text-secondary">没有待审阅 Draft。在 Learning History 中可从已接受的 Skill 候选生成。</p>}
  </div>
}
