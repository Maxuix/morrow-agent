import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { ApiClient } from '../api/client'
import type { ActivityItem } from '../api/activity'
import type { ActivityContentState } from '../state/activity'
import { activityRunningLabel, durationText, toolCopy } from '../state/activity'
import { CopyButton } from './ChatMessage'

/**
 * Row-level pieces of the execution region. Every component takes explicit
 * props (no hidden stores) so collapsed/expanded states are renderable and
 * testable statically; the region owns collapse memory and the clock.
 */

const ICON_PATHS: Record<string, ReactNode> = {
  file: <><path d="M4 1.5h5l3.5 3.5v9.5h-8.5z"/><path d="M9 1.5v3.5h3.5"/></>,
  search: <><circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5L14 14"/></>,
  list: <><path d="M5.5 4h8M5.5 8h8M5.5 12h8"/><path d="M2.5 4h.01M2.5 8h.01M2.5 12h.01"/></>,
  terminal: <><path d="M2.5 4.5l3.5 3.5-3.5 3.5"/><path d="M8 12.5h5.5"/></>,
  edit: <><path d="M11.2 2.3l2.5 2.5-8 8L2.5 13.5l.7-3.2z"/><path d="M9.5 4l2.5 2.5"/></>,
  tool: <><path d="M8 1.8l5.4 3.1v6.2L8 14.2l-5.4-3.1V4.9z"/><path d="M8 5.5v3"/></>,
  thought: <><path d="M8 1.8l1.4 4.3 4.3 1.4-4.3 1.4L8 13.2l-1.4-4.3-4.3-1.4 4.3-1.4z"/></>,
  shield: <><path d="M8 1.5l5.5 2v4.2c0 3.3-2.4 5.7-5.5 6.8-3.1-1.1-5.5-3.5-5.5-6.8V3.5z"/><path d="M5.8 8l1.6 1.6L10.5 6.5"/></>,
  retry: <><path d="M13.2 8A5.2 5.2 0 1 1 11.6 4.3"/><path d="M13.5 1.8v3h-3"/></>,
  image: <><rect x="2" y="3" width="12" height="10" rx="1.5"/><circle cx="5.5" cy="6.5" r="1"/><path d="M2 11.5l3.5-3.5 2.5 2.5 2-2 4 3.5"/></>,
  chevron: <><path d="M4 6l4 4 4-4"/></>,
  node: <><circle cx="4" cy="8" r="2"/><circle cx="12" cy="4" r="2"/><circle cx="12" cy="12" r="2"/><path d="M6 7l4-2.2M6 9l4 2.2"/></>,
  warn: <><path d="M8 2.2L14.4 13.5H1.6z"/><path d="M8 6.5v3M8 11.8h.01"/></>,
}

/** 16px semantic icon; decorative only — accessible meaning lives in the row text. */
export function ActivityIcon({name, className}: {name: string; className?: string}) {
  return <svg className={className ?? 'exec-icon'} width="16" height="16" viewBox="0 0 16 16" fill="none"
    stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {ICON_PATHS[name] ?? ICON_PATHS.tool}
  </svg>
}

/** Live model-stage line ("正在思考 · 12 秒"); finished stage markers fold away. */
export function StageLine({item, now}: {item: ActivityItem; now: number}) {
  const span = durationText(item.started_at, item.ended_at ?? null, now)
  return <div className="exec-row exec-row-static" role="status">
    <ActivityIcon name="thought"/>
    <span className="exec-row-label">{activityRunningLabel(item)}</span>
    <span className="exec-row-meta" aria-hidden="true">{span}</span>
  </div>
}

export function RetryLine({item, now}: {item: ActivityItem; now: number}) {
  const payload = item.payload.kind === 'retry' ? item.payload : null
  const delay = payload?.retry_delay_seconds
  const span = durationText(item.started_at, item.ended_at ?? null, now)
  return <div className="exec-row exec-row-static">
    <ActivityIcon name="retry"/>
    <span className="exec-row-label">第 {payload?.attempt_ordinal ?? '?'} 次尝试{typeof delay === 'number' ? ` · 约 ${Math.round(delay)} 秒后重试` : ''}</span>
    <span className="exec-row-meta" aria-hidden="true">{span}</span>
  </div>
}

