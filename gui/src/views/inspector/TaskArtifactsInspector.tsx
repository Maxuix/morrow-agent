import { outcomeSummary } from '../lib/outcomePresentation'
import { useEffect, useState } from 'react'
import type { ApiClient } from '../../api/client'
import type {
  TaskArtifactContentWire,
  TaskArtifactItemWire,
  TaskArtifactsWire,
  TaskFileChangeWire,
  WorkspaceFileReadWire,
} from '../../api/types'
import { inspectorResourceKey, useInspectorResource } from '../../state/inspectorResources'
import { CopyButton } from '../ChatMessage'
import { TaskOutcomeActions } from '../TaskOutcomeActions'
import { buttonClass } from '../management/styles'
import { TASK_STATUS_LABELS } from '../lib/labels'
import type { InspectorBodyProps } from './InspectorBodyProps'

const EMPTY_ARTIFACTS: TaskArtifactsWire = {
  schema_version: 1,
  session_id: '',
  task_run_id: null,
  title: '任务产物',
  status: null,
  purpose: null,
  workflow_run_id: null,
  workflow_run_ids: [],
  node_run_id: null,
  task: null,
  outcomes: [],
  artifacts: [],
  files: [],
  command_outputs: [],
  availability: 'empty',
  message: null,
}

const SOURCE_LABELS: Record<TaskArtifactItemWire['source'], string> = {
  task_evidence: '任务证据',
  registered_result: '登记结果',
  workflow_output: '工作流输出',
  workflow_delivery: '交付文件',
  command_output: '命令输出',
}

const AVAILABILITY_LABELS: Record<TaskArtifactItemWire['availability'], string> = {
  available: '可预览',
  staging: '发布中',
  missing: '正文缺失',
  corrupt: '正文损坏',
  unavailable: '暂不可用',
}

const FILE_AVAILABILITY_LABELS: Record<TaskFileChangeWire['availability'], string> = {
  ...AVAILABILITY_LABELS,
}

const RETENTION_LABELS: Record<string, string> = {
  standard: '标准保留',
  pinned: '已固定保留',
}

const selectionMemory = new Map<string, string>()

function rememberSelection(scope: string, value: string): void {
  if (selectionMemory.has(scope)) selectionMemory.delete(scope)
  selectionMemory.set(scope, value)
  while (selectionMemory.size > 24) selectionMemory.delete(selectionMemory.keys().next().value!)
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
}

function statusLabel(status: string | null): string {
  if (status === null) return ''
  return TASK_STATUS_LABELS[status as keyof typeof TASK_STATUS_LABELS] ?? status
}

export function selectorParams(target: InspectorBodyProps['target'], currentTaskRunId: string | null | undefined) {
  return {
    task_run_id: target?.taskRunId ?? currentTaskRunId ?? undefined,
    workflow_run_id: target?.workflowRunId,
    node_run_id: target?.nodeRunId,
  }
}

function scopeKey(workspaceId: string, sessionId: string, params: ReturnType<typeof selectorParams>): string {
  return JSON.stringify([workspaceId, sessionId, params.task_run_id ?? null, params.workflow_run_id ?? null, params.node_run_id ?? null])
}

function selectionExists(data: TaskArtifactsWire, value: string | null): boolean {
  if (value === null) return false
  return data.files.some(item => `file:${item.path}` === value)
    || data.artifacts.some(item => `artifact:${item.artifact_id}` === value)
}

function availabilityNote(availability: TaskArtifactItemWire['availability'], omission: string | null): string {
  if (omission) return omission
  return AVAILABILITY_LABELS[availability]
}

