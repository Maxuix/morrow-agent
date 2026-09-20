import { HistoryAttachments } from './AttachmentPreview'
import { useEffect, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { TimelineItem } from '../api/chat'
import type { FileTarget } from '../state/inspector'
import { MarkdownBody } from './lib/MarkdownBody'
import { CopyButton } from './lib/markdownParts'
import { TaskResultMessage } from './TaskResultMessage'

export { CopyButton, FileLink, flattenText, safeLink } from './lib/markdownParts'

/** The one Markdown entry point for chat, results and documents. */
export function Markdown({
  text,
  onOpenFile,
}: {
  text: string
  onOpenFile?: (target: FileTarget) => void
}) {
  return <MarkdownBody text={text} onOpenFile={onOpenFile} />
}

export function ChatMessage({item, client, onTask, onOpenFile, expanded, onExpand}: {item: TimelineItem; client: ApiClient; onTask: (id: string) => void; onOpenFile?: (target: FileTarget) => void; expanded: boolean; onExpand: () => void}) {
  const [body, setBody] = useState<string | null>(null); const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  useEffect(() => {if(!expanded)setBody(null)}, [expanded])
  // A result is its own component: it renders the Outcome body, outputs and
  // files instead of the generic mechanism line.
  if (item.kind === 'result') return <TaskResultMessage item={item} client={client} onOpenFile={onOpenFile} onTask={onTask} />
  const isUser = item.kind === 'user_message'
  const message = isUser || item.kind === 'assistant_message'
  const content = typeof item.content === 'string' ? item.content : body
  const fact = typeof item.content === 'object' && item.content !== null ? item.content : null
  return <article data-item-id={item.item_id} data-kind={item.kind} className={`chat-message ${isUser ? 'chat-user' : ''}`}>
    {!isUser && <div className="message-meta"><strong>{item.kind === 'assistant_message' ? 'Morrow' : item.kind === 'tool_activity' ? '工具活动' : item.kind === 'interruption' ? '运行中断' : '轮次结束'}</strong>
      {item.source.task_run_id && <button className="message-task" onClick={() => onTask(item.source.task_run_id!)}>任务 {item.source.task_run_id.slice(-8)}</button>}
      {item.kind === 'assistant_message' && content && <span className="message-copy"><CopyButton text={content}/></span>}
    </div>}
    {message && content !== null && (item.kind === 'assistant_message' ? <Markdown text={content} onOpenFile={onOpenFile}/> : <p className="whitespace-pre-wrap break-words">{content}</p>)}
    {message && content === null && item.content_ref && <button className="editor-button" disabled={loading} onClick={() => {
      onExpand(); setLoading(true)
      void client.chatContent(item.workspace_id, item.session_id, item.source.record_id ?? item.content_ref!.split('/').pop()!).then(value => setBody(value.content), () => setError('正文加载失败，请重试')).finally(() => setLoading(false))
    }}>{loading ? '加载正文…' : '加载完整正文'}</button>}
    {!!item.attachments?.length&&<HistoryAttachments client={client} workspace={item.workspace_id} session={item.session_id} ids={item.attachments.map(a=>a.attachment_id)}/>}
    {fact && <p className="text-sm">{[fact.tool_name, fact.status, fact.disposition, fact.finish_reason, fact.stop_code].filter(Boolean).join(' · ')}
      {fact.task_outcome && <span> · 任务：{fact.task_outcome.task_status}</span>}</p>}
    {item.kind === 'interruption' && <p className="mt-1 text-xs text-secondary">回复中断。</p>}
    {error && <p role="alert">{error}</p>}
  </article>
}
