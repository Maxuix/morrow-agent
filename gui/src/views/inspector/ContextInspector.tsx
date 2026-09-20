import { useEffect, useRef, useState } from 'react'
import type { ResolvedContext } from '../../api/management'
import { inspectorResourceKey, useInspectorResource } from '../../state/inspectorResources'
import { Card, FieldList } from '../management/components'
import { buttonClass, fieldClass } from '../management/styles'
import type { InspectorBodyProps } from './InspectorBodyProps'

function ContextUnavailable({ message }: { message: string }) {
  return <section className="flex h-full flex-col overflow-y-auto" aria-label="上下文" data-inspector-kind="context">
    <h2 className="px-4 pt-4 pb-2 text-xs font-medium tracking-wide text-secondary">上下文</h2>
    <p className="px-4 pb-4 text-sm text-secondary">{message}</p>
  </section>
}

function ListField({ label, values }: { label: string; values: string[] }) {
  if (values.length === 0) return null
  return <div className="asset-field"><dt>{label}</dt><dd><ul className="space-y-1">{values.map((value, index) => <li key={`${label}:${index}`}>{value}</li>)}</ul></dd></div>
}

function ContextBody({ value, onNavigate }: { value: ResolvedContext; onNavigate?: InspectorBodyProps['onNavigate'] }) {
  const prompt = value.prompt_constraints
  const selectedRun = value.available_runs.find(run => run.agent_run_id === value.agent_run_id)
    ?? value.available_runs[0]
  if (value.status === 'not_started' || value.agent_run_id === null) return <div className="px-4 py-3 text-sm text-secondary"><p>暂无运行上下文</p>{onNavigate && <div className="mt-3 flex flex-wrap gap-2"><button className={buttonClass} onClick={() => onNavigate({ kind: 'knowledge', section: 'profile' })}>查看项目画像</button><button className={buttonClass} onClick={() => onNavigate({ kind: 'knowledge', section: 'preferences' })}>查看行为偏好</button></div>}</div>
  return <div className="flex flex-col gap-3 px-4 pb-4 text-sm">
    {value.refresh_status !== 'ok' && <p role="status" className="text-blocked">偏好刷新已降级；以下仍是该运行保存的快照。</p>}

    <Card title="运行范围">
      {selectedRun && <div className="mb-3 rounded-[8px] border border-subtle bg-raised p-2">
        <p className="font-medium">{selectedRun.label ?? '当前执行运行'}</p>
        {(selectedRun.node_title || selectedRun.task_title) && <p className="text-xs text-secondary">
          {selectedRun.node_title ? `节点：${selectedRun.node_title}` : `任务：${selectedRun.task_title}`}
        </p>}
        {selectedRun.created_at && <p className="text-xs text-secondary">创建于 {selectedRun.created_at}</p>}
      </div>}
      <FieldList items={[
        { label: '会话', value: value.session_id ? '当前会话' : '未绑定会话' },
        { label: '任务', value: value.task_run_id ? '当前选定任务' : '未选定任务' },
        { label: '状态', value: value.status === 'resolved' ? '已解析' : '尚未开始' },
      ]} />
    </Card>

    <details open={value.profile !== null || undefined}><summary className="cursor-pointer text-secondary">项目画像</summary>
      {value.profile ? <>
        <p>{value.profile.name}</p>
        {value.profile.summary && <p className="text-secondary">{value.profile.summary}</p>}
        <dl className="asset-fields">
          <ListField label="目标" values={value.profile.goals} />
          <ListField label="技术栈" values={value.profile.tech_stack} />
          <ListField label="约束" values={value.profile.constraints} />
          <ListField label="约定" values={value.profile.conventions} />
        </dl>
      </> : <p className="text-secondary">未使用项目画像</p>}
      {onNavigate && <button type="button" className={buttonClass} onClick={() => onNavigate({ kind: 'knowledge', section: 'profile' })}>查看项目画像</button>}
    </details>

    <details open={value.preferences.length > 0 || undefined}><summary className="cursor-pointer text-secondary">偏好</summary>
      {value.preferences.length === 0 ? <p className="text-secondary">未使用偏好</p> : <div className="space-y-2">
        {value.preferences.map((preference, index) => <div key={`${preference.statement}:${index}`} className="rounded-[8px] border border-subtle p-2">
          <p>{preference.statement}</p><p className="text-xs text-secondary">{preference.scope} · 修订 r{preference.revision}</p>
        </div>)}
      </div>}
      {value.omitted_count > 0 && <p className="text-xs text-secondary">省略 {value.omitted_count} 条。</p>}
      {onNavigate && <button type="button" className={buttonClass} onClick={() => onNavigate({ kind: 'knowledge', section: 'preferences' })}>查看行为偏好</button>}
    </details>

    <details open={value.knowledge.length > 0 || undefined}><summary className="cursor-pointer text-secondary">知识</summary>
      {value.knowledge.length === 0 ? <p className="text-secondary">未使用知识</p> : <div className="space-y-2">
        {value.knowledge.map((item, index) => <div key={`${item.revision?.statement ?? 'missing'}:${index}`} className="rounded-[8px] border border-subtle p-2">
          <p>{item.revision?.statement ?? '历史修订正文不可用。'}</p><p className="text-xs text-secondary">冻结修订 r{item.selection.revision}</p>
        </div>)}
      </div>}
      {onNavigate && <button type="button" className={buttonClass} onClick={() => onNavigate({ kind: 'knowledge', section: 'knowledge' })}>查看知识库</button>}
    </details>

    <Card title="提示词约束">
      {prompt === undefined ? <p className="text-secondary">历史约束不可用。</p> : <>
        {prompt.message && <p role="status" className="text-secondary">{prompt.message}</p>}
        {prompt.sections.map(section => <section key={section.kind} className="rounded-[8px] border border-subtle p-2">
          <p className="font-medium">{section.label} · {section.available ? '已记录' : '缺失'}</p>
          <p className="text-xs text-secondary">{section.summary}</p>
          {section.sources && section.sources.length > 0 && <ul className="mt-2 space-y-1 text-xs text-secondary">
            {section.sources.map(source => <li key={`${source.path}:${source.scope}`}>
              {source.path} · {source.scope} · {source.byte_count} bytes
            </li>)}
          </ul>}
        </section>)}
      </>}
    </Card>
  </div>
}

