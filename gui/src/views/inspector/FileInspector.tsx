import { Suspense, lazy, useEffect, useMemo, useState, useSyncExternalStore } from "react"
import type { ApiClient } from "../../api/client"
import type { TaskArtifactContentWire } from "../../api/types"
import { inspectorResourceKey, useInspectorResource } from "../../state/inspectorResources"
// 工作区文件不走 useInspectorResource：编辑缓冲区自己持有信息、基线版本与草稿。
import type { FileTarget } from "../../state/inspector"
import {
  FileBufferStore,
  type FileBuffer,
  type FileScope,
} from "../../state/fileBuffer"
import type { DirtyGuard } from "../../state/navigation"
import { MarkdownBody } from "../lib/MarkdownBody"
import { buttonClass } from "../management/styles"
import {
  BinaryPreview,
  exceedsPixelBudget,
} from "./FilePreview"
import type { InspectorBodyProps } from "./InspectorBodyProps"
// 编辑器与只读查看器都是按需 chunk：打开文本文件时才加载 CodeMirror。

const SourceView = lazy(() =>
  import("./SourceView").then(module => ({ default: module.SourceView })),
)

/** 单文件只读预览一次渲染的最大行数；超出时明确标注截断。 */
const MAX_RENDERED_LINES = 2000

/** Directory of a viewed document; its relative links resolve there, never at the root. */
function documentDirectory(path: string | null | undefined): string | undefined {
  if (path === null || path === undefined || !path.includes("/")) return undefined
  return path.slice(0, path.lastIndexOf("/"))
}

function selectorParams(target: InspectorBodyProps["target"]) {
  return {
    task_run_id: target?.taskRunId,
    workflow_run_id: target?.workflowRunId,
    node_run_id: target?.nodeRunId,
  }
}

function Unavailable({ message }: { message: string }) {
  return (
    <section className="flex h-full flex-col overflow-y-auto" aria-label="文件" data-inspector-kind="file">
      <h2 className="px-4 pt-4 pb-2 text-xs font-medium tracking-wide text-secondary">文件</h2>
      <p className="px-4 pb-4 text-sm text-secondary">{message}</p>
    </section>
  )
}

/** 带行号的只读正文；行元素保留 data-line 以支持行号定位。 */
export function SourceLines({
  text,
  line,
}: {
  text: string
  line?: number
}) {
  const lines = text.split("\n")
  const shown = lines.slice(0, MAX_RENDERED_LINES)
  const [scrollRef, setScrollRef] = useState<HTMLDivElement | null>(null)
  useEffect(() => {
    if (line === undefined || scrollRef === null) return
    const node = scrollRef.querySelector<HTMLElement>(`[data-line="${line}"]`)
    // jsdom has no layout engine; the guard keeps the scroll a pure enhancement.
    if (typeof node?.scrollIntoView === "function") node.scrollIntoView({ block: "center" })
  }, [line, text, scrollRef])
  return (
    <div ref={setScrollRef} className="max-h-[28rem] overflow-auto rounded-[8px] border border-subtle bg-base">
      <ol className="min-w-full font-mono text-xs leading-relaxed">
        {shown.map((value, index) => (
          <li
            key={index}
            data-line={index + 1}
            className={index + 1 === line ? "bg-raised" : undefined}
          >
            <span className="inline-block w-12 shrink-0 select-none pr-2 text-right text-secondary">
              {index + 1}
            </span>
            <span className="whitespace-pre-wrap break-words">{value}</span>
          </li>
        ))}
      </ol>
      {lines.length > shown.length && (
        <p role="status" className="border-t border-subtle p-2 text-xs text-secondary">
          仅显示前 {MAX_RENDERED_LINES} 行（共 {lines.length} 行）。
        </p>
      )}
    </div>
  )
}

/** 文件面板只读：Markdown 排版，其余文本直接展示高亮源码。 */
function WorkspaceTextBody({ buffer, line, onOpenFile }: {
  buffer: FileBuffer
  line?: number
  onOpenFile?: (target: FileTarget) => void
}) {
  if (buffer.loading && buffer.loadedRevision === null) {
    return <p role="status">正在读取正文…</p>
  }
  return /\.(md|markdown)$/i.test(buffer.path) ? (
    <div className="min-h-0 flex-1 overflow-auto px-4 py-3">
      <MarkdownBody text={buffer.loadedText} onOpenFile={onOpenFile}
        basePath={documentDirectory(buffer.path)} />
    </div>
  ) : (
    <Suspense fallback={<p role="status">正在加载源码…</p>}>
      <SourceView value={buffer.loadedText} path={buffer.path} line={line} />
    </Suspense>
  )
}

