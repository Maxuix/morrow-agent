import { useEffect, useState, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { FileTarget } from '../../state/inspector'
import { parseFileHref, resolveDocumentHref } from './inlineLinks'
import type { CodeSegment } from './markdownHighlight'
import { CopyButton, FileLink, flattenText, safeLink } from './markdownParts'

/**
 * The one Markdown renderer for chat, results, documents and history.
 *
 * remark-gfm supplies tables, task lists, strikethrough and autolinks. Raw HTML
 * is skipped instead of executed, remote images are never auto-loaded, and a
 * local reference stays a panel action instead of a navigation. Fenced code is
 * highlighted statically through the CodeMirror language parsers: no EditorView
 * is mounted per block.
 */

const MAX_URL_CHARS = 2048

/**
 * Keep only URLs the app can safely classify: validated external links,
 * in-document fragments and workspace file candidates. Everything else
 * (javascript:, data:, file:, blob:, oversize) is dropped, so disabling the
 * library default never weakens the boundary.
 */
function safeMarkdownUrl(url: string): string {
  const trimmed = url.trim()
  if (trimmed === '' || trimmed.length > MAX_URL_CHARS) return ''
  if (safeLink(trimmed) !== null) return trimmed
  if (trimmed.startsWith('#')) return trimmed
  return parseFileHref(trimmed) === null ? '' : trimmed
}

function heading(level: number, children: ReactNode) {
  return (
    <div role="heading" aria-level={level} className="chat-heading">
      {children}
    </div>
  )
}

function CodeBlock({ language, code }: { language: string; code: string }) {
  const [segments, setSegments] = useState<CodeSegment[] | null>(null)
  useEffect(() => {
    let cancelled = false
    setSegments(null)
    void import('./markdownHighlight').then(
      module => {
        if (!cancelled) setSegments(module.highlightSegments(code, language))
      },
      () => {
        if (!cancelled) setSegments(null)
      },
    )
    return () => {
      cancelled = true
    }
  }, [code, language])
  return (
    <div className="chat-code">
      <div className="flex items-center justify-between gap-2">
        <span>{language}</span>
        <CopyButton text={code} label="复制代码" />
      </div>
      <pre>
        <code>
          {segments === null
            ? code
            : segments.map((segment, index) =>
                segment.className === null ? (
                  <span key={index}>{segment.text}</span>
                ) : (
                  <span key={index} className={segment.className}>
                    {segment.text}
                  </span>
                ),
              )}
        </code>
      </pre>
    </div>
  )
}

function RemoteImage({ source, label }: { source: string; label: string }) {
  return (
    <span className="chat-image-ref" title={source}>
      远程图片未自动加载：{label || source}
    </span>
  )
}

export function MarkdownBody({
  text,
  onOpenFile,
  className = 'chat-markdown',
  basePath,
  historical = false,
}: {
  text: string
  onOpenFile?: (target: FileTarget) => void
  className?: string
  /** Directory of the viewed document; chat has none and stays workspace-relative. */
  basePath?: string
  /** Historical body: a local link can only open the current workspace version. */
  historical?: boolean
}) {
  const components: Components = {
    h1: ({ children }) => heading(1, children),
    h2: ({ children }) => heading(2, children),
    h3: ({ children }) => heading(3, children),
    h4: ({ children }) => heading(4, children),
    h5: ({ children }) => heading(5, children),
    h6: ({ children }) => heading(6, children),
    pre: ({ children }) => <>{children}</>,
    code: ({ className: codeClass, children }) => {
      const language = /language-([\w-]+)/.exec(codeClass ?? '')?.[1] ?? ''
      const value = flattenText(children)
      if (language === '' && !value.includes('\n')) {
        return <code className={codeClass}>{children}</code>
      }
      return <CodeBlock language={language} code={value.replace(/\n$/, '')} />
    },
    a: ({ href, children }) => {
      const label = flattenText(children)
      const external = typeof href === 'string' ? safeLink(href) : null
      if (external !== null) {
        return (
          <a href={external} target="_blank" rel="noopener noreferrer">
            {children}
          </a>
        )
      }
      if (typeof href !== 'string') return <span>{children}</span>
      return (
        <FileLink
          label={label}
          href={resolveDocumentHref(basePath, href)}
          onOpenFile={onOpenFile}
          currentVersion={historical}
        />
      )
    },
    img: ({ src, alt }) => {
      const source = typeof src === 'string' ? src : ''
      const external = source === '' ? null : safeLink(source)
      if (external !== null) return <RemoteImage source={external} label={alt ?? ''} />
      const resolved = resolveDocumentHref(basePath, source)
      const target = resolved === '' ? null : parseFileHref(resolved)
      if (target !== null && onOpenFile !== undefined) {
        return (
          <FileLink
            label={alt || target.path}
            href={resolved}
            onOpenFile={onOpenFile}
            currentVersion={historical}
          />
        )
      }
      return <span className="chat-image-ref">{alt || source}</span>
    },
  }
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        urlTransform={safeMarkdownUrl}
        components={components}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}