export function ApprovalLine({item}: {item: ActivityItem}) {
  const payload = item.payload.kind === 'approval' ? item.payload : null
  const text = item.state === 'waiting' ? '等待批准'
    : payload?.decision === 'approved' ? '已批准'
    : payload?.decision === 'denied' ? '已拒绝' : '审批已收束'
  const tone = item.state === 'waiting' ? 'attention' : item.payload.kind === 'approval' && payload?.decision === 'denied' ? 'failed' : 'normal'
  return <div className="exec-row exec-row-static" data-tone={tone}>
    <ActivityIcon name="shield"/>
    <span className="exec-row-label">{text} · {item.safe_title}</span>
  </div>
}

/** Control receipts, compaction and node-output markers: one factual line each. */
export function MarkerLine({item, now}: {item: ActivityItem; now: number}) {
  const span = durationText(item.started_at, item.ended_at ?? null, now)
  return <div className="exec-row exec-row-static">
    <ActivityIcon name="tool"/>
    <span className="exec-row-label">{item.safe_title}</span>
    <span className="exec-row-meta" aria-hidden="true">{span}</span>
  </div>
}

/**
 * One tool call: semantic icon, human action summary, right-aligned
 * status/duration, expandable detail. preparing→running→terminal upserts of
 * the same call render as this single row (shared activity_id).
 */
export function ToolRow({item, content, now, open, onToggle, client}: {
  item: ActivityItem; content?: ActivityContentState; now: number; open: boolean; onToggle: () => void
  client?: ApiClient
}) {
  const copy = toolCopy(item)
  const failed = item.state === 'failed'
  const label = item.state === 'failed' ? copy.failedLabel
    : item.state === 'waiting' ? `等待批准 · ${copy.rowLabel}`
    : item.state === 'preparing' ? '正在准备调用'
    : item.state === 'running' ? copy.runningLabel
    : item.state === 'cancelled' ? `已取消 · ${copy.rowLabel}`
    : item.state === 'skipped' ? `已跳过 · ${copy.rowLabel}`
    : copy.rowLabel
  const span = durationText(item.started_at, item.ended_at, now)
  return <li className="exec-row-wrap">
    <button type="button" className="exec-row" aria-expanded={open} onClick={onToggle}
      data-state={failed ? 'failed' : item.state === 'running' || item.state === 'preparing' ? 'running' : undefined}>
      <ActivityIcon name={copy.icon}/>
      <span className="exec-row-label">{label}</span>
      <span className="exec-row-meta">
        {failed && <><ActivityIcon name="warn" className="exec-icon exec-icon-failed"/><span className="exec-tag-failed">失败</span></>}
        <span aria-hidden="true">{span}</span>
      </span>
      <ActivityIcon name="chevron" className="exec-icon exec-chev"/>
    </button>
    {open && <ToolDetail item={item} content={content} client={client}/>}
  </li>
}

