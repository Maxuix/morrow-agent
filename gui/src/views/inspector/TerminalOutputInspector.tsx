import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import type { ActivityItem, ContentAvailability } from '../../api/activity'
import type { ApiClient } from '../../api/client'
import type { CommandOutputWire, TaskArtifactsWire } from '../../api/types'
import { type ActivityState, type ActivityStore, durationText } from '../../state/activity'
import { inspectorResourceKey, useInspectorResource } from '../../state/inspectorResources'
import { CopyButton } from '../ChatMessage'
import { buttonClass } from '../management/styles'
import { ArtifactContentPreview, selectorParams } from './TaskArtifactsInspector'
import type { InspectorBodyProps } from './InspectorBodyProps'

const COMMAND_TOOLS = new Set(['bash', 'run_command'])
const EMPTY_ACTIVITY: ActivityState = {
  status: 'offline', items: [], content: {}, activity_epoch: '', activity_sequence: 0,
}
const EMPTY_SUBSCRIBE = (_listener: () => void) => () => undefined
const EMPTY_GET_STATE = () => EMPTY_ACTIVITY

function statusLabel(value: string): string {
  const labels: Record<string, string> = {
    preparing: '准备中', waiting: '等待批准', running: '运行中', succeeded: '已完成',
    failed: '失败', cancelled: '已取消', skipped: '已跳过', unknown: '未知状态',
  }
  return labels[value] ?? value
}

function availabilityLabel(value: ContentAvailability): string {
  return {
    live: '实时', evicted: '已按预算淘汰', unsaved: '未持久化', none: '无正文', committed: '已持久化',
  }[value]
}

function commandInScope(item: ActivityItem, target: InspectorBodyProps['target'], data: TaskArtifactsWire): boolean {
  if (item.kind !== 'tool' || item.payload.kind !== 'tool' || !COMMAND_TOOLS.has(item.payload.tool_name)) return false
  const workflowRunId = target?.workflowRunId ?? (data.workflow_run_ids.length === 1 ? data.workflow_run_ids[0] : undefined)
  if (workflowRunId && item.identity.workflow_run_id !== workflowRunId) return false
  if (target?.nodeRunId && item.identity.node_run_id !== target.nodeRunId) return false
  return true
}