/** Bounded plain-text/Diff preview. React text nodes keep untrusted output inert. */
export function ArtifactContentPreview({
  active,
  client,
  workspaceId,
  sessionId,
  params,
  artifact,
  diff,
  diffTruncated,
  availability,
  omissionReason,
  contentEncoding,
}: {
  active: boolean
  client: ApiClient
  workspaceId: string
  sessionId: string
  params: ReturnType<typeof selectorParams>
  artifact?: TaskArtifactItemWire
  diff?: string | null
  diffTruncated?: boolean
  availability: TaskArtifactItemWire['availability']
  omissionReason?: string | null
  contentEncoding: TaskArtifactItemWire['content_encoding']
}) {
  const shouldLoad = artifact !== undefined && artifact.diff === null && availability === 'available'
  const key = inspectorResourceKey('task-artifact-content', workspaceId, sessionId, {
    artifact_id: artifact?.artifact_id,
    ...params,
  })
  const content = useInspectorResource<TaskArtifactContentWire>(
    key,
    () => client.taskArtifactContent(workspaceId, sessionId, artifact!.artifact_id, params),
    active && shouldLoad,
  )
  const preview = diff ?? artifact?.diff ?? content.data?.content ?? null
  const truncated = Boolean(diffTruncated || artifact?.diff_truncated || content.data?.truncated)

  if (availability !== 'available') {
    return <p role="status" className="rounded-[8px] border border-subtle bg-raised p-3 text-sm text-secondary">{availabilityNote(availability, omissionReason ?? null)}</p>
  }
  if (contentEncoding === 'binary' || content.data?.encoding === 'binary') {
    return <p role="status" className="rounded-[8px] border border-subtle bg-raised p-3 text-sm text-secondary">此文件暂不支持文本预览。</p>
  }
  if (preview !== null) return <PreviewText text={preview} label="Diff / 变更正文" truncated={truncated} />
  if (content.error) return <p role="alert" className="rounded-[8px] border border-failed p-3 text-sm text-failed">{content.error}</p>
  if (content.loading || (shouldLoad && content.data === null)) return <p role="status" className="rounded-[8px] border border-subtle bg-raised p-3 text-sm text-secondary">正在读取正文…</p>
  return <p role="status" className="rounded-[8px] border border-subtle bg-raised p-3 text-sm text-secondary">暂无正文。</p>
}

function PreviewText({ text, label, truncated }: { text: string; label: string; truncated: boolean }) {
  return <div className="rounded-[8px] border border-subtle bg-base p-3">
    <div className="flex items-center justify-between gap-2 text-xs text-secondary"><span>{label}{truncated ? '（已截断）' : ''}</span><CopyButton text={text} label="复制正文" /></div>
    <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">{text}</pre>
  </div>
}

function Unavailable({ message }: { message: string }) {
  return <section className="flex h-full flex-col overflow-y-auto" aria-label="任务产物" data-inspector-kind="artifacts">
    <h2 className="px-4 pt-4 pb-2 text-xs font-medium tracking-wide text-secondary">任务产物</h2><p className="px-4 pb-4 text-sm text-secondary">{message}</p>
  </section>
}

function ArtifactRow({ item, selected, onSelect }: { item: TaskArtifactItemWire; selected: boolean; onSelect: () => void }) {
  return <li><button type="button" className={`w-full rounded-[8px] border p-3 text-left text-sm ${selected ? 'border-accent bg-raised' : 'border-subtle hover:border-accent'}`} aria-pressed={selected} onClick={onSelect}>
    <span className="flex items-baseline gap-2"><span className="min-w-0 flex-1 truncate font-medium">{item.name}</span><span className="shrink-0 text-xs text-secondary">{AVAILABILITY_LABELS[item.availability]}</span></span>
    <span className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-xs text-secondary"><span>{SOURCE_LABELS[item.source]}</span><span>{item.kind}</span><span>{formatBytes(item.byte_size)}</span>{item.output_slot && <span>{item.output_slot}</span>}</span>
  </button></li>
}