/** Detail under one tool row: safe facts, streamed output, availability notes. */
export function ToolDetail({item, content, client}: {item: ActivityItem; content?: ActivityContentState; client?: ApiClient}) {
  const payload = item.payload.kind === 'tool' ? item.payload : null
  const failed = item.state === 'failed'
  // After recovery the transient map is empty; durable content is fetched
  // through the JSON content_ref when the server kept it.
  const [durable, setDurable] = useState<{ref: string; text: string; truncated: boolean} | null>(null)
  useEffect(() => {
    if (content?.text || !item.content_ref || !client) return
    let alive = true
    const ref = item.content_ref
    client.activityContent(ref).then(
      result => { if (alive) setDurable({ref, text: result.content, truncated: result.truncated}) },
      () => { if (alive) setDurable(null) },
    )
    return () => { alive = false }
  }, [client, item.content_ref, content?.text])
  const resolved = durable?.ref === item.content_ref ? durable : null
  const outputText = content?.text || resolved?.text || ''
  const outputTruncated = content?.text ? content.truncated : resolved?.truncated ?? false
  const validationAction = failed && payload?.validation_reason === 'empty_submission'
    ? '节点未提交有效结果'
    : null
  return <div className="exec-detail">
    <dl className="exec-detail-facts">
      <dt>工具</dt><dd>{payload?.tool_name ?? item.safe_title}</dd>
      {payload?.ordinal != null && <><dt>序号</dt><dd>第 {payload.ordinal}{payload.total ? ` / ${payload.total}` : ''} 个调用</dd></>}
      {payload?.exit_code != null && <><dt>退出码</dt><dd>{payload.exit_code}</dd></>}
      {failed && payload?.error_code && <><dt>错误码</dt><dd>{payload.error_code}</dd></>}
      {failed && payload?.validation_reason && <><dt>校验原因</dt><dd>{payload.validation_reason}</dd></>}
      {failed && payload?.validation_path && <><dt>字段位置</dt><dd>{payload.validation_path}</dd></>}
      {item.safe_summary && <><dt>摘要</dt><dd>{item.safe_summary}</dd></>}
    </dl>
    {validationAction && <p className="exec-note">{validationAction}</p>}
    {outputText && <div className="exec-output">
      <div className="exec-output-head">
        <span>输出{outputTruncated ? '（超出显示上限，仅保留前 32 KiB）' : ''}</span>
        <CopyButton text={outputText} label="复制输出"/>
      </div>
      <pre>{outputText}</pre>
    </div>}
    {item.truncated && <p className="exec-note">内容超出显示上限</p>}
    {item.availability === 'unsaved' && <p className="exec-note">输出未保存</p>}
    {item.availability === 'evicted' && <p className="exec-note">输出已过期</p>}
    {failed && payload?.exit_code == null && !outputText && !payload?.error_code && <p className="exec-note">失败输出未保存</p>}
  </div>
}