function LiveCommandRow({ item, text }: { item: ActivityItem; text?: string }) {
  const payload = item.payload.kind === 'tool' ? item.payload : null
  const duration = durationText(item.started_at, item.ended_at ?? null, Date.now())
  const output = text ?? ''
  return <article className="rounded-[8px] border border-subtle bg-raised p-3" data-activity-id={item.activity_id}>
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm"><strong>{payload?.tool_name ?? item.safe_title}</strong><span className="text-secondary">{statusLabel(item.state)}</span><span className="text-xs text-secondary">{duration}</span></div>
    <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs"><dt className="text-secondary">执行摘要</dt><dd>{item.safe_summary ?? item.safe_title}</dd>{payload?.exit_code !== null && payload?.exit_code !== undefined && <><dt className="text-secondary">退出码</dt><dd>{payload.exit_code}</dd></>}</dl>
    {output ? <div className="mt-3 rounded-[8px] border border-subtle bg-base p-2"><div className="flex items-center justify-between text-xs text-secondary"><span>命令输出</span><CopyButton text={output} label="复制输出" /></div><pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-xs">{output}</pre></div> : <p className="mt-3 text-xs text-secondary">{item.availability === 'live' && (item.state === 'running' || item.state === 'preparing') ? '等待命令输出…' : `输出正文：${availabilityLabel(item.availability)}`}</p>}
  </article>
}

function CommandRow({ item, selected, onSelect }: { item: CommandOutputWire; selected: boolean; onSelect: () => void }) {
  return <li><button type="button" className={`w-full rounded-[8px] border p-3 text-left text-sm ${selected ? 'border-accent bg-raised' : 'border-subtle hover:border-accent'}`} aria-pressed={selected} onClick={onSelect}>
    <span className="flex items-baseline gap-2"><span className="font-medium">第 {item.ordinal} 个命令</span><span className="text-secondary">{item.tool_name}</span><span className="ml-auto text-xs text-secondary">{statusLabel(item.state)}</span></span>
    <span className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-xs text-secondary"><span>{item.command_class ?? '命令类别未记录'}</span>{item.cwd && <span>{item.cwd}</span>}{item.exit_code !== null ? <span>退出码 {item.exit_code}</span> : <span>退出码未记录</span>}<span>{item.output_availability === 'available' ? '输出可用' : item.output_availability === 'not_persisted' ? '输出未保存' : item.output_availability === 'missing' ? '输出缺失' : item.output_availability === 'corrupt' ? '输出损坏' : '输出暂存'}</span></span>
  </button></li>
}

function PersistentOutput({ data, command, active, client, workspaceId, sessionId, params }: { data: TaskArtifactsWire; command: CommandOutputWire; active: boolean; client: ApiClient; workspaceId: string; sessionId: string; params: ReturnType<typeof selectorParams> }) {
  const artifact = command.output_artifact_id ? data.artifacts.find(item => item.artifact_id === command.output_artifact_id) : undefined
  if (!artifact) {
    return <div className="rounded-[8px] border border-subtle bg-raised p-3 text-sm"><p className="font-medium">命令输出</p><p className="mt-2 text-secondary">{command.message ?? (command.output_availability === 'not_persisted' ? '历史执行只保留元数据，输出正文未保存。' : '输出正文当前不可用。')}</p>{command.output_excerpt && <PreviewText text={command.output_excerpt} truncated={command.output_truncated} />}</div>
  }
  return <ArtifactContentPreview active={active} client={client} workspaceId={workspaceId} sessionId={sessionId} params={params} artifact={artifact} availability={artifact.availability} omissionReason={artifact.omission_reason ?? command.message} contentEncoding={artifact.content_encoding} />
}

function PreviewText({ text, truncated }: { text: string; truncated: boolean }) {
  return <div className="mt-3 rounded-[8px] border border-subtle bg-base p-2"><div className="flex items-center justify-between text-xs text-secondary"><span>命令输出{truncated ? '（已截断）' : ''}</span><CopyButton text={text} label="复制输出" /></div><pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-xs">{text}</pre></div>
}

function Unavailable({ message }: { message: string }) {
  return <section className="flex h-full flex-col overflow-y-auto" aria-label="终端输出" data-inspector-kind="terminal"><h2 className="px-4 pt-4 pb-2 text-xs font-medium tracking-wide text-secondary">终端输出</h2><p className="px-4 pb-4 text-sm text-secondary">{message}</p></section>
}

export function TerminalOutputInspector({ target, active, client, workspaceId, sessionId, currentTaskRunId, activityStore }: InspectorBodyProps) {
  if (!client || !workspaceId || !sessionId) return <Unavailable message="终端输出需要在已选定的会话中查看。" />
  return <TerminalOutputContent target={target} active={active} client={client} workspaceId={workspaceId} sessionId={sessionId} currentTaskRunId={currentTaskRunId} activityStore={activityStore ?? null} />
}

function TerminalOutputContent({ target, active, client, workspaceId, sessionId, currentTaskRunId, activityStore }: { target: InspectorBodyProps['target']; active: boolean; client: ApiClient; workspaceId: string; sessionId: string; currentTaskRunId: string | null | undefined; activityStore: ActivityStore | null }) {
  const params = selectorParams(target, currentTaskRunId)
  const key = inspectorResourceKey('task-artifacts', workspaceId, sessionId, params)
  const resource = useInspectorResource<TaskArtifactsWire>(key, () => client.taskArtifacts(workspaceId, sessionId, params), active)
  const activityState = useSyncExternalStore(activityStore?.subscribe ?? EMPTY_SUBSCRIBE, activityStore?.getState ?? EMPTY_GET_STATE)
  const [filter, setFilter] = useState('')
  const [follow, setFollow] = useState(true)
  const [selected, setSelected] = useState<string | null>(null)
  const outputRef = useRef<HTMLDivElement>(null)
  const data = resource.data
  const liveItems = useMemo(() => data ? activityState.items.filter(item => commandInScope(item, target, data)) : [], [activityState.items, data, target])
  const commands = useMemo(() => {
    const values = data?.command_outputs ?? []
    const text = filter.trim().toLocaleLowerCase()
    if (!text) return values
    return values.filter(item => [item.tool_name, item.command_class, item.cwd, item.state, item.disposition, item.message].filter(Boolean).join(' ').toLocaleLowerCase().includes(text))
  }, [data?.command_outputs, filter])
  useEffect(() => {
    if (!active || !follow || outputRef.current === null) return
    outputRef.current.scrollTop = outputRef.current.scrollHeight
  }, [active, follow, liveItems.length, activityState.content, commands.length])
  useEffect(() => {
    if (selected && !commands.some(item => `command:${item.tool_execution_id}` === selected)) setSelected(null)
  }, [commands, selected])
  const selectedCommand = selected?.startsWith('command:') ? commands.find(item => `command:${item.tool_execution_id}` === selected) : undefined
  return <section className="flex h-full min-h-0 flex-col overflow-hidden" aria-label="终端输出" data-inspector-kind="terminal">
    <header className="flex shrink-0 items-center justify-between gap-3 border-b border-subtle px-4 py-3"><div><h2 className="text-sm font-medium">终端输出</h2></div><button type="button" className={buttonClass} disabled={resource.loading} onClick={() => void resource.reload()}>刷新</button></header>
    <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-subtle px-4 py-2"><input aria-label="过滤命令输出" className="min-w-0 flex-1 rounded-[8px] border border-subtle bg-base px-2 py-1.5 text-xs" value={filter} onChange={event => setFilter(event.target.value)} placeholder="按工具、类别、目录或状态过滤" /><button type="button" className={buttonClass} aria-pressed={follow} onClick={() => setFollow(value => !value)}>{follow ? '跟随最新' : '已停止跟随'}</button>{!follow && <button type="button" className={buttonClass} onClick={() => { setFollow(true); if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight }}>回到最新输出</button>}</div>
    {resource.error && <div className="px-4 pt-3"><p role="alert" className="text-sm text-failed">{resource.error}</p><button type="button" className={buttonClass} onClick={() => void resource.reload()}>重试</button></div>}
    {!data && resource.loading && <p role="status" className="p-4 text-sm text-secondary">正在读取命令历史…</p>}
    {data && <div ref={outputRef} className="min-h-0 flex-1 overflow-y-auto px-4 pb-4 pt-3" onScroll={event => { const el = event.currentTarget; const atTail = el.scrollHeight - el.scrollTop - el.clientHeight < 48; if (follow !== atTail) setFollow(atTail) }}>
      <div className="space-y-3">
        {liveItems.length > 0 && <section aria-label="实时命令执行"><h3 className="text-xs font-medium text-secondary">实时执行</h3><div className="mt-2 space-y-2">{liveItems.map(item => <LiveCommandRow key={item.activity_id} item={item} text={item.activity_id in activityState.content ? activityState.content[item.activity_id].text : undefined} />)}</div></section>}
        {commands.length > 0 && <section aria-label="历史命令输出"><h3 className="text-xs font-medium tracking-wide text-secondary">历史命令输出 · {commands.length}</h3><ul className="mt-2 space-y-2">{commands.map(item => <CommandRow key={item.tool_execution_id} item={item} selected={selected === `command:${item.tool_execution_id}`} onSelect={() => setSelected(`command:${item.tool_execution_id}`)} />)}</ul></section>}
        {liveItems.length === 0 && commands.length === 0 && <p className="text-sm text-secondary">{filter ? '无匹配记录' : '暂无命令记录'}</p>}
        {activityStore && activityState.status === 'offline' && <p role="status" className="text-xs text-secondary">实时输出已断开</p>}
        {selectedCommand && <section aria-label="选中命令输出"><div className="flex items-baseline justify-between gap-2"><h3 className="text-sm font-medium">第 {selectedCommand.ordinal} 个命令的输出</h3><span className="text-xs text-secondary">{selectedCommand.disposition}</span></div><div className="mt-2"><PersistentOutput data={data} command={selectedCommand} active={active} client={client} workspaceId={workspaceId} sessionId={sessionId} params={params} /></div></section>}
      </div>
    </div>}
    {!data && !resource.loading && !resource.error && <p role="status" className="p-4 text-sm text-secondary">暂时没有可显示的命令输出。</p>}
  </section>
}
