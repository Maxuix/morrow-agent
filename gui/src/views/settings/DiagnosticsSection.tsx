import { useEffect, useRef, useState } from 'react'
import { ApiError, type ApiClient } from '../../api/client'
import { fieldClass } from '../management/styles'
import { commandId } from '../lib/editor'
import { CONNECTION_LABELS } from '../lib/labels'
import type { ConnectionState } from '../../state/sync'

type Status = {
  scope: string
  maintaining: boolean
  blockers: string[]
  backups: string[]
  next_cursor: string | null
}

type Job = {
  status: string
  action: string
  message?: string
  error?: string
  result?: {
    preview_digest?: string
    download?: string
    [key: string]: unknown
  }
}

const DATA_ROOT_SCOPE =
  '此操作作用于当前 Core 的整个数据根（所有已注册工作区），不是当前项目或当前会话。'

function isConnectionState(value: string | undefined): value is ConnectionState {
  return value === 'connecting' || value === 'live' || value === 'reconnecting'
    || value === 'offline' || value === 'unauthorized'
}

function resultEntries(result: Job['result'] | undefined): Array<[string, string]> {
  if (!result) return []
  const skip = new Set(['preview_digest', 'download'])
  const rows: Array<[string, string]> = []
  for (const [key, value] of Object.entries(result)) {
    if (skip.has(key) || value === undefined || value === null) continue
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      rows.push([key, String(value)])
    } else {
      rows.push([key, Array.isArray(value) ? `${value.length} 项` : '已记录'])
    }
  }
  return rows
}

function eventLabel(event: unknown, index: number): string {
  if (!event || typeof event !== 'object') return `事件 ${index + 1}`
  const record = event as Record<string, unknown>
  const kind = record.action ?? record.type ?? record.kind
  const message = record.message ?? record.summary
  const parts = [
    typeof kind === 'string' && kind ? kind : `事件 ${index + 1}`,
    typeof message === 'string' && message ? message : null,
  ]
  return parts.filter(Boolean).join(' · ')
}

/**
 * Structured diagnostics body for the settings page. Backup/cleanup keep the
 * original services and in-place confirmation; raw JSON dumps stay out of the
 * ordinary path. The modal wrapper is unused after this package.
 */
