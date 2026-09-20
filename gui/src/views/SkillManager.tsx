import { SkillOperations, SkillRemoval } from './SkillOperations'
import type { ApiClient } from '../api/client'
import { useEffect, useMemo, useState } from 'react'
import type { ManagedSkill, SkillDraft } from '../api/management'
import type { DirtyGuard } from '../state/navigation'
import { Card, FieldList, OriginBadge, Pager } from './management/components'
import { LeaveGuardDialog, useLeaveGuard, useLeavePrompt } from './management/LeaveGuard'
import { useManagement } from './management/hooks'
import { buttonClass, fieldClass } from './management/styles'
import type { Mutate } from './management/types'
import type { ManagerProps } from './LearningManager'

export function partitionSkills(skills: ManagedSkill[]) {
  return { active: skills.filter(s => s.enabled), inactive: skills.filter(s => !s.enabled) }
}

function availabilityLabel(value: string): string {
  if (value === 'available') return '可用'
  if (value === 'unavailable') return '不可用'
  if (value === 'unknown') return '版本未知'
  return value
}

export function SkillCard({ item, digest, scope, mutate, client, onChanged }: { item: ManagedSkill; digest: string; scope: string; mutate: Mutate; client:ApiClient; onChanged:()=>void }) {
  const s = item.status
  const [version, setVersion] = useState(s.binding?.pinned_version_id ?? item.versions[0]?.version.version_id ?? '')
  const details = item.versions.find(v => v.version.version_id === version)
  const change = (action: string) => mutate('skill-binding', { action, scope, expected_digest: digest, version_id: ['pin', 'update'].includes(action) ? version : null }, s.skill_id)
  const origin = scope === 'global' || s.source_kind === 'global' ? 'global' : 'workspace'
  const enableLabel = item.enabled
    ? (scope === 'global' ? '停用全局 Skill' : '在本项目停用')
    : (scope === 'global' ? '在全局启用（不会自动加入本项目）' : '在本项目启用')
  return <Card title={s.name}>
    <p className="text-xs text-secondary flex flex-wrap items-center gap-2">
      <OriginBadge origin={origin === 'global' ? 'global' : 'workspace'} />
      {s.source_kind} · {item.enabled ? '已启用' : '未启用'} · {availabilityLabel(s.availability)}
    </p>
    <p className="text-sm">{details?.summary ?? '未提供 SKILL.md 摘要'}</p>
    <FieldList items={[
      { label: '实际信任', value: s.effective_trust },
      { label: '请求信任', value: s.requested_trust ?? '未声明（声明不会授予权限）' },
      { label: '当前绑定', value: s.binding?.pinned_version_id ? (details?.version.display_version ?? '已固定版本') : '自动选择可用版本' },
    ]} />
    {s.availability !== 'available' && <p role="status">限制：{availabilityLabel(s.availability)}</p>}
    <label className="block text-sm">版本
      <select className={fieldClass} aria-label={`${s.name}版本`} value={version} onChange={e => setVersion(e.target.value)}>
        {item.versions.map(v => <option key={v.version.version_id} value={v.version.version_id}>{v.version.display_version ?? '未命名版本'}</option>)}
      </select>
    </label>
    {details?.inspection_error && <p role="status">包内容校验失败，详情不可用。</p>}
    <details>
      <summary>请求的工具、权限与脚本</summary>
      <FieldList items={[
        { label: '工具', value: details?.manifest?.required_tools?.join('、') || '无' },
        { label: '权限', value: details?.manifest?.requested_permissions?.join('、') || '无' },
        { label: 'MCP', value: details?.manifest?.required_mcp_servers?.join('、') || '无' },
        { label: '脚本', value: details?.scripts?.join('、') || '无' },
      ]} />
    </details>
    <details>
      <summary>测试与运行证据（{item.usage.length}）</summary>
      {item.usage.length
        ? <ul className="asset-evidence">{item.usage.map(row => <li key={row.usage_id}>{row.terminal_status} · {row.version_id ? '指定版本' : '未绑定版本'}</li>)}</ul>
        : <p className="py-2 text-xs text-secondary">暂无评估记录</p>}
    </details>
    <div className="flex flex-wrap gap-2">
      <button type="button" className={buttonClass} disabled={!item.enabled && s.availability !== 'available'} onClick={() => void change(item.enabled ? 'disable' : 'enable')}>{enableLabel}</button>
      <button type="button" className={buttonClass} disabled={!version || s.availability !== 'available'} onClick={() => void change('pin')}>绑定所选版本</button>
      <button type="button" className={buttonClass} disabled={!version || s.availability !== 'available'} onClick={() => void change('update')}>更新至所选版本</button>
      <button type="button" className={buttonClass} disabled={item.versions.length < 2 || s.availability !== 'available'} onClick={() => void change('rollback')}>回滚</button>
    </div>
    <SkillRemoval client={client} scope={scope} item={item} version={version} digest={digest} onChanged={onChanged}/>
  </Card>
}

