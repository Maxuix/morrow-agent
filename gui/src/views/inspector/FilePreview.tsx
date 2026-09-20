import { useEffect, useRef, useState } from 'react'
import type { ApiClient } from '../../api/client'
import type { WorkspaceFileInfo } from '../../api/types'
import { buttonClass } from '../management/styles'

/** 与服务端一致的有界上限：超过时只显示类型与大小，不尝试预览。 */
export const MAX_PREVIEW_BYTES = 20 * 1024 * 1024
/** 图片像素上限：超大图会拖垮面板，超过时改为只提供下载。 */
export const MAX_PREVIEW_PIXELS = 50_000_000

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
}

/** 图片像素预算；纯函数，便于在不加载真实图片的情况下断言。 */
export function exceedsPixelBudget(width: number, height: number): boolean {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return true
  return width * height > MAX_PREVIEW_PIXELS
}

/**
 * 受控下载。面板不带 Core 令牌时直接用同源下载地址，浏览器原生流式处理并可
 * 取消，也不把令牌放进 URL；带令牌的环境退回经认证的有界字节读取 + 手动保存。
 */
export function FileDownload({
  client,
  path,
  filename,
  label = '下载',
}: {
  client: ApiClient
  path: string
  filename: string
  label?: string
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const abort = useRef<AbortController | null>(null)

  if (client.tokenless) {
    return (
      <a className={buttonClass} href={client.workspaceFileDownloadUrl(path)} download={filename}>
        {label}
      </a>
    )
  }

  const start = async () => {
    const controller = new AbortController()
    abort.current = controller
    setBusy(true)
    setError(null)
    try {
      const blob = await client.workspaceFileBytes(path, controller.signal)
      const url = URL.createObjectURL(blob)
      try {
        const anchor = document.createElement('a')
        anchor.href = url
        anchor.download = filename
        anchor.click()
      } finally {
        URL.revokeObjectURL(url)
      }
    } catch (failure) {
      if (!controller.signal.aborted) {
        setError(failure instanceof Error ? failure.message : '下载失败')
      }
    } finally {
      abort.current = null
      setBusy(false)
    }
  }

  return (
    <span className="flex flex-wrap items-center gap-2">
      <button type="button" className={buttonClass} disabled={busy} onClick={() => void start()}>
        {busy ? '下载中…' : label}
      </button>
      {busy && (
        <button type="button" className={buttonClass} onClick={() => abort.current?.abort()}>
          取消下载
        </button>
      )}
      {error !== null && <span role="alert" className="text-xs text-failed">{error}</span>}
    </span>
  )
}

/**
 * 图片/PDF 预览：字节经认证读取后建成对象 URL，切换文件或关闭面板时释放。
 * SVG 只作为图像上下文使用，HTML 文本永远不会被当作文档加载。
 */
export function BinaryPreview({
  client,
  info,
  path,
}: {
  client: ApiClient
  info: WorkspaceFileInfo
  path: string
}) {
  const previewable = info.preview === 'image' || info.preview === 'pdf'
  const [tick, setTick] = useState(0)
  const [url, setUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(previewable)
  const [tooManyPixels, setTooManyPixels] = useState(false)

  useEffect(() => {
    if (!previewable) {
      setLoading(false)
      return
    }
    let cancelled = false
    let created: string | null = null
    setLoading(true)
    setError(null)
    setUrl(null)
    setTooManyPixels(false)
    client.workspaceFileBytes(path).then(
      blob => {
        if (cancelled) return
        try {
          created = URL.createObjectURL(blob)
        } catch {
          // 环境不支持对象 URL 时如实说明，绝不假装已经有预览。
          setError('无法预览，请下载查看。')
          setLoading(false)
          return
        }
        setUrl(created)
        setLoading(false)
      },
      (failure: unknown) => {
        if (cancelled) return
        setError(failure instanceof Error ? failure.message : '读取预览字节失败')
        setLoading(false)
      },
    )
    return () => {
      cancelled = true
      if (created !== null) URL.revokeObjectURL(created)
    }
  }, [client, path, previewable, tick])

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      {loading && <p role="status" className="text-sm text-secondary">正在读取预览…</p>}
      {error !== null && (
        <div className="space-y-2">
          <p role="alert" className="text-sm text-failed">{error}</p>
          <button type="button" className={buttonClass} onClick={() => setTick(value => value + 1)}>
            重试预览
          </button>
        </div>
      )}
      {url !== null && info.preview === 'image' && !tooManyPixels && (
        <img
          src={url}
          alt={path}
          data-file-preview="image"
          onLoad={event => {
            const node = event.currentTarget
            if (exceedsPixelBudget(node.naturalWidth, node.naturalHeight)) {
              setTooManyPixels(true)
            }
          }}
          style={{ maxWidth: '100%', flex: '1 1 auto', minHeight: 0, objectFit: 'contain' }}
        />
      )}
      {tooManyPixels && (
        <p role="status" className="text-sm text-secondary">
          图片过大，请下载查看。
        </p>
      )}
      {url !== null && info.preview === 'pdf' && (
        <iframe
          title="PDF 预览"
          data-file-preview="pdf"
          src={url}
          style={{ width: '100%', flex: '1 1 auto', minHeight: '12rem', border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius-sm)' }}
        />
      )}
      {!previewable && <p role="status" className="text-sm text-secondary">此文件类型暂不支持预览。</p>}
    </div>
  )
}

/** 浏览器是否具备 WebGL2；只作为提示，不代替页面自身的运行结果。 */
export function webglAvailable(): boolean {
  try {
    const canvas = document.createElement('canvas')
    if (typeof canvas.getContext !== 'function') return false
    return canvas.getContext('webgl2') !== null
  } catch {
    return false
  }
}

interface HtmlPreviewState {
  phase: 'loading' | 'ready' | 'error'
  url: string | null
  missing: string[]
  totalBytes: number | null
  message: string | null
}

/**
 * HTML 运行预览：内容来自服务端一次性构建的固定字节集合，页面运行在独立的
 * loopback 端口上，以 sandbox（仅 allow-scripts，无 allow-same-origin）承载。
 * 草稿不会被自动执行：只有已保存的版本才会建立预览；保存成功后 revision 变化，
 * 这里重新建立集合。切换文件、切标签或卸载时释放该集合。
 */
export function HtmlPreview({
  client,
  path,
  revision,
  active,
  refreshKey = 0,
  artifactSessionId,
  artifactId,
  artifactTaskRunId,
  artifactWorkflowRunId,
  artifactNodeRunId,
}: {
  client: ApiClient
  /** 当前工作区来源；历史快照来源只用 artifact* 属性。 */
  path?: string
  revision?: string
  active: boolean
  refreshKey?: number
  artifactSessionId?: string
  artifactId?: string
  artifactTaskRunId?: string | null
  artifactWorkflowRunId?: string | null
  artifactNodeRunId?: string | null
}) {
  const [tick, setTick] = useState(0)
  const [state, setState] = useState<HtmlPreviewState>({
    phase: 'loading',
    url: null,
    missing: [],
    totalBytes: null,
    message: null,
  })

  useEffect(() => {
    if (!active) return
    let cancelled = false
    let previewId: string | null = null
    setState({ phase: 'loading', url: null, missing: [], totalBytes: null, message: null })
    const request =
      artifactId !== undefined && artifactSessionId !== undefined
        ? client.createHtmlPreviewFromArtifact({
            sessionId: artifactSessionId,
            artifactId,
            taskRunId: artifactTaskRunId,
            workflowRunId: artifactWorkflowRunId,
            nodeRunId: artifactNodeRunId,
          })
        : path !== undefined && revision !== undefined
          ? client.createHtmlPreview(path, revision)
          : null
    if (request === null) {
      setState({
        phase: 'error',
        url: null,
        missing: [],
        totalBytes: null,
        message: '缺少预览来源',
      })
      return
    }
    request.then(
      preview => {
        if (cancelled) {
          void client.releaseHtmlPreview(preview.preview_id).catch(() => {})
          return
        }
        previewId = preview.preview_id
        setState({
          phase: 'ready',
          url: preview.url,
          missing: preview.missing,
          totalBytes: preview.total_bytes,
          message: null,
        })
      },
      (failure: unknown) => {
        if (cancelled) return
        setState({
          phase: 'error',
          url: null,
          missing: [],
          totalBytes: null,
          message: failure instanceof Error ? failure.message : '建立运行预览失败',
        })
      },
    )
    return () => {
      cancelled = true
      if (previewId !== null) void client.releaseHtmlPreview(previewId).catch(() => {})
    }
  }, [
    client,
    path,
    revision,
    active,
    tick,
    refreshKey,
    artifactSessionId,
    artifactId,
    artifactTaskRunId,
    artifactWorkflowRunId,
    artifactNodeRunId,
  ])

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 text-xs text-secondary">
        <span>运行预览</span>
        {state.totalBytes !== null && <span>· {formatBytes(state.totalBytes)}</span>}
        <button type="button" className={buttonClass} onClick={() => setTick(value => value + 1)}>
          刷新预览
        </button>
        <a className={buttonClass} href={state.url ?? undefined} target="_blank" rel="noopener noreferrer" aria-disabled={state.url === null}>
          在新标签打开
        </a>
      </div>

      {state.phase === 'loading' && (
        <p role="status" className="text-sm text-secondary">正在加载预览…</p>
      )}
      {state.phase === 'error' && (
        <div className="space-y-2">
          <p role="alert" className="text-sm text-failed">{state.message}</p>
          <p className="text-xs text-secondary">
            未保存的草稿不会被执行；请先保存，或检查入口文件与依赖是否都在工作区内。
          </p>
        </div>
      )}
      {state.phase === 'ready' && state.url !== null && (
        <iframe
          title="HTML 运行预览"
          data-file-preview="html"
          sandbox="allow-scripts"
          src={state.url}
          style={{
            width: '100%',
            flex: '1 1 auto',
            minHeight: '16rem',
            border: '1px solid var(--border-subtle)',
            borderRadius: 'var(--radius-sm)',
            background: 'var(--bg-base)',
          }}
        />
      )}
      {state.missing.length > 0 && (
        <div role="status" className="text-xs text-secondary">
          <p>以下依赖未包含在本次预览中（页面可能因此报错）：</p>
          <ul className="list-disc pl-5">
            {state.missing.slice(0, 5).map(entry => (
              <li key={entry} className="break-all">{entry}</li>
            ))}
            {state.missing.length > 5 && <li>以及另外 {state.missing.length - 5} 项</li>}
          </ul>
          <p>补全依赖后刷新预览。</p>
        </div>
      )}
    </div>
  )
}