export function DiagnosticsSection({
  client,
  connection,
}: {
  client: ApiClient
  connection?: string
}) {
  const [status, setStatus] = useState<Status | null>(null)
  const [page, setPage] = useState(0)
  const [name, setName] = useState('')
  const [bundle, setBundle] = useState('')
  const [job, setJob] = useState<Job | null>(null)
  const [jobId, setJobId] = useState('')
  const [resumeId, setResumeId] = useState('')
  const [preview, setPreview] = useState<string | null>(null)
  const [after, setAfter] = useState(0)
  const [events, setEvents] = useState<{ events: unknown[]; next_cursor: string | null } | null>(null)
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  const [confirmation, setConfirmation] = useState<Record<string, unknown> | null>(null)
  const [advanced, setAdvanced] = useState(false)
  const retry = useRef<{ key: string; id: string } | null>(null)

  async function refresh() {
    try {
      setStatus(await client.operationQuery<Status>('state/status', { page }))
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : '状态读取失败')
    }
  }

  useEffect(() => { void refresh() }, [client, page])

  useEffect(() => {
    if (!jobId) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async () => {
      try {
        const result = await client.operationQuery<Job>(`state-jobs/${encodeURIComponent(jobId)}`)
        if (cancelled) return
        setJob(result)
        if (result.status === 'running') {
          timer = setTimeout(() => void poll(), 750)
        } else {
          if (result.result?.preview_digest && result.action === 'cleanup_preview') {
            setPreview(result.result.preview_digest)
          }
          void refresh()
        }
      } catch (cause) {
        if (!cancelled) setError(cause instanceof ApiError ? cause.message : '无法获取维护结果，可核对原请求编号')
      }
    }
    void poll()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [jobId, client])

  async function execute(body: Record<string, unknown>) {
    const key = JSON.stringify(body)
    if (retry.current?.key !== key) retry.current = { key, id: commandId('state') }
    setPending(true)
    setError('')
    try {
      const result = await client.operationCommand<{ command_id: string }>('state-actions', {
        ...body,
        command_id: retry.current.id,
      })
      setJob({ status: 'running', action: String(body.action) })
      setJobId(result.command_id)
      setResumeId(result.command_id)
      setConfirmation(null)
      retry.current = null
      void refresh()
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : '提交结果待核对，请按原编号重试')
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) retry.current = null
    } finally {
      setPending(false)
    }
  }

  const busy = pending || job?.status === 'running'
  const connectionLabel = isConnectionState(connection) ? CONNECTION_LABELS[connection] : (connection || '未知')
  const extraResult = resultEntries(job?.result)

  return (
    <section className="settings-page" aria-label="诊断与维护">
      <div className="settings-content">
        <header className="settings-heading">
          <span className="settings-eyebrow">设置 / 诊断与维护</span>
          <h2>诊断与维护</h2>

        </header>

        {error && <p role="alert" className="text-failed">{error}</p>}

        <section className="diagnostics-health" aria-label="连接与存储健康">
          <article className="diagnostics-health-card">
            <h3>连接</h3>
            <p>{connectionLabel}</p>
            <p className="diagnostics-scope">当前浏览器到 Core 的实时连接。断开时不会自动续跑任务。</p>
          </article>
          <article className="diagnostics-health-card">
            <h3>存储</h3>
            {status ? (
              <>
                <p>{status.maintaining ? '正在维护，新写入已暂停' : '未在维护'}</p>
                <p className="diagnostics-scope">
                  占用工作区 {status.blockers.length} 个。
                  {status.blockers.length === 0
                    ? '当前没有阻塞备份或清理的运行。'
                    : '请先处理下列占用后再备份或清理。'}
                </p>
              </>
            ) : (
              <p>正在读取存储状态…</p>
            )}
            <button type="button" className="editor-button" onClick={() => void refresh()}>刷新维护状态</button>
          </article>
        </section>

        {status && status.blockers.length > 0 && (
          <section aria-label="修复建议">
            <h3>修复建议</h3>
            <ul className="diagnostics-advice">
              {status.blockers.map(workspace => (
                <li key={workspace}>{workspace}：先停止运行、撤回排队输入或等待附件处理完成</li>
              ))}
            </ul>
          </section>
        )}

        <section aria-label="诊断动作">
          <h3>状态诊断</h3>

          <button type="button" className="editor-button" disabled={busy} onClick={() => void execute({ action: 'doctor' })}>
            运行状态诊断
          </button>
        </section>

        <section aria-label="孤立文件清理">
          <h3>孤立文件清理</h3>
          <p className="diagnostics-scope">{DATA_ROOT_SCOPE} 清理只隔离无权威引用的孤立文件，保留可恢复的隔离内容。</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="editor-button" disabled={busy} onClick={() => { setPreview(null); void execute({ action: 'cleanup_preview' }) }}>
              预览孤立文件清理
            </button>
            <button
              type="button"
              className="editor-button"
              disabled={busy || !preview}
              onClick={() => setConfirmation({ action: 'cleanup', preview_digest: preview, confirmed: true })}
            >
              按预览清理
            </button>
          </div>

        </section>

        <section aria-label="备份">
          <h3>备份</h3>
          <p className="diagnostics-scope">{DATA_ROOT_SCOPE} 备份不包含凭据。</p>
          <label>备份名称（可留空）
            <input className={fieldClass} aria-label="备份名称" value={name} maxLength={64} onChange={event => setName(event.target.value)} />
          </label>
          <button
            type="button"
            className="editor-button"
            disabled={busy}
            onClick={() => setConfirmation({ action: 'backup', ...(name ? { name } : {}), confirmed: true })}
          >
            创建完整备份
          </button>
          <label>待校验备份目录
            <input
              className={fieldClass}
              aria-label="备份目录"
              placeholder="选择已创建备份，或输入已有 .bundle 目录绝对路径"
              value={bundle}
              onChange={event => setBundle(event.target.value)}
            />
          </label>
          <button type="button" className="editor-button" disabled={busy || !bundle} onClick={() => void execute({ action: 'verify', bundle })}>
            校验备份
          </button>
          <div className="space-y-2">
            {status?.backups.map(item => (
              <button type="button" className="editor-button block break-all" key={item} onClick={() => setBundle(item)}>{item}</button>
            ))}
            <div className="flex gap-2">
              <button type="button" className="editor-button" disabled={!page} onClick={() => setPage(value => value - 1)}>上一页</button>
              <span>第 {page + 1} 页</span>
              <button type="button" className="editor-button" disabled={!status?.next_cursor} onClick={() => setPage(value => value + 1)}>下一页</button>
            </div>
          </div>
        </section>

        {confirmation && (
          <div className="rounded border border-subtle p-3" role="dialog" aria-label="确认维护操作">
            <p>
              {confirmation.action === 'backup'
                ? `${DATA_ROOT_SCOPE} 确认备份整个数据根；凭据不包含在备份内。`
                : `${DATA_ROOT_SCOPE} 确认隔离当前预览中的孤立文件；对象或引用变化会拒绝执行。`}
            </p>
            <button type="button" className="editor-button" disabled={busy} onClick={() => void execute(confirmation)}>确认维护操作</button>
            <button type="button" className="editor-button" disabled={busy} onClick={() => setConfirmation(null)}>返回检查</button>
          </div>
        )}

        {job && (
          <div role="status" className="diagnostics-health-card">
            <p>{job.action} · {job.status}</p>
            {job.message && <p>{job.message}</p>}
            {job.error && <p className="text-failed">{job.error}</p>}

            {job.result?.download && (
              <button
                type="button"
                className="editor-button"
                onClick={async () => {
                  try {
                    const blob = await client.stateDownload(job.result!.download!)
                    const url = URL.createObjectURL(blob)
                    const link = document.createElement('a')
                    link.href = url
                    link.download = job.result!.download!
                    link.click()
                    setTimeout(() => URL.revokeObjectURL(url), 1000)
                  } catch (cause) {
                    setError(cause instanceof ApiError ? cause.message : '下载失败')
                  }
                }}
              >
                下载已校验备份 ZIP
              </button>
            )}
          </div>
        )}

        <details className="diagnostics-advanced" open={advanced} onToggle={event => setAdvanced((event.target as HTMLDetailsElement).open)}>
          <summary>高级诊断</summary>

          {extraResult.length > 0 && (
            <dl className="asset-fields">
              {extraResult.map(([key, value]) => (
                <div key={key} className="asset-field">
                  <dt>{key}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          )}
          <label>核对已有维护请求
            <input className={fieldClass} aria-label="维护请求编号" value={resumeId} onChange={event => setResumeId(event.target.value)} />
          </label>
          <button
            type="button"
            className="editor-button"
            disabled={!resumeId}
            onClick={() => { setJobId(''); queueMicrotask(() => setJobId(resumeId)) }}
          >
            核对请求
          </button>
          <label>事件起始游标
            <input className={fieldClass} aria-label="事件起始游标" type="number" min={0} value={after} onChange={event => setAfter(Number(event.target.value))} />
          </label>
          <button
            type="button"
            className="editor-button"
            onClick={async () => {
              try {
                setEvents(await client.operationQuery('state/events', { after, limit: 20 }))
              } catch (cause) {
                setError(cause instanceof ApiError ? cause.message : '事件读取失败')
              }
            }}
          >
            读取事件
          </button>
          {events && (
            <>
              {events.events.length === 0
                ? <p>没有更多事件。</p>
                : (
                  <ul className="diagnostics-advice">
                    {events.events.map((event, index) => <li key={index}>{eventLabel(event, index)}</li>)}
                  </ul>
                )}
              <button
                type="button"
                className="editor-button"
                disabled={!events.next_cursor}
                onClick={async () => {
                  const next = Number(events.next_cursor)
                  setAfter(next)
                  try {
                    setEvents(await client.operationQuery('state/events', { after: next, limit: 20 }))
                  } catch {
                    setError('事件读取失败')
                  }
                }}
              >
                下一页事件
              </button>
            </>
          )}
        </details>
      </div>
    </section>
  )
}
