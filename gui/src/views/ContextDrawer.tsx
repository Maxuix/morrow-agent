import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { ApiClient } from '../api/client'
import { ApiError } from '../api/client'
import type { ManagementCommand, ManagementQueries, ResolvedContext, Scope } from '../api/management'
import { PreferenceManager, ProfileManager, KnowledgeManager, LearningManager } from './LearningManager'
import { SkillManager } from './SkillManager'

export const fieldClass = 'w-full rounded-[8px] border border-subtle bg-base px-3 py-2 text-sm text-primary'
export const buttonClass = 'rounded-[8px] border border-subtle px-3 py-1.5 text-xs hover:border-accent disabled:opacity-40'
export type Mutate = (kind: ManagementCommand, body: Record<string, unknown>, target?: string) => Promise<boolean>
export function Card({ title, children }: { title: string; children: ReactNode }) {
  return <article className="space-y-3 rounded-[10px] border border-subtle bg-raised p-4"><h3 className="font-medium">{title}</h3>{children}</article>
}
export function Facts({ value }: { value: unknown }) {
  return <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-[8px] bg-base p-3 text-xs text-secondary">{JSON.stringify(value, null, 2)}</pre>
}
export function useManagement<K extends keyof ManagementQueries>(client: ApiClient, kind: K, scope: Scope, refresh: number, page = 0) {
  const [data, setData] = useState<ManagementQueries[K] | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    setData(null)
    let request = 0
    const load = () => {
      const current = ++request
      void client.managementQuery(kind, { scope, page }).then(value => { if (active && current === request) { setData(value); setError('') } })
        .catch(() => { if (active && current === request) setError('加载失败，请刷新重试。') })
    }
    load()
    window.addEventListener('focus', load)
    const timer = window.setInterval(load, 10000)
    return () => { active = false; window.clearInterval(timer); window.removeEventListener('focus', load) }
  }, [client, kind, scope, refresh, page])
  return { data, error }
}

export function Pager({ page, next, onChange }: { page: number; next: string | null; onChange: (page: number) => void }) {
  return <div className="flex items-center gap-3 text-xs">
    <button className={buttonClass} disabled={page === 0} onClick={() => onChange(page - 1)}>上一页</button>
    <span>第 {page + 1} 页</span>
    <button className={buttonClass} disabled={!next} onClick={() => onChange(page + 1)}>下一页</button>
  </div>
}

export function ResolvedContextView({ value }: { value: ResolvedContext }) {
  return <div className="space-y-4">
    <p className="text-sm text-secondary">这里显示所选 AgentRun 实际采用的上下文。编辑影响之后的解析；历史运行保持原值。</p>
    <p className="break-all font-mono text-xs">{value.workspace_id} · {value.agent_run_id ?? '尚无已解析运行'}</p>
    {value.refresh_status !== 'ok' && <p role="status">偏好刷新降级，请检查当前配置。</p>}
    <Card title="Resolved Preferences">
      {value.preferences.length === 0 && <p className="text-sm text-secondary">本次运行未注入偏好。</p>}
      {value.preferences.map(p => <div key={p.preference_id}><p>{p.statement}</p><p className="text-xs text-secondary">{p.scope} · {p.preference_id} · r{p.revision}</p></div>)}
      <p className="text-xs text-secondary">省略 {value.omitted_count} 条</p>
    </Card>
    <Card title="本次 Profile">{value.profile ? <Facts value={value.profile} /> : <p>未注入 Profile</p>}</Card>
    <Card title="选中的 Knowledge">
      {value.knowledge.length === 0 && <p className="text-sm text-secondary">本次未选中 Knowledge。</p>}
      {value.knowledge.map(k => <div key={k.selection.record_id}><p>{k.revision?.statement ?? '历史修订不可用'}</p><p className="text-xs text-secondary">{k.selection.record_id} · r{k.selection.revision}</p></div>)}
      <p className="break-all text-xs text-secondary">{value.memory_selection_id}</p>
    </Card>
    <details><summary className="text-sm">来源修订与解析摘要</summary><Facts value={{ sources: value.source_revisions, digest: value.preference_digest }} /></details>
  </div>
}