function FileFullContentPreview({
  active,
  client,
  workspaceId,
  sessionId,
  path,
}: {
  active: boolean
  client: ApiClient
  workspaceId: string
  sessionId: string
  path: string
}) {
  const key = inspectorResourceKey('workspace-file-content', workspaceId, sessionId, { path })
  const resource = useInspectorResource<WorkspaceFileReadWire>(
    key,
    () => client.workspaceFileContent(path),
    active,
  )
  if (resource.loading) {
    return <p role="status" className="rounded-[8px] border border-subtle bg-raised p-3 text-sm text-secondary">正在读取文件全文…</p>
  }
  if (resource.error) {
    return <p role="alert" className="rounded-[8px] border border-failed p-3 text-sm text-failed">{resource.error}</p>
  }
  if (!resource.data?.file) {
    return <p role="status" className="rounded-[8px] border border-subtle bg-raised p-3 text-sm text-secondary">暂无正文。</p>
  }
  const file = resource.data.file
  return <PreviewText text={file.text} label={`只读正文（${formatBytes(file.revision.size)}）`} truncated={false} />
}

function FileRow({ item, selected, onSelect }: { item: TaskFileChangeWire; selected: boolean; onSelect: () => void }) {
  return <li><button type="button" className={`w-full rounded-[8px] border p-3 text-left text-sm ${selected ? 'border-accent bg-raised' : 'border-subtle hover:border-accent'}`} aria-pressed={selected} onClick={onSelect}>
    <span className="block truncate font-mono text-xs">{item.path}</span>
    <span className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-xs text-secondary"><span>{item.operation}</span><span>{item.status}</span><span>{FILE_AVAILABILITY_LABELS[item.availability]}</span></span>
  </button></li>
}

function ResultSummary({ data }: { data: TaskArtifactsWire }) {
  const outcome = data.outcomes[data.outcomes.length - 1]
  if (outcome === undefined) return null
  return <section className="rounded-[10px] border border-subtle bg-raised p-4" aria-label="任务结果">
    <h3 className="text-xs font-medium tracking-wide text-secondary">任务结果</h3><p className="mt-2 whitespace-pre-wrap font-serif text-sm leading-relaxed">{outcomeSummary(String(outcome.summary ?? ''), String(outcome.task_status ?? data.status ?? ''))}</p>
    {Array.isArray(outcome.changed_paths) && outcome.changed_paths.length > 0 && <div className="mt-3"><h4 className="text-xs text-secondary">记录的变化路径</h4><ul className="mt-1 space-y-1 font-mono text-xs">{outcome.changed_paths.map(path => <li key={path}>{String(path)}</li>)}</ul></div>}
    {Array.isArray(outcome.unresolved_items) && outcome.unresolved_items.length > 0 && <div className="mt-3"><h4 className="text-xs text-secondary">未解决项</h4><ul className="mt-1 list-disc space-y-1 pl-5 text-xs text-secondary">{outcome.unresolved_items.map(item => <li key={item}>{String(item)}</li>)}</ul></div>}
  </section>
}