/** Minimal fence-aware paragraph rendering: prose wraps, code stays mono. */
function ProseText({text}: {text: string}) {
  const segments = text.split(/```[a-z]*\n?/i)
  return <>
    {segments.map((segment, index) => index % 2 === 1
      ? <pre key={index} className="exec-thinking-code">{segment.replace(/\n$/, '')}</pre>
      : segment.split(/\n{2,}/).filter(Boolean).map((paragraph, p) => <p key={`${index}-${p}`}>{paragraph}</p>))}
  </>
}

/**
 * Model-provided thinking summary: its own weak-colour block, prose layout
 * (never a bare <pre>), short preview with 展开全文 when collapsed.
 */
export function ThinkingBlock({content, open, onToggle}: {
  content?: ActivityContentState; open: boolean; onToggle: () => void
}) {
  const text = content?.text ?? ''
  if (!text) return null
  const previewLimit = 160
  const clipped = !open && text.length > previewLimit
  return <div className="exec-thinking">
    <button type="button" className="exec-thinking-head" aria-expanded={open} onClick={onToggle}>
      <ActivityIcon name="thought"/>
      <span>思考摘要</span>
      <ActivityIcon name="chevron" className="exec-icon exec-chev"/>
    </button>
    <div className="exec-thinking-body">
      <ProseText text={clipped ? `${text.slice(0, previewLimit)}…` : text}/>
      {clipped && <button type="button" className="exec-link" onClick={onToggle}>展开全文</button>}
      {content?.truncated && <p className="exec-note">思考内容超出显示上限，仅保留前 32 KiB</p>}
    </div>
  </div>
}

interface AssetGroupProps {
  items: ActivityItem[]
  client: ApiClient
  open: boolean
  onToggle: () => void
}

/**
 * Verifiable asset references (preview_ref) as one collapsible group with a
 * thumbnail grid. Labels come from the producing tool's real action; images
 * render inline, anything else (or a failure) degrades to a clear placeholder.
 */
export function AssetGroup({items, client, open, onToggle}: AssetGroupProps) {
  if (!items.length) return null
  const verbs = new Set(items.map(item => toolCopy(item).rowLabel.split(' ')[0]))
  const label = verbs.size === 1 ? `${[...verbs][0]} ${items.length} 个产物` : `产物引用 · ${items.length} 项`
  return <div className="exec-assets-group">
    <button type="button" className="exec-assets-head" aria-expanded={open} onClick={onToggle}>
      <ActivityIcon name="image"/>
      <span>{label}</span>
      <ActivityIcon name="chevron" className="exec-icon exec-chev"/>
    </button>
    {open && <div className="exec-assets">
      {items.map(item => <AssetThumb key={item.activity_id} item={item} client={client}/>)}
    </div>}
  </div>
}

type ThumbState = {kind: 'loading'} | {kind: 'image'; url: string} | {kind: 'file'; url: string} | {kind: 'error'}

function AssetThumb({item, client}: {item: ActivityItem; client: ApiClient}) {
  const [state, setState] = useState<ThumbState>({kind: 'loading'})
  useEffect(() => {
    if (!item.preview_ref) return
    let alive = true
    let url: string | null = null
    // preview_ref addresses a binary artifact; the raw variant serves the
    // original bytes instead of the default JSON text preview.
    void client.fetchBlob(`${item.preview_ref}?raw=1`).then(blob => {
      if (!alive) return
      url = URL.createObjectURL(blob)
      setState(blob.type.startsWith('image/') ? {kind: 'image', url} : {kind: 'file', url})
    }, () => { if (alive) setState({kind: 'error'}) })
    return () => { alive = false; if (url) URL.revokeObjectURL(url) }
  }, [client, item.preview_ref])
  const caption = item.safe_title
  return <figure className="exec-asset" title={caption}>
    <button type="button" className="exec-asset-frame" disabled={state.kind === 'loading' || state.kind === 'error'}
      aria-label={`${caption}（在预览中打开）`}
      onClick={() => { if (state.kind === 'image' || state.kind === 'file') window.open(state.url, '_blank', 'noopener') }}>
      {state.kind === 'loading' && <span className="exec-asset-placeholder">加载中…</span>}
      {state.kind === 'error' && <span className="exec-asset-placeholder"><ActivityIcon name="warn"/>加载失败</span>}
      {state.kind === 'image' && <img src={state.url} alt={caption} loading="lazy"/>}
      {state.kind === 'file' && <span className="exec-asset-placeholder"><ActivityIcon name="file"/>产物文件</span>}
    </button>
    <figcaption>{caption}</figcaption>
  </figure>
}

/** Node-level steering input; opened from the node row's action entry. */
export function SteerInput({identity, onSteer}: {
  identity: ActivityItem['identity']; onSteer: (identity: ActivityItem['identity'], text: string) => Promise<string>
}) {
  const [text, setText] = useState('')
  const [receipt, setReceipt] = useState<string | null>(null)
  const busy = useRef(false)
  const send = async () => {
    const trimmed = text.trim()
    if (!trimmed || busy.current) return
    busy.current = true
    setText('')
    try {
      const result = await onSteer(identity, trimmed)
      setReceipt(result === 'accepted' ? '已接纳，将在当前动作完成后生效' : '原请求回执')
    } catch {
      setReceipt('发送失败，请重试')
    } finally {
      busy.current = false
    }
  }
  return <div className="exec-steer">
    <input
      aria-label={`节点 ${identity.node_id ?? identity.node_run_id ?? ''} 纠偏输入`}
      value={text}
      onChange={event => setText(event.target.value)}
      onKeyDown={event => { if (event.key === 'Enter') void send() }}
      placeholder="纠正该节点的执行说明…"
    />
    <button type="button" className="editor-button" disabled={!text.trim()} onClick={() => void send()}>发送纠偏</button>
    {receipt && <span role="status" className="text-xs">{receipt}</span>}
  </div>
}