/** 历史 Artifact 的只读正文；有可信当前路径时才提供“打开当前文件”。 */
function ArtifactBody({
  active,
  client,
  workspaceId,
  sessionId,
  artifactId,
  params,
  onOpenFile,
}: {
  active: boolean
  client: ApiClient
  workspaceId: string
  sessionId: string
  artifactId: string
  params: ReturnType<typeof selectorParams>
  onOpenFile?: (target: FileTarget) => void
}) {
  const resource = useInspectorResource<TaskArtifactContentWire>(
    inspectorResourceKey("task-artifact-content", workspaceId, sessionId, {
      artifact_id: artifactId,
      ...params,
    }),
    () => client.taskArtifactContent(workspaceId, sessionId, artifactId, params),
    active,
  )
  const [imageTooLarge, setImageTooLarge] = useState(false)
  const isMarkdown =
    resource.data?.preview === "text" && /\.(md|markdown)$/i.test(resource.data.name)
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      {resource.loading && resource.data === null && (
        <p role="status" className="text-sm text-secondary">正在读取产物正文…</p>
      )}
      {resource.error && (
        <div className="space-y-2">
          <p role="alert" className="text-sm text-failed">{resource.error}</p>
          <button type="button" className={buttonClass} onClick={() => void resource.reload()}>重试</button>
        </div>
      )}

      {resource.data !== null &&
        resource.data.content_kind === "text" &&
        !isMarkdown && (
          <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
            <Suspense
              fallback={<p role="status" className="text-sm text-secondary">正在加载只读查看器…</p>}
            >
              <SourceView
                value={resource.data.content ?? ""}
                path={resource.data.path ?? resource.data.name}
              />
            </Suspense>
          </div>
        )}
      {resource.data !== null && isMarkdown && (
        <div className="min-h-0 flex-1 overflow-auto px-4 py-3">
          <MarkdownBody
          text={resource.data.content ?? ""}
          onOpenFile={onOpenFile}
            basePath={documentDirectory(resource.data.path ?? resource.data.name)}
            historical
          />
        </div>
      )}
      {resource.data !== null &&
        resource.data.content_kind !== "text" &&
        (resource.data.preview === "image" || resource.data.preview === "pdf") &&
        (client.tokenless ? (
          resource.data.preview === "image" ? (
            !imageTooLarge ? (
              <img
                src={client.taskArtifactDownloadUrl(workspaceId, sessionId, artifactId, params)}
                alt={resource.data.name}
                data-artifact-preview="image"
                onLoad={event => {
                  const node = event.currentTarget
                  if (exceedsPixelBudget(node.naturalWidth, node.naturalHeight)) {
                    setImageTooLarge(true)
                  }
                }}
                style={{ maxWidth: "100%", flex: "1 1 auto", minHeight: 0, objectFit: "contain" }}
              />
            ) : (
              <p role="status" className="text-sm text-secondary">
                图片过大，请下载查看。
              </p>
            )
          ) : (
            <iframe
              title="PDF 预览"
              data-artifact-preview="pdf"
              src={client.taskArtifactDownloadUrl(workspaceId, sessionId, artifactId, params)}
              style={{ width: "100%", flex: "1 1 auto", minHeight: 0, border: "1px solid var(--border-subtle)", borderRadius: "var(--radius-sm)" }}
            />
          )
        ) : (
          <p role="status" className="text-sm text-secondary">
            当前无法预览，请下载查看。
          </p>
        ))}
      {resource.data !== null && resource.data.preview === "binary" && (
        <p role="status" className="text-sm text-secondary">此文件暂不支持文本预览。</p>
      )}
      {resource.data?.truncated === true && (
        <p role="status" className="text-xs text-secondary">内容已截断。</p>
      )}
    </div>
  )
}