function SelectedPreview({ data, selected, active, client, workspaceId, sessionId, params }: { data: TaskArtifactsWire; selected: string | null; active: boolean; client: ApiClient; workspaceId: string; sessionId: string; params: ReturnType<typeof selectorParams> }) {
  const file = selected?.startsWith('file:') ? data.files.find(item => `file:${item.path}` === selected) : undefined
  const artifact = selected?.startsWith('artifact:') ? data.artifacts.find(item => `artifact:${item.artifact_id}` === selected) : undefined
  const [fileViewMode, setFileViewMode] = useState<'diff' | 'full'>('diff')
  if (file === undefined && artifact === undefined) return null
  const linked = file?.artifact_id ? data.artifacts.find(item => item.artifact_id === file.artifact_id) : artifact
  const hasDiff = Boolean(file?.diff)
  const activeMode = file ? ((hasDiff && fileViewMode === 'diff') ? 'diff' : (hasDiff ? fileViewMode : 'full')) : 'diff'
  return <section className="space-y-2" aria-label="正文预览">
    <div className="flex items-baseline justify-between gap-2">
      <h3 className="min-w-0 truncate text-sm font-medium">{file?.path ?? artifact?.name}</h3>
      <div className="flex shrink-0 items-center gap-2">
        {file && (
          <div className="flex items-center gap-1 rounded-[6px] border border-subtle bg-base p-0.5 text-xs" role="tablist" aria-label="查看模式">
            {hasDiff && (
              <button
                type="button"
                role="tab"
                aria-selected={activeMode === 'diff'}
                className={`rounded-[4px] px-2 py-0.5 transition-colors ${activeMode === 'diff' ? 'bg-raised font-medium text-primary shadow-xs' : 'text-secondary hover:text-primary'}`}
                onClick={() => setFileViewMode('diff')}
              >
                变更对比
              </button>
            )}
            <button
              type="button"
              role="tab"
              aria-selected={activeMode === 'full'}
              className={`rounded-[4px] px-2 py-0.5 transition-colors ${activeMode === 'full' ? 'bg-raised font-medium text-primary shadow-xs' : 'text-secondary hover:text-primary'}`}
              onClick={() => setFileViewMode('full')}
            >
              只读全文
            </button>
          </div>
        )}
        <span className="text-xs text-secondary">{file ? file.operation : artifact?.kind}</span>
      </div>
    </div>
    {file && activeMode === 'full' ? (
      <FileFullContentPreview active={active} client={client} workspaceId={workspaceId} sessionId={sessionId} path={file.path} />
    ) : (
      <ArtifactContentPreview active={active} client={client} workspaceId={workspaceId} sessionId={sessionId} params={params} artifact={linked} diff={file?.diff ?? artifact?.diff} diffTruncated={file?.diff_truncated ?? artifact?.diff_truncated} availability={file?.availability ?? artifact!.availability} omissionReason={file?.omission_reason ?? artifact?.omission_reason} contentEncoding={file?.content_encoding ?? artifact!.content_encoding} />
    )}
  </section>
}

export function TaskArtifactsInspector({ target, active, client, workspaceId, sessionId, currentTaskRunId }: InspectorBodyProps) {
  if (!client || !workspaceId || !sessionId) return <Unavailable message="任务产物需要在已选定的会话中查看。" />
  return <TaskArtifactsContent target={target} active={active} client={client} workspaceId={workspaceId} sessionId={sessionId} currentTaskRunId={currentTaskRunId} />
}

