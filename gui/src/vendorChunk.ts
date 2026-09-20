/** Stable vendor graphs for the production Rollup split. */
const MARKDOWN_VENDOR = [
  '/react-markdown/',
  '/remark-',
  '/micromark',
  '/mdast-',
  '/unified/',
  '/unist-',
  '/hast-',
  '/vfile',
  '/character-entities',
  '/decode-named-character-reference/',
  '/property-information/',
  '/space-separated-tokens/',
  '/comma-separated-tokens/',
  '/trim-lines/',
  '/devlop/',
  '/bail/',
  '/trough/',
  '/zwitch/',
  '/is-plain-obj/',
  '/ccount/',
  '/markdown-table/',
  '/longest-streak/',
  '/escape-string-regexp/',
  '/web-namespaces/',
  '/html-url-attributes/',
  '/parse-entities/',
  '/style-to-object/',
  '/inline-style-parser/',
  '/character-reference-invalid/',
  '/is-alphanumerical/',
  '/is-decimal/',
  '/is-hexadecimal/',
  '/is-alphabetical/',
  '/stringify-entities/',
]

export function vendorChunk(id: string): string | undefined {
  if (!id.includes('node_modules')) return undefined
  if (id.includes('@xyflow')) return 'vendor-xyflow'
  if (MARKDOWN_VENDOR.some(marker => id.includes(marker))) return 'vendor-markdown'
  // CodeMirror is split along its one-way dependency order: the Lezer
  // runtime is a leaf, CodeMirror core and the React wrapper sit above it,
  // and the language packs are leaves of the core. Any other grouping makes
  // two chunks import each other and breaks the built GUI at load (observed
  // in the browser acceptance), while each file must stay under the gate.
  if (id.includes('@lezer/')) return 'vendor-codemirror-lezer'
  if (id.includes('@codemirror/lang-')) return 'vendor-codemirror-languages'
  if (id.includes('@codemirror/') || id.includes('@uiw/')) return 'vendor-codemirror'
  if (
    id.includes('react-dom') ||
    id.includes('/react/') ||
    id.includes('\\react\\') ||
    id.includes('/scheduler/') ||
    id.includes('\\scheduler\\')
  ) {
    return 'vendor-react'
  }
  return undefined
}
