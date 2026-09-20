/**
 * Static Lezer highlighting for fenced Markdown code blocks.
 *
 * The language packs are loaded on demand and never mount an EditorView per
 * block; the returned segments are plain React text, so model output can never
 * inject HTML.
 */

import { css, cssLanguage } from '@codemirror/lang-css'
import { html, htmlLanguage } from '@codemirror/lang-html'
import {
  javascript,
  javascriptLanguage,
  jsxLanguage,
  tsxLanguage,
  typescriptLanguage,
} from '@codemirror/lang-javascript'
import { json, jsonLanguage } from '@codemirror/lang-json'
import { markdown, markdownLanguage } from '@codemirror/lang-markdown'
import { python, pythonLanguage } from '@codemirror/lang-python'
import { yaml, yamlLanguage } from '@codemirror/lang-yaml'
import type { LanguageSupport } from '@codemirror/language'
import { highlightTree, tagHighlighter, tags as t } from '@lezer/highlight'
import type { Parser } from '@lezer/common'

export interface CodeSegment {
  text: string
  className: string | null
}

const highlighter = tagHighlighter([
  { tag: t.keyword, class: 'tok-keyword' },
  { tag: [t.name, t.deleted, t.character, t.macroName], class: 'tok-name' },
  { tag: [t.function(t.variableName), t.labelName], class: 'tok-function' },
  { tag: [t.color, t.constant(t.name), t.standard(t.name)], class: 'tok-constant' },
  { tag: [t.definition(t.name), t.separator, t.propertyName], class: 'tok-definition' },
  {
    tag: [t.typeName, t.className, t.number, t.changed, t.annotation, t.modifier, t.self, t.namespace],
    class: 'tok-type',
  },
  { tag: [t.operator, t.operatorKeyword, t.url, t.escape, t.regexp], class: 'tok-operator' },
  { tag: [t.meta, t.comment], class: 'tok-comment' },
  { tag: t.strong, class: 'tok-strong' },
  { tag: t.emphasis, class: 'tok-emphasis' },
  { tag: t.strikethrough, class: 'tok-strike' },
  { tag: t.link, class: 'tok-link' },
  { tag: t.heading, class: 'tok-heading' },
  { tag: [t.atom, t.bool, t.special(t.variableName)], class: 'tok-atom' },
  { tag: [t.processingInstruction, t.string, t.inserted], class: 'tok-string' },
  { tag: t.invalid, class: 'tok-invalid' },
])

const PARSERS: Record<string, Parser> = {
  js: javascriptLanguage.parser,
  javascript: javascriptLanguage.parser,
  jsx: jsxLanguage.parser,
  ts: typescriptLanguage.parser,
  typescript: typescriptLanguage.parser,
  tsx: tsxLanguage.parser,
  py: pythonLanguage.parser,
  python: pythonLanguage.parser,
  json: jsonLanguage.parser,
  yaml: yamlLanguage.parser,
  yml: yamlLanguage.parser,
  html: htmlLanguage.parser,
  htm: htmlLanguage.parser,
  css: cssLanguage.parser,
  md: markdownLanguage.parser,
  markdown: markdownLanguage.parser,
}

export function supportedLanguage(info: string): boolean {
  return PARSERS[info.trim().toLowerCase()] !== undefined
}

/** Highlighted segments for one code block, or null for an unknown language. */
export function highlightSegments(code: string, info: string): CodeSegment[] | null {
  const parser = PARSERS[info.trim().toLowerCase()]
  if (parser === undefined) return null
  const segments: CodeSegment[] = []
  let cursor = 0
  highlightTree(parser.parse(code), highlighter, (from, to, classes) => {
    if (from > cursor) segments.push({ text: code.slice(cursor, from), className: null })
    segments.push({ text: code.slice(from, to), className: classes || null })
    cursor = to
  })
  if (cursor < code.length) segments.push({ text: code.slice(cursor), className: null })
  return segments.length === 0 ? null : segments
}

/** File-suffix selection for the read-only and editing CodeMirror views. */
export function languageForPath(path: string): LanguageSupport | null {
  const leaf = path.split('/').pop() ?? path
  const suffix = leaf.includes('.') ? leaf.split('.').pop()!.toLowerCase() : ''
  switch (suffix) {
    case 'js':
    case 'mjs':
    case 'cjs':
      return javascript({ jsx: false })
    case 'jsx':
      return javascript({ jsx: true })
    case 'ts':
    case 'mts':
    case 'cts':
      return javascript({ typescript: true })
    case 'tsx':
      return javascript({ typescript: true, jsx: true })
    case 'py':
      return python()
    case 'json':
      return json()
    case 'yaml':
    case 'yml':
      return yaml()
    case 'html':
    case 'htm':
    case 'xhtml':
    case 'svg':
      return html()
    case 'css':
      return css()
    case 'md':
    case 'markdown':
      return markdown()
    default:
      return null
  }
}