export function DraftCard({ item, mutate, registerGuard }: {
  item: SkillDraft
  mutate: Mutate
  registerGuard?: (guard: DirtyGuard) => () => void
}) {
  const d = item.draft
  const [reason,setReason]=useState('rejected_by_user')
  const [editing, setEditing] = useState(false)
  const [content, setContent] = useState(item.skill_md ?? '')
  const draftDirty = editing && content !== (item.skill_md ?? '')
  const { open: guardOpen, confirmLeave, settle } = useLeavePrompt()
  const promptLeave = () => draftDirty ? confirmLeave() : Promise.resolve(true)
  useLeaveGuard(registerGuard, draftDirty, promptLeave)
  const change = (action: string) => mutate('skill-draft', { action, reason, expected_row_version: d.row_version, skill_md: action === 'edit' ? content : null }, d.draft_id)
  const mutable = ['draft', 'validated'].includes(d.status)
  return <Card title={`${d.name} · Draft`}>
    <p className="text-xs text-secondary"><OriginBadge origin="workspace" /> generated · {d.status}</p>

    <p className="text-xs">{item.inspection_error ? '内容校验失败，请检查 Draft 包。' : item.editable === false ? '敏感内容已遮盖，不能用遮盖后的文本覆盖原文件。' : ''}</p>
    <p className="text-xs">校验：{item.validation?.valid ? '通过' : item.validation ? '未通过' : '尚无结果'}</p>
    <details>
      <summary>来源与校验结果</summary>
      <FieldList items={[
        { label: '来源条数', value: String(d.evidence_refs.length) },
        { label: '发现', value: item.validation?.findings?.map(row => row.message).join('；') || '无' },
      ]} />
    </details>
    <details><summary>SKILL.md 内容</summary><pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs">{item.skill_md ?? '文档不可用'}</pre></details>
    <details>
      <summary>Draft Diff</summary>
      {item.text_diff && <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs">{item.text_diff}</pre>}
      {item.diff
        ? <FieldList items={[
          { label: '新增', value: item.diff.added.join('、') || '无' },
          { label: '删除', value: item.diff.removed.join('、') || '无' },
          { label: '修改', value: item.diff.changed.join('、') || '无' },
        ]} />
        : <p className="py-2 text-xs text-secondary">无历史修订</p>}
    </details>
    {mutable && <><label className="block text-sm">拒绝原因<input className={fieldClass} value={reason} maxLength={256} onChange={e=>setReason(e.target.value)}/></label><div className="flex gap-2">
      <button type="button" className={buttonClass} disabled={item.editable === false} onClick={() => setEditing(v => !v)}>编辑 SKILL.md</button>
      <button type="button" className={buttonClass} onClick={() => void change('validate')}>重新校验</button>
      <button type="button" className={buttonClass} disabled={!item.validation?.valid || d.status !== 'validated'} onClick={() => void change('accept')}>接受并发布版本</button>
      <button type="button" className={buttonClass} onClick={() => void change('reject')}>拒绝 Draft</button>
    </div>{editing && <div className="space-y-2"><label className="block text-sm">新的完整 SKILL.md<textarea className={`${fieldClass} min-h-40 font-mono`} value={content} maxLength={65536} onChange={e => setContent(e.target.value)} /></label><button type="button" className={buttonClass} disabled={!content.trim()} onClick={() => void change('edit')}>保存 Draft 修订</button></div>}</>}
    <LeaveGuardDialog
      open={guardOpen}
      title="Skill Draft 有未保存修改"
      description="草稿尚未保存。"
      onSave={() => { void change('edit'); settle(true) }}
      saveLabel="保存 Draft 并离开"
      onDiscard={() => { setEditing(false); setContent(item.skill_md ?? ''); settle(true) }}
      onStay={() => settle(false)}
    />
  </Card>
}

export function SkillManager({ client, scope, refresh, mutate, item, registerGuard }: ManagerProps & {
  item?: string
  registerGuard?: (guard: DirtyGuard) => () => void
}) {
  const [page, setPage] = useState(0)
  const [localRefresh,setLocalRefresh]=useState(0)
  const onChanged=()=>setLocalRefresh(v=>v+1)
  const { data, error } = useManagement(client, 'skills', scope, refresh+localRefresh, page)
  const drafts = useManagement(client, 'skill-drafts', scope, refresh+localRefresh, page)
  const [view, setView] = useState('Active')
  const [locateNotice, setLocateNotice] = useState('')
  const needle = item?.trim().toLowerCase() ?? ''
  const match = useMemo(() => {
    if (!needle || !data) return null
    const all = [...data.skills]
    return all.find(row => row.status.name.toLowerCase() === needle)
      ?? all.find(row => row.status.name.toLowerCase().includes(needle))
      ?? null
  }, [data, needle])
  const draftMatch = useMemo(() => {
    if (!needle || !drafts.data) return null
    return drafts.data.drafts.find(row => row.draft.name.toLowerCase() === needle)
      ?? drafts.data.drafts.find(row => row.draft.name.toLowerCase().includes(needle))
      ?? null
  }, [drafts.data, needle])
  useEffect(() => {
    if (!needle) { setLocateNotice(''); return }
    if (!data || !drafts.data) return
    if (match) {
      setView(match.enabled ? 'Active' : '未启用')
      setLocateNotice('')
      return
    }
    if (draftMatch) {
      setView(['draft', 'validated'].includes(draftMatch.draft.status) ? 'Drafts' : 'History')
      setLocateNotice('')
      return
    }
    setLocateNotice(`未找到名为「${item}」的 Skill，已打开列表。`)
  }, [needle, data, drafts.data, match, draftMatch, item])
  if (!data?.skills || !drafts.data) return <p role="status">{error || drafts.error || '加载中…'}</p>
  const groups = partitionSkills(data.skills)
  const listedSkills = view === 'Active' ? groups.active : view === '未启用' ? groups.inactive : []
  return <div className="space-y-4">
    <SkillOperations client={client} scope={scope} mutate={mutate} onChanged={onChanged} registerGuard={registerGuard}/>
    {locateNotice && <p role="status">{locateNotice}</p>}
    {match && !locateNotice && <p role="status">已定位到 Skill：{match.status.name}</p>}
    {draftMatch && !match && !locateNotice && <p role="status">已定位到 Draft：{draftMatch.draft.name}</p>}
    <nav className="flex flex-wrap gap-2" aria-label="Skill 状态">{['Active', '未启用', 'Drafts', 'History'].map(v => <button type="button" key={v} className={buttonClass} aria-pressed={view === v} onClick={() => {setView(v);setPage(0)}}>{({Active: '已启用', Drafts: '草稿', History: '历史'} as Record<string, string>)[v] ?? v}</button>)}</nav>

    {listedSkills.map(row => <SkillCard key={`${scope}:${row.status.skill_id}`} item={row} digest={data.binding_digest} scope={scope} mutate={mutate} client={client} onChanged={onChanged} />)}
    {view === 'Active' && listedSkills.length === 0 && <p className="text-sm text-secondary">此范围没有已启用 Skill。</p>}
    {view === '未启用' && listedSkills.length === 0 && <p className="text-sm text-secondary">此范围没有未启用的包。</p>}
    {['Active', '未启用'].includes(view) && <Pager page={page} next={data.next_cursor??null} onChange={setPage} />}
    {['Drafts', 'History'].includes(view) && <Pager page={page} next={drafts.data.next_cursor} onChange={setPage} />}
    {['Drafts', 'History'].includes(view) && drafts.data.drafts.filter(d => view === 'Drafts' ? ['draft', 'validated'].includes(d.draft.status) : !['draft', 'validated'].includes(d.draft.status)).map(d => <DraftCard key={`${d.draft.draft_id}:${d.draft.row_version}`} item={d} mutate={mutate} registerGuard={registerGuard} />)}
    {view === 'Drafts' && !drafts.data.drafts.some(d => ['draft', 'validated'].includes(d.draft.status)) && <p className="text-sm text-secondary">暂无待审阅草稿</p>}
  </div>
}