function TaskArtifactsContent({ target, active, client, workspaceId, sessionId, currentTaskRunId }: { target: InspectorBodyProps['target']; active: boolean; client: ApiClient; workspaceId: string; sessionId: string; currentTaskRunId: string | null | undefined }) {
  const params = selectorParams(target, currentTaskRunId)
  const key = inspectorResourceKey('task-artifacts', workspaceId, sessionId, params)
  const resource = useInspectorResource<TaskArtifactsWire>(key, () => client.taskArtifacts(workspaceId, sessionId, params), active)
  const memoryKey = scopeKey(workspaceId, sessionId, params)
  const [selected, setSelected] = useState<string | null>(() => selectionMemory.get(memoryKey) ?? null)
  useEffect(() => { setSelected(selectionMemory.get(memoryKey) ?? null) }, [memoryKey])
  const data = resource.data ?? EMPTY_ARTIFACTS
  useEffect(() => {
    if (resource.data && !selectionExists(resource.data, selected)) {
      const first = resource.data.files[0] ? `file:${resource.data.files[0].path}` : resource.data.artifacts[0] ? `artifact:${resource.data.artifacts[0].artifact_id}` : null
      setSelected(first)
      if (first) rememberSelection(memoryKey, first)
    }
  }, [memoryKey, resource.data, selected])
  const select = (value: string) => { setSelected(value); rememberSelection(memoryKey, value) }
  const message = data.message && !['此会话还没有可显示的任务产物。', '该历史任务没有登记产物或命令输出。'].includes(data.message)
    ? data.message
    : data.availability === 'partial' ? '部分产物不可用' : null
  const selectedArtifact = selected?.startsWith('artifact:') ? data.artifacts.find(item => `artifact:${item.artifact_id}` === selected) : undefined
  return <section className="flex h-full min-h-0 flex-col overflow-hidden" aria-label="任务产物" data-inspector-kind="artifacts">
    <header className="flex shrink-0 items-center justify-between gap-3 border-b border-subtle px-4 py-3"><div className="min-w-0"><h2 className="truncate text-sm font-medium">{data.title}</h2>{data.status && <p className="text-xs text-secondary">{statusLabel(data.status)}</p>}</div><button type="button" className={buttonClass} disabled={resource.loading} onClick={() => void resource.reload()}>刷新</button></header>
    {resource.error && <div className="shrink-0 px-4 pt-3"><p role="alert" className="text-sm text-failed">{resource.error}</p><button type="button" className={buttonClass} onClick={() => void resource.reload()}>重试</button></div>}
    {!resource.data && resource.loading && <p role="status" className="p-4 text-sm text-secondary">正在读取任务产物…</p>}
    {resource.data && <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4 pt-3"><div className="space-y-3">
      {message && <p role="status" className="rounded-[8px] border border-subtle bg-raised p-3 text-xs text-secondary">{message}</p>}
      <ResultSummary data={data} />
      {data.task && <TaskOutcomeActions client={client} task={data.task} onChanged={() => void resource.reload()} />}
      {(data.files.length > 0 || data.artifacts.length > 0) && <div className="grid gap-3 lg:grid-cols-[minmax(12rem,0.8fr)_minmax(0,1.2fr)]"><div className="space-y-3">
        {data.files.length > 0 && <section aria-label="文件变化"><h3 className="text-xs font-medium tracking-wide text-secondary">文件变化</h3><ul className="mt-2 space-y-2">{data.files.map(item => <FileRow key={item.path} item={item} selected={selected === `file:${item.path}`} onSelect={() => select(`file:${item.path}`)} />)}</ul></section>}
        {data.artifacts.length > 0 && <section aria-label="登记产物与报告"><h3 className="text-xs font-medium tracking-wide text-secondary">登记产物与报告</h3><ul className="mt-2 space-y-2">{data.artifacts.map(item => <ArtifactRow key={item.artifact_id} item={item} selected={selected === `artifact:${item.artifact_id}`} onSelect={() => select(`artifact:${item.artifact_id}`)} />)}</ul></section>}
      </div><div className="space-y-3"><SelectedPreview data={data} selected={selected} active={active} client={client} workspaceId={workspaceId} sessionId={sessionId} params={params} />{selectedArtifact && <RetentionActions client={client} item={selectedArtifact} onChanged={() => void resource.reload()} />}</div></div>}
      {data.files.length === 0 && data.artifacts.length === 0 && data.outcomes.length === 0 && !message && <p className="text-sm text-secondary">暂无产物</p>}
    </div></div>}
    {!resource.data && !resource.loading && !resource.error && <p role="status" className="p-4 text-sm text-secondary">暂时没有可显示的任务产物。</p>}
  </section>
}

function RetentionActions({ client, item, onChanged }: { client: ApiClient; item: TaskArtifactItemWire; onChanged: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const action = item.retention === 'pinned' ? 'release' : 'pin'
  const run = async () => {
    setBusy(true); setError(null)
    try { await client.artifactRetention(item.artifact_id, action, item.row_version, `cmd_artifact_${action}_${item.row_version}`); onChanged() }
    catch (reason) { setError(reason instanceof Error ? reason.message : '保留操作失败，请重试。') }
    finally { setBusy(false) }
  }
  return <div className="rounded-[8px] border border-subtle bg-raised p-3 text-xs"><div className="flex items-center justify-between gap-2"><span>{RETENTION_LABELS[item.retention] ?? '标准保留'}</span><button type="button" className={buttonClass} disabled={busy} onClick={() => void run()}>{busy ? '处理中…' : action === 'pin' ? '固定保留' : '释放固定'}</button></div>{error && <p role="alert" className="mt-2 text-failed">{error}</p>}</div>
}