export function ContextDrawer({ client, taskId, context, connected, onChanged, onClose }: {
  client: ApiClient; taskId: string | null; context: ResolvedContext | null; connected: boolean
  onChanged: () => void; onClose: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [tab, setTab] = useState('resolved')
  const [scope, setScope] = useState<Scope>('workspace')
  const [refresh, setRefresh] = useState(0)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [selectedRun, setSelectedRun] = useState('')
  const [resolved, setResolved] = useState(context)
  // Preserve identity across an uncertain delivery; editing creates a new command.
  const retry = useRef<{ key: string; commandId: string } | null>(null)
  useEffect(() => { const d = dialog.current; d?.showModal(); return () => d?.close() }, [])
  useEffect(() => { setSelectedRun('') }, [taskId])
  useEffect(() => {
    let active = true
    if (!selectedRun) { setResolved(context); return }
    setResolved(null)
    void client.managementQuery('context', { task_run_id: taskId ?? undefined, agent_run_id: selectedRun })
      .then(value => { if (active) setResolved(value) })
      .catch(() => { if (active) setMessage('运行上下文加载失败。') })
    return () => { active = false }
  }, [client, taskId, selectedRun, context, refresh])
  const mutate: Mutate = async (kind, body, target) => {
    if (busy || !connected) return false
    setBusy(true); setMessage('')
    const key = JSON.stringify([kind, target, body])
    const commandId = typeof body.command_id === "string" ? body.command_id : retry.current?.key === key ? retry.current.commandId : `cmd_${crypto.randomUUID().replaceAll('-', '')}`
    retry.current = { key, commandId }
    try {
      await client.managementCommand(kind, { ...body, command_id: commandId }, target)
      retry.current = null
      setRefresh(n => n + 1); onChanged(); setMessage('已保存。新设置将在之后的上下文解析中生效。')
      return true
    } catch (error) {
      if (error instanceof ApiError && error.status < 500) retry.current = null
      setMessage(error instanceof ApiError && error.status === 409 ? '内容已变化或状态不允许此操作，请刷新并重新检查。' : '保存失败。可重试相同操作；输入会保留。')
      return false
    } finally { setBusy(false) }
  }
  const props = { client, scope, refresh, mutate }
  return <dialog ref={dialog} onCancel={onClose} aria-labelledby="context-title"
    className="fixed inset-0 m-auto h-[92vh] max-h-[100dvh] w-[960px] max-w-[96vw] rounded-[12px] border border-subtle bg-base p-0 text-primary shadow-xl backdrop:bg-black/40">
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between gap-4 border-b border-subtle px-6 py-4">
        <div><h2 id="context-title" className="font-serif text-xl">上下文与学习</h2><p className="mt-1 text-xs text-secondary">查看来源、确认学习，管理偏好和 Skills。</p></div>
        <button className={buttonClass} onClick={onClose}>关闭</button>
      </header>
      <nav className="flex flex-wrap gap-2 border-b border-subtle px-6 py-3" aria-label="上下文管理分类">
        {([['resolved', '本次上下文'], ['preferences', '偏好'], ['profile', 'Profile'], ['knowledge', 'Knowledge'], ['learning', 'Learning'], ['skills', 'Skills']] as const).map(([key, label]) =>
          <button key={key} className={`${buttonClass} ${tab === key ? 'text-accent border-accent' : ''}`} aria-pressed={tab === key} onClick={() => { setTab(key); setMessage('') }}>{label}</button>)}
      </nav>
      <div className="flex flex-wrap items-center gap-3 px-6 py-3 text-xs">
        {(tab === 'preferences' || tab === 'skills') && <label>作用域 <select className={buttonClass} value={scope} onChange={e => setScope(e.target.value as Scope)}><option value="workspace">Workspace</option><option value="global">Global</option></select></label>}
        <button className={buttonClass} disabled={busy} onClick={() => { setRefresh(n => n + 1); onChanged() }}>刷新</button>
        {!connected && <span role="status">连接中断，恢复后可编辑。</span>}
        <span role="status" className="text-secondary">{message}</span>
      </div>
      <fieldset disabled={busy || !connected} className="min-h-0 flex-1 overflow-auto px-6 pb-6">
        {tab === 'resolved' && <>
          {(context?.available_runs.length ?? 0) > 1 && <label className="mb-4 block text-sm">运行 <select className={fieldClass} value={selectedRun} onChange={e => setSelectedRun(e.target.value)}><option value="">最近一次运行</option>{context?.available_runs.map(r => <option key={r.agent_run_id}>{r.agent_run_id}</option>)}</select></label>}
          {resolved ? <ResolvedContextView value={resolved} /> : <p>尚未加载上下文，请刷新。</p>}
        </>}
        {tab === 'preferences' && <PreferenceManager key={scope} {...props} context={context} />}
        {tab === 'profile' && <ProfileManager {...props} />}
        {tab === 'knowledge' && <KnowledgeManager {...props} />}
        {tab === 'learning' && <LearningManager {...props} />}
        {tab === 'skills' && <SkillManager key={scope} {...props} />}
      </fieldset>
    </div>
  </dialog>
}
