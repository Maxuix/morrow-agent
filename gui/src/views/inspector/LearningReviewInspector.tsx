import { useEffect, useRef, useState } from 'react'
import { ApiError, type ApiClient } from '../../api/client'
import type { LearningPage } from '../../api/management'
import { inspectorResourceKey, useInspectorResource } from '../../state/inspectorResources'
import { commandId } from '../lib/editor'
import { CandidateCard, ProposalCard } from '../LearningManager'
import { Pager } from '../management/components'
import { buttonClass } from '../management/styles'
import type { Mutate } from '../management/types'
import type { InspectorBodyProps } from './InspectorBodyProps'

type CandidateItem = LearningPage['candidates'][number]
type ProposalItem = LearningPage['proposals'][number]

function sourceLabel(source: CandidateItem['source'] | ProposalItem['source'] | undefined): string {
  if (!source?.known) return '来源未知'
  return source.kind === 'task' ? '当前任务' : '当前会话'
}

function LearningUnavailable({ message }: { message: string }) {
  return <section className="flex h-full flex-col overflow-y-auto" aria-label="学习审阅" data-inspector-kind="learning">
    <h2 className="px-4 pt-4 pb-2 text-xs font-medium tracking-wide text-secondary">学习审阅</h2>
    <p className="px-4 pb-4 text-sm text-secondary">{message}</p>
  </section>
}

export function LearningReviewInspector({ target, active, client, workspaceId, sessionId, currentTaskRunId, onNavigate }: InspectorBodyProps) {
  const taskRunId = target?.taskRunId ?? currentTaskRunId ?? undefined
  if (!client || !workspaceId || !sessionId) {
    return <LearningUnavailable message="学习审阅需要在已选定的会话中查看。" />
  }
  return <LearningReviewContent
    active={active}
    client={client}
    workspaceId={workspaceId}
    sessionId={sessionId}
    taskRunId={taskRunId}
    onNavigate={onNavigate}
  />
}

function LearningReviewContent({ active, client, workspaceId, sessionId, taskRunId, onNavigate }: {
  active: boolean
  client: ApiClient
  workspaceId: string
  sessionId: string
  taskRunId?: string
  onNavigate?: InspectorBodyProps['onNavigate']
}) {
  const [page, setPage] = useState(0)
  useEffect(() => setPage(0), [sessionId, taskRunId])
  const pageKey = inspectorResourceKey('learning', workspaceId, sessionId, {
    task_run_id: taskRunId,
    page: String(page),
  })
  const resource = useInspectorResource(
    pageKey,
    () => client.managementQuery('learning', { session_id: sessionId, task_run_id: taskRunId, page }),
    active,
  )
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const retry = useRef<{ key: string; id: string } | null>(null)
  const mutate: Mutate = async (kind, body, targetId) => {
    if (!targetId || busy) return false
    const key = JSON.stringify([kind, targetId, body])
    const command = retry.current?.key === key ? retry.current.id : commandId('learning')
    retry.current = { key, id: command }
    setBusy(true)
    setMessage(null)
    try {
      await client.managementCommand(kind, { ...body, command_id: command }, targetId)
      retry.current = null
      setMessage('审阅决定已提交。')
      await resource.reload()
      return true
    } catch (error) {
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) retry.current = null
      setMessage(error instanceof Error ? error.message : '审阅决定失败；请按原请求重试。')
      return false
    } finally {
      setBusy(false)
    }
  }
  const data = resource.data
  return <section className="flex h-full flex-col overflow-y-auto" aria-label="学习审阅" data-inspector-kind="learning">
    <header className="flex items-center justify-between border-b border-subtle px-4 py-3">
      <div><h2 className="text-sm font-medium">学习审阅</h2></div>
      {data && <button type="button" className={buttonClass} disabled={resource.loading || busy} onClick={() => void resource.reload()}>刷新</button>}
    </header>
    {data && <p className="px-4 pt-3 text-xs text-secondary">
      范围：{data.source_scope === 'task' ? '当前任务' : data.source_scope === 'session' ? '当前会话' : '工作区'} · 候选 {data.candidate_count ?? data.candidates.length} · 偏好 {data.proposal_count ?? data.proposals.length}
    </p>}
    {message && <p role="status" className="px-4 pt-2 text-sm">{message}</p>}
    {resource.error && <div className="px-4 pt-3"><p role="alert" className="text-sm text-failed">{resource.error}</p><button type="button" className={buttonClass} onClick={() => void resource.reload()}>重试</button></div>}
    {!data && resource.loading && <p role="status" className="p-4 text-sm text-secondary">正在读取本会话学习候选…</p>}
    {data && <div className="flex flex-col gap-3 px-4 pb-4 pt-3">
      <Pager page={page} next={data.next_cursor} onChange={setPage} />
      {data.proposals.map(proposal => <article key={`${proposal.proposal_id}:${proposal.row_version}`} className="space-y-2">
        <p className="text-xs text-secondary">{sourceLabel(proposal.source)}</p>
        <ProposalCard p={proposal} mutate={mutate} />
      </article>)}
      {data.candidates.map(candidate => <article key={`${candidate.candidate.candidate_id}:${candidate.candidate.row_version}`} className="space-y-2">
        <p className="text-xs text-secondary">{sourceLabel(candidate.source)}</p>
        <CandidateCard item={candidate} mutate={mutate} />
      </article>)}
      {data.proposals.length === 0 && data.candidates.length === 0 && <p className="text-sm text-secondary">暂无待审阅内容</p>}
      {onNavigate && <button type="button" className={buttonClass} onClick={() => onNavigate({ kind: 'knowledge', section: 'knowledge', focus: 'learning' })}>查看全部学习审阅{(data.unknown_source_count ?? 0) > 0 ? `（含 ${data.unknown_source_count} 条来源未知记录）` : ''}</button>}
    </div>}
    {!data && !resource.loading && !resource.error && <p role="status" className="p-4 text-sm text-secondary">暂无可显示的学习记录。</p>}
  </section>
}
