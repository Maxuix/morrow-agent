import { useState, type ReactNode } from 'react'
import type { FileTarget } from '../../state/inspector'
import { parseFileHref } from './inlineLinks'

/** Only http(s)/mailto become real anchors; everything else is inert text. */
export function safeLink(value: string): string | null {
  // No images, active HTML, relative file loads, or protocol-relative URLs.
  if (!/^(https?:\/\/|mailto:)/i.test(value) || /[\u0000-\u0020]/.test(value)) return null
  try {
    const url = new URL(value)
    return ['https:', 'http:', 'mailto:'].includes(url.protocol) ? url.href : null
  } catch {
    return null
  }
}

export function CopyButton({ text, label = '复制' }: { text: string; label?: string }) {
  const [status, setStatus] = useState('')
  return (
    <button
      className="editor-button"
      type="button"
      onClick={() =>
        void navigator.clipboard.writeText(text).then(
          () => setStatus('已复制'),
          () => setStatus('复制失败，请选择文本复制'),
        )
      }
    >
      {status || label}
    </button>
  )
}

/** 本地文件链接：点击只在右侧面板打开，绝不直接导航。 */
export function FileLink({
  label,
  href,
  onOpenFile,
  currentVersion = false,
}: {
  label: string
  href: string
  onOpenFile?: (target: FileTarget) => void
  /** Historical body: an inline link can only open the current workspace file. */
  currentVersion?: boolean
}) {
  const target = parseFileHref(href)
  if (target === null) return <span>{label}</span>
  if (onOpenFile === undefined) return <code>{label}</code>
  return (
    <button
      type="button"
      className="chat-file-link"
      title={
        currentVersion
          ? `打开当前工作区版本 ${target.path}（历史正文）`
          : `在右侧打开 ${target.path}`
      }
      onClick={() =>
        onOpenFile({
          kind: 'workspace',
          path: target.path,
          ...(target.line !== undefined ? { line: target.line } : {}),
          ...(target.fallbackPath !== undefined ? { fallbackPath: target.fallbackPath } : {}),
        })
      }
    >
      {label}
    </button>
  )
}

/** One rendered node's plain text, for labels and copy actions. */
export function flattenText(value: ReactNode): string {
  if (value === null || value === undefined || typeof value === 'boolean') return ''
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  if (Array.isArray(value)) return value.map(flattenText).join('')
  const element = value as { props?: { children?: ReactNode } }
  return flattenText(element.props?.children)
}