/** Compact summary kept for status surfaces and pure rendering tests. */
export function contextSummary(value: ResolvedContext): string {
  return `工作空间：${value.workspace_id} · 语言：${value.language ?? '见已解析规则'} · 详细度：${value.verbosity ?? '见已解析规则'} · 约定 ${value.convention_count} · 待确认学习 ${value.pending_learning_count}`
}

/** Data-only rendering entry point; no dialog or cross-page shell is involved. */
export function ResolvedContextView({ value }: { value: ResolvedContext }) {
  return <ContextBody value={value} />
}

export function ContextInspector({ target, active, client, workspaceId, sessionId, currentTaskRunId, onNavigate }: InspectorBodyProps) {
  const [selectedRun, setSelectedRun] = useState(target?.agentRunId ?? '')
  const taskRunId = target?.taskRunId ?? currentTaskRunId ?? undefined
  const selectionScope = JSON.stringify([
    sessionId ?? null,
    taskRunId ?? null,
    target?.taskRunId ?? null,
    target?.agentRunId ?? null,
  ])
  const previousSelectionScope = useRef(selectionScope)
  const selectionScopeChanged = previousSelectionScope.current !== selectionScope
  // A session/task switch is visible before the reset effect commits. Do not
  // let that one render use the old opaque run target against the new scope.
  const selectedRunForRequest = selectionScopeChanged ? target?.agentRunId ?? '' : selectedRun
  useEffect(() => {
    setSelectedRun(target?.agentRunId ?? '')
    previousSelectionScope.current = selectionScope
  }, [currentTaskRunId, selectionScope, target?.agentRunId, target?.taskRunId])
  const ready = client !== undefined && workspaceId !== undefined && sessionId !== undefined
  const resource = useInspectorResource(
    inspectorResourceKey('context', workspaceId, sessionId, {
      task_run_id: taskRunId,
      agent_run_id: selectedRunForRequest || undefined,
    }),
    () => client!.managementQuery('context', {
      session_id: sessionId,
      task_run_id: taskRunId,
      agent_run_id: selectedRunForRequest || undefined,
    }),
    active && ready,
  )
  if (!ready) {
    return <ContextUnavailable message="上下文需要在已选定的会话中查看。" />
  }
  return <section className="flex h-full flex-col overflow-y-auto" aria-label="上下文" data-inspector-kind="context">
    <header className="flex items-center justify-between border-b border-subtle px-4 py-3">
      <div><h2 className="text-sm font-medium">上下文</h2></div>
      {resource.data && <button type="button" className={buttonClass} onClick={() => void resource.reload()} disabled={resource.loading}>刷新</button>}
    </header>
    {resource.error && <div className="px-4 pt-3"><p role="alert" className="text-sm text-failed">{resource.error}</p><button type="button" className={buttonClass} onClick={() => void resource.reload()}>重试</button></div>}
    {!resource.data && resource.loading && <p role="status" className="p-4 text-sm text-secondary">正在读取实际上下文…</p>}
    {resource.data && <>
      {resource.data.available_runs.length > 1 && <label className="px-4 pt-3 text-sm">运行
        <select className={fieldClass} aria-label="选择上下文运行" value={selectedRunForRequest || resource.data.agent_run_id || ''} onChange={event => setSelectedRun(event.target.value)}>
          {resource.data.available_runs.map((run, index) => <option key={run.agent_run_id} value={run.agent_run_id}>{run.label ?? run.task_title ?? `运行 ${index + 1}`}</option>)}
        </select>
      </label>}
      <ContextBody value={resource.data} onNavigate={onNavigate} />
    </>}
    {!resource.data && !resource.loading && !resource.error && <p role="status" className="p-4 text-sm text-secondary">暂时没有可显示的上下文。</p>}
  </section>
}
