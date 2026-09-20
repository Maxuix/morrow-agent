/**
 * Bounded inline Markdown tokenizer plus workspace file-link parsing.
 *
 * The chat renderer never parses source HTML and never navigates to a local
 * path itself: an external HTTP(S)/mailto link stays a real anchor, while a
 * local reference becomes a panel action. Resolution, symlink/traversal
 * rejection and workspace ownership are decided server side - this module
 * only produces a bounded, decoded-once candidate.
 */

export type InlineToken =
  | { kind: 'text'; text: string }
  | { kind: 'code'; text: string }
  | { kind: 'strong'; text: string }
  | { kind: 'em'; text: string }
  | { kind: 'link'; label: string; href: string }

/** Upper bounds keep the tokenizer linear on arbitrary model output. */
const MAX_CODE_SPAN = 400
const MAX_LABEL = 300
const MAX_HREF = 600
const MAX_SPAN_ATTEMPTS = 64

/** `[label](href)`, allowing one level of nested parentheses and spaces. */
function readLink(text: string, start: number): { label: string; href: string; end: number } | null {
  if (text[start] !== '[') return null
  const labelEnd = text.indexOf("]", start + 1)
  if (labelEnd < 0 || labelEnd - start - 1 > MAX_LABEL) return null
  if (text[labelEnd + 1] !== '(') return null
  const label = text.slice(start + 1, labelEnd)
  let depth = 0
  let index = labelEnd + 1
  const limit = Math.min(text.length, labelEnd + 1 + MAX_HREF + 2)
  while (index < limit) {
    const char = text[index]
    if (char === '\n') return null
    if (char === '(') depth += 1
    else if (char === ')') {
      depth -= 1
      if (depth === 0) {
        return { label, href: text.slice(labelEnd + 2, index), end: index + 1 }
      }
    }
    index += 1
  }
  return null
}

/** Tokenize one inline line: code spans, bold, emphasis and links. */
export function tokenizeInline(text: string): InlineToken[] {
  const tokens: InlineToken[] = []
  let buffer = ''
  let attempts = 0
  const flush = () => {
    if (buffer !== '') {
      tokens.push({ kind: 'text', text: buffer })
      buffer = ''
    }
  }
  let index = 0
  while (index < text.length) {
    const char = text[index]
    if (char === '`' && attempts < MAX_SPAN_ATTEMPTS) {
      const end = text.indexOf('`', index + 1)
      if (end > index + 1 && end - index - 1 <= MAX_CODE_SPAN) {
        flush()
        tokens.push({ kind: 'code', text: text.slice(index + 1, end) })
        index = end + 1
        attempts += 1
        continue
      }
    }
    if (char === '*' && attempts < MAX_SPAN_ATTEMPTS) {
      const marker = text.startsWith('**', index) ? '**' : '*'
      const end = text.indexOf(marker, index + marker.length)
      if (end > index + marker.length) {
        flush()
        tokens.push({ kind: marker === '**' ? 'strong' : 'em', text: text.slice(index + marker.length, end) })
        index = end + marker.length
        attempts += 1
        continue
      }
    }
    if (char === '[' && attempts < MAX_SPAN_ATTEMPTS) {
      const link = readLink(text, index)
      if (link !== null && link.href !== '') {
        flush()
        tokens.push({ kind: 'link', label: link.label, href: link.href })
        index = link.end
        attempts += 1
        continue
      }
    }
    buffer += char
    index += 1
  }
  flush()
  return tokens
}

export interface FileLinkTarget {
  /** 解码一次的候选路径；可能带行号后缀，由服务端规范化与拒绝。 */
  path: string
  /** 从 `:line` / `#Lline` 后缀解析出的 1 起算行号。 */
  line?: number
  /** 去掉行号后的候选路径；只有真实文件不存在时才回退到它。 */
  fallbackPath?: string
}

const LINE_SUFFIX = /^(.*?)(?::(\d{1,7})|#L(\d{1,7}))$/
const URL_SCHEME = /^[a-zA-Z][a-zA-Z0-9+.\-]*:/

/**
 * `[label](href)` → workspace file candidate, or null when it is not one.
 *
 * External schemes (including `file:`) never become file links, so a local
 * reference can never turn into a direct navigation. Percent-decoding happens
 * exactly once here; the server performs no second decode.
 */
/**
 * Resolve one document-relative reference against the viewed file's directory.
 *
 * Absolute, fragment and scheme references are left untouched, and a path that
 * escapes above the workspace root falls back to the raw reference so the
 * server-side resolver still owns traversal rejection.
 */
export function resolveDocumentHref(baseDir: string | undefined, href: string): string {
  const raw = href.trim()
  if (raw === '' || raw.startsWith('#') || raw.startsWith('/')) return href
  if (URL_SCHEME.test(raw) && !isFileWithLineSuffix(raw)) return href
  if (baseDir === undefined || baseDir === '' || baseDir === '.') return href
  const parts: string[] = []
  for (const part of `${baseDir.replace(/\/+$/, '')}/${raw}`.split('/')) {
    if (part === '' || part === '.') continue
    if (part === '..') {
      if (parts.length === 0) return href
      parts.pop()
      continue
    }
    parts.push(part)
  }
  return parts.join('/')
}

/**
 * True when a scheme-looking value is really a workspace file with a line
 * suffix (`notes.md:12`). Without this, every such link is dropped as if it
 * were a protocol; genuinely dangerous schemes never have this shape.
 */
function isFileWithLineSuffix(raw: string): boolean {
  const match = LINE_SUFFIX.exec(raw)
  if (match === null) return false
  const stripped = match[1]
  return stripped !== '' && /[./]/.test(stripped) && !/[\\/]/.test(stripped.split(':')[0])
}

export function parseFileHref(href: string): FileLinkTarget | null {
  const raw = href.trim()
  if (raw === '' || raw.length > MAX_HREF) return null
  if (raw.startsWith('#')) return null
  if (URL_SCHEME.test(raw) && !isFileWithLineSuffix(raw)) return null
  let decoded = raw
  try {
    decoded = decodeURIComponent(raw)
  } catch {
    return null
  }
  if (decoded === '' || decoded.length > MAX_HREF) return null
  if (/[\u0000-\u001f\u007f]/.test(decoded)) return null
  const match = LINE_SUFFIX.exec(decoded)
  if (match === null) return { path: decoded }
  const stripped = match[1]
  const line = Number(match[2] ?? match[3])
  if (stripped === '' || !Number.isInteger(line) || line < 1) return { path: decoded }
  return { path: decoded, line, fallbackPath: stripped }
}