/** 离开确认：保存 / 放弃 / 取消。保存失败时留在原文件，绝不静默丢草稿。 */
function LeaveConfirm({
  paths,
  busy,
  error,
  onSave,
  onDiscard,
  onCancel,
}: {
  paths: string[]
  busy: boolean
  error: string | null
  onSave: () => void
  onDiscard: () => void
  onCancel: () => void
}) {
  return (
    <div
      role="dialog"
      aria-label="未保存的文件"
      className="space-y-2 rounded-[8px] border border-subtle bg-raised p-3"
    >
      <p className="text-sm">有未保存的文件草稿：</p>
      <ul className="list-disc pl-5 text-xs text-secondary">
        {paths.slice(0, 5).map(path => <li key={path} className="break-all">{path}</li>)}
        {paths.length > 5 && <li>以及另外 {paths.length - 5} 个文件</li>}
      </ul>
      {error !== null && <p role="alert" className="text-xs text-failed">{error}</p>}
      <div className="flex flex-wrap gap-2">
        <button type="button" className={buttonClass} disabled={busy} onClick={onSave}>
          {busy ? "保存中…" : "保存并继续"}
        </button>
        <button type="button" className={buttonClass} disabled={busy} onClick={onDiscard}>
          放弃草稿并继续
        </button>
        <button type="button" className={buttonClass} disabled={busy} onClick={onCancel}>
          取消
        </button>
      </div>
    </div>
  )
}

