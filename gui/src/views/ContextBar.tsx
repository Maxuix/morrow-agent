import { useEffect, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { ResolvedContext } from '../api/management'
import { ContextDrawer } from './ContextDrawer'

export function contextSummary(value: ResolvedContext) {
  return `工作空间：${value.workspace_id} · 语言：${value.language ?? '见已解析规则'} · 详细度：${value.verbosity ?? '见已解析规则'} · 约定 ${value.convention_count} · 待确认学习 ${value.pending_learning_count}`
}

export function ContextBar({ client, taskId, cursor, connected }: {
  client: ApiClient; taskId: string | null; cursor: number; connected: boolean
}) {
  const [context, setContext] = useState<ResolvedContext | null>(null)
  const [error, setError] = useState(false)
  const [open, setOpen] = useState(false)
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    let cancelled = false
    setContext(null)
    let request = 0
    const load = () => {
      const current = ++request
      void client.managementQuery('context', taskId ? { task_run_id: taskId } : {})
        .then(value => { if (!cancelled && request === current) { setContext(value); setError(false) } })
        .catch(() => { if (!cancelled && request === current) setError(true) })
    }
    load()
    window.addEventListener('focus', load)
    const timer = window.setInterval(load, 10000)
    return () => { cancelled = true; window.clearInterval(timer); window.removeEventListener('focus', load) }
  }, [client, taskId, cursor, connected, refresh])
  return <>
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-subtle bg-raised px-4 py-2 text-xs">
      <button type="button" onClick={() => setOpen(true)} className="text-left text-secondary hover:text-accent" aria-label="打开上下文与学习管理">
        <span className="mr-3 font-medium text-primary">Active Context</span>
        {error ? '上下文加载失败，点击重试' : context ? contextSummary(context) : '读取上下文…'}
        {context?.status === 'not_started' && ' · 尚无已解析运行'}
      </button>
      <button type="button" className="text-accent" onClick={() => setOpen(true)}>上下文、学习与 Skills →</button>
    </div>
    {open && <ContextDrawer client={client} taskId={taskId} context={context} connected={connected}
      onChanged={() => setRefresh(n => n + 1)} onClose={() => setOpen(false)} />}
  </>
}
