import { useEffect, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { TimelineItem } from '../api/chat'
import type { TaskResultFileWire, TaskResultWire } from '../api/types'
import type { FileTarget } from '../state/inspector'
import { CopyButton, Markdown } from './ChatMessage'
import { outcomeSummary } from './lib/outcomePresentation'
import { TASK_STATUS_LABELS } from './lib/labels'

/**
 * One durable TaskOutcome, rendered as the answer it actually produced.
 *
 * The result item already carried the Outcome identity; what was missing was
 * the readable body, the declared outputs and the files. Nothing here is
 * inferred from the current task: every field comes from the server-side
 * projection of that Outcome, and an old Core without the projection still
 * renders the existing entry point instead of a new capability switch.
 */

const ROLE_LABELS: Record<TaskResultFileWire["role"], string> = {
  delivery: "交付文件",
  output: "节点输出",
  generated: "生成文件",
  changed: "相关变更",
  related: "相关产物",
}

const AVAILABILITY_LABELS: Record<string, string> = {
  available: "可预览",
  staging: "发布中",
  missing: "正文缺失",
  corrupt: "正文损坏",
  unavailable: "暂不可用",
}

/** Status text for one result; a failed run never claims success. */
export function resultStatusLabel(result: TaskResultWire): string {
  if (result.task_status === "failed") return "未成功 · 失败"
  if (result.task_status === "cancelled") return "未成功 · 已取消"
  if (result.result_status === "needs_revision") return "需要修改"
  return TASK_STATUS_LABELS[result.task_status] ?? result.task_status
}

/**
 * Result file → one file-open target, or null when nothing can be opened.
 *
 * A recorded change path is the current workspace file; a registered Artifact
 * is immutable history. The two are never conflated: only an Artifact with a
 * trusted current path offers "打开当前文件".
 */
export function resultFileTarget(
  file: TaskResultFileWire,
  taskRunId: string,
): FileTarget | null {
  if (file.role === "changed") {
    return file.path === null ? null : { kind: "workspace", path: file.path }
  }
  if (file.artifact_id === null) {
    return file.path === null ? null : { kind: "workspace", path: file.path }
  }
  return {
    kind: "artifact",
    artifactId: file.artifact_id,
    label: file.label,
    path: file.path,
    taskRunId,
  }
}

/** 旧 Core 没有结果投影时的既有入口：仍是可点开的任务产物。 */
function LegacyResult({ item, onTask }: { item: TimelineItem; onTask: (id: string) => void }) {
  return (
    <article className="chat-message" data-kind="result" data-item-id={item.item_id}>
      <div className="message-meta"><strong>任务结果</strong></div>
      <p className="text-sm text-secondary">结果正文不可用。</p>
      {item.source.task_run_id && (
        <button type="button" className="editor-button" onClick={() => onTask(item.source.task_run_id!)}>
          查看任务与产物
        </button>
      )}
    </article>
  )
}

export function TaskResultMessage({
  item,
  client,
  onOpenFile,
  onTask,
}: {
  item: TimelineItem
  client: ApiClient
  onOpenFile?: (target: FileTarget) => void
  onTask: (id: string) => void
}) {
  const result = item.result ?? null
  const [full, setFull] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    setFull(null)
    setError(null)
  }, [item.item_id])
  if (result === null) return <LegacyResult item={item} onTask={onTask} />
  const body = full ?? result.body?.text ?? null
  const loadFull = () => {
    if (result.body_ref === null) return
    setLoading(true)
    setError(null)
    void client
      .chatContent(item.workspace_id, item.session_id, result.body_ref.record_id)
      .then(
        value => setFull(value.content),
        () => setError("完整结果读取失败，请重试。"),
      )
      .finally(() => setLoading(false))
  }
  return (
    <article className="chat-message chat-result" data-kind="result" data-item-id={item.item_id}>
      <div className="message-meta">
        <strong>任务结果</strong>
        <span className="text-xs text-secondary">{resultStatusLabel(result)}</span>
        {item.source.task_run_id && (
          <button className="message-task" onClick={() => onTask(item.source.task_run_id!)}>
            任务 {item.source.task_run_id.slice(-8)}
          </button>
        )}
      </div>
      {body !== null ? (
        <>
          <Markdown text={body} onOpenFile={onOpenFile} />
          {result.body_ref !== null && (
            <div className="mt-1 flex items-center gap-2 text-xs text-secondary">
              <span>{result.body!.content_complete ? "结果正文" : "结果正文（摘录）"}</span>
              <button type="button" className="editor-button" disabled={loading} onClick={loadFull}>
                {loading ? "加载中…" : "查看完整结果"}
              </button>
              <CopyButton text={body} label="复制结果" />
            </div>
          )}
        </>
      ) : (
        <p className="whitespace-pre-wrap font-serif text-sm leading-relaxed">{outcomeSummary(result.summary, result.task_status)}</p>
      )}
      {result.sections.map(section => (
        <section key={section.label} className="mt-2">
          <h4 className="text-xs text-secondary">{section.label}</h4>
          {section.kind === "text" ? (
            <p className="whitespace-pre-wrap text-sm">{section.items.join("\n")}</p>
          ) : (
            <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
              {section.items.map((line, index) => <li key={index}>{line}</li>)}
            </ul>
          )}
        </section>
      ))}
      {result.files.length > 0 && (
        <section className="mt-2" aria-label="结果文件">
          <h4 className="text-xs text-secondary">结果文件</h4>
          <ul className="mt-1 space-y-1">
            {result.files.map((file, index) => {
              const target = resultFileTarget(file, result.task_run_id)
              const disabled = target === null || onOpenFile === undefined
              return (
                <li key={`${file.artifact_id ?? file.path ?? index}`} className="flex flex-wrap items-baseline gap-2 text-sm">
                  <button
                    type="button"
                    className="chat-file-link"
                    disabled={disabled}
                    title={target === null ? "该结果没有可打开的文件正文" : "在右侧打开"}
                    onClick={() => { if (target !== null && onOpenFile) onOpenFile(target) }}
                  >
                    {file.label}
                  </button>
                  <span className="text-xs text-secondary">
                    {ROLE_LABELS[file.role]}
                    {" · "}
                    {AVAILABILITY_LABELS[file.availability] ?? file.availability}
                    {file.role === "delivery" && file.mime ? ` · ${file.mime}` : ""}
                    {file.role === "delivery" && file.resource_count
                      ? ` · ${file.resource_count} 个依赖`
                      : ""}
                    {file.note ? ` · ${file.note}` : ""}
                  </span>
                </li>
              )
            })}
          </ul>
        </section>
      )}
      {result.notes.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs text-secondary">
          {result.notes.map((note, index) => <li key={index}>{note}</li>)}
        </ul>
      )}
      {error && <p role="alert" className="text-sm text-failed">{error}</p>}
    </article>
  )
}