/** 一次只显示当前选中的一个文件；编辑缓冲区按面板存活期保留。 */
function FilePanel({
  target,
  active,
  client,
  workspaceId,
  sessionId,
  onOpenFile,
  fileBuffers,
  inspectorStore,
  registerGuard,
}: InspectorBodyProps & { client: ApiClient; workspaceId: string; sessionId: string }) {
  const path = target?.path
  const artifactId = target?.artifactId
  const scope = useMemo<FileScope>(() => ({ workspaceId, sessionId }), [workspaceId, sessionId])
  const subscription = fileBuffers?.subscribe
  const version = useSyncExternalStore(
    subscription ?? (() => () => {}),
    fileBuffers?.getVersion ?? (() => 0),
  )
  void version
  // 先按完整候选路径读取真实文件；只有它确实不存在时，才把 “:行号” 后缀
  // 解释掉再试一次。文件名本身带冒号时不会被误当成行号。
  const [candidate, setCandidate] = useState(path)
  useEffect(() => {
    setCandidate(path)
  }, [path, workspaceId, sessionId])
  const buffer = candidate !== undefined && fileBuffers ? fileBuffers.buffer(scope, candidate) : null

  const [pending, setPending] = useState<{ resolve: (allowed: boolean) => void } | null>(null)
  const [leaveBusy, setLeaveBusy] = useState(false)
  const [leaveError, setLeaveError] = useState<string | null>(null)

  useEffect(() => {
    if (!active || artifactId !== undefined || candidate === undefined || fileBuffers === undefined) return
    void fileBuffers.load(scope, candidate)
  }, [active, artifactId, candidate, scope, fileBuffers])

  const canFallback =
    target?.fallbackPath !== undefined && target.fallbackPath !== "" && candidate !== target.fallbackPath
  useEffect(() => {
    if (!canFallback || target?.fallbackPath === undefined || buffer === null) return
    if (buffer.loadError !== null && !buffer.loading) setCandidate(target.fallbackPath)
  }, [canFallback, buffer, target?.fallbackPath])

  const requestLeave = useMemo(() => {
    return () => {
      if (!fileBuffers || !fileBuffers.hasDirty()) return Promise.resolve(true)
      return new Promise<boolean>(resolve => {
        setLeaveError(null)
        setPending({ resolve })
      })
    }
  }, [fileBuffers])

  useEffect(() => {
    if (!inspectorStore) return
    return inspectorStore.setLeaveConfirm(requestLeave)
  }, [inspectorStore, requestLeave])

  useEffect(() => {
    if (!registerGuard || !fileBuffers) return
    const guard: DirtyGuard = {
      isDirty: () => fileBuffers.hasDirty(),
      confirmLeave: requestLeave,
    }
    return registerGuard(guard)
  }, [registerGuard, fileBuffers, requestLeave])

  const settle = (allowed: boolean) => {
    const target_ = pending
    setPending(null)
    setLeaveError(null)
    target_?.resolve(allowed)
  }

  const saveAll = async () => {
    if (!fileBuffers) return
    setLeaveBusy(true)
    try {
      const dirty = fileBuffers.dirtyBuffers()
      for (const item of dirty) {
        await fileBuffers.save({ workspaceId: item.workspaceId, sessionId: item.sessionId }, item.path)
      }
      const remaining = fileBuffers.dirtyBuffers()
      if (remaining.length > 0) {
        setLeaveError(`仍有未保存或未核对的草稿：${remaining.map(item => item.path).join("、")}`)
        return
      }
      settle(true)
    } finally {
      setLeaveBusy(false)
    }
  }

  const discardAll = () => {
    if (fileBuffers) {
      for (const item of fileBuffers.dirtyBuffers()) {
        fileBuffers.discardDraft({ workspaceId: item.workspaceId, sessionId: item.sessionId }, item.path)
      }
    }
    settle(true)
  }

  if (artifactId !== undefined) {
    return (
      <section className="flex h-full min-h-0 flex-col overflow-hidden" aria-label="文件" data-inspector-kind="file">
        <header className="shrink-0 overflow-x-auto whitespace-nowrap border-b border-subtle px-4 py-2 font-mono text-xs text-secondary" title={path ?? target?.label ?? artifactId}>
          {path ?? target?.label ?? artifactId}
        </header>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          <ArtifactBody
            active={active}
            client={client}
            workspaceId={workspaceId}
            sessionId={sessionId}
            artifactId={artifactId}
            params={selectorParams(target)}
            onOpenFile={onOpenFile}
          />
        </div>
      </section>
    )
  }

  const file = buffer?.info ?? null
  const displayPath = buffer?.path ?? path ?? ""
  return (
    <section className="flex h-full min-h-0 flex-col overflow-hidden" aria-label="文件" data-inspector-kind="file">
      <header className="shrink-0 overflow-x-auto whitespace-nowrap border-b border-subtle px-4 py-2 font-mono text-xs text-secondary" title={displayPath}>{displayPath}</header>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        {pending !== null && fileBuffers && (
          <LeaveConfirm
            paths={fileBuffers.dirtyBuffers().map(item => item.path)}
            busy={leaveBusy}
            error={leaveError}
            onSave={() => void saveAll()}
            onDiscard={discardAll}
            onCancel={() => settle(false)}
          />
        )}
        {!fileBuffers && (
          <p role="status" className="text-sm text-secondary">当前无法编辑。</p>
        )}
        {fileBuffers && buffer !== null && buffer.loadError !== null && !buffer.loading && (
          <div className="space-y-2">
            <p role="alert" className="text-sm text-failed">{buffer.loadError}</p>
            <button type="button" className={buttonClass} onClick={() => void fileBuffers.load(scope, buffer.path)}>
              重试
            </button>
          </div>
        )}
        {fileBuffers && buffer !== null && buffer.loadError === null && (
          file === null ? (
            <p role="status" className="text-sm text-secondary">正在读取文件…</p>
          ) : file.text ? (
            <WorkspaceTextBody
              buffer={buffer}
              line={target?.line}
              onOpenFile={onOpenFile}
            />
          ) : (
            <BinaryPreview client={client} info={file} path={buffer.path} />
          )
        )}
      </div>
    </section>
  )
}

/**
 * “文件”标签：一次只显示当前选中的一个文件。定位来自 InspectorTarget，
 * 加载按 workspace/session/target 的键隔离，快速切换或切会话时旧响应不会
 * 回填。当前工作区与历史 Artifact 均只读展示。
 */
export function FileInspector(props: InspectorBodyProps) {
  const { client, workspaceId, sessionId, target, fileBuffers } = props
  // 外壳未提供共享缓冲区时（例如独立挂载本面板）自建一份，行为保持一致：
  // 草稿仍然只活在这个挂载期内。
  const [ownBuffers] = useState(() =>
    fileBuffers === undefined && client !== undefined ? new FileBufferStore(client) : null,
  )
  const buffers = fileBuffers ?? ownBuffers ?? undefined
  if (!client || !workspaceId || !sessionId) {
    return <Unavailable message="文件需要在已选定的会话中查看。" />
  }
  if (target?.path === undefined && target?.artifactId === undefined) {
    return <Unavailable message="未选择文件。" />
  }
  return (
    <FilePanel
      {...props}
      fileBuffers={buffers}
      client={client}
      workspaceId={workspaceId}
      sessionId={sessionId}
    />
  )
}
