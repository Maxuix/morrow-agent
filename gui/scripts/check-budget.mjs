#!/usr/bin/env node
/*
 * Bundle budget check for the GUI build output. Plain node, no deps.
 * Fails (exit 1) if:
 *   - total JS across dist exceeds 1900 KiB
 *   - any single JS file exceeds 500 KiB
 *   - total CSS across dist exceeds 120 KiB
 *   - any font asset is not .woff2
 */
import { readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const DIST = fileURLToPath(new URL('../../src/morrow/gui_static', import.meta.url))

// Total JS still counts every emitted chunk, including vendor. The previous
// 720 KiB cap was a single-bundle ceiling with ~3 KiB of slack. Vendor split
// does not shrink that total; it keeps React / xyflow off the app chunk so
// product code can grow. 500 KiB is the per-file gate that matches Vite's
// large-chunk warning.
//
// The combined ceiling moved 800 -> 832 KiB with the preference-profile-ui
// plan: the graphical preference surface (list, editor, history, ordered
// batch) replaced the old ContextDrawer editors and netted ~9 KiB while the
// old forms were deleted, and lazy loading cannot reduce the total because
// every emitted chunk is counted. The user explicitly asked for a higher
// ceiling (2026-09-08), so 832 KiB (+4%) leaves ~29 KiB of deliberate
// headroom; the per-file 500 KiB gate and the CSS gate stay unchanged, so an
// unbounded regression still fails the build. Recorded in
// docs/acceptance/preference-profile-ui-redesign.md.
//
// Workbench refactor transition (2026-09-11): the Inspector shell lands while
// the legacy panels still ship (they migrate in packages 5/8 and are deleted
// in package 10). The user explicitly raised the ceiling to 1000 KiB so the
// remaining packages do not churn the gate; package 10 must still record the
// final total against the 832 KiB pre-refactor baseline in the delivery
// report. Recorded in
// docs/decisions/gui-workbench-transitional-js-budget-2026-09-11.md.
//
// Mature text rendering (2026-09-16, subplan 2 of
// workflow-delivery-preview-2026-09-16): the self-written Markdown parser and
// textarea-overlay editor were replaced by react-markdown + remark-gfm and
// CodeMirror 6 (@uiw/react-codemirror) with the seven required language
// families. Measured on this branch: 983.5 KiB before the swap, 1795.2 KiB
// after it (net +811.7 KiB). The increment is library code, not app code:
// markdown vendor 153.6, CodeMirror core 365.7 and languages 297.5 KiB. It
// was minimised first: only the required languages, one default theme, no
// all-language/all-theme aggregate packages, static Lezer highlighting for
// fenced blocks (no EditorView per block), and the replaced implementations
// deleted. The plan forbids returning to the hand-written renderers for
// budget reasons, so the total ceiling moves 1000 -> 1900 KiB (+5.9% over
// the measured total). The per-file 500 KiB gate and the CSS gate stay
// unchanged, so an unbounded regression still fails the build. Recorded in
// docs/acceptance/workflow-delivery-preview-2026-09-16.md and .agent/PLAN.md
// section 5.
const JS_BUDGET = 1900 * 1024
const JS_CHUNK_BUDGET = 500 * 1024
const CSS_BUDGET = 120 * 1024
const FONT_EXTENSIONS = new Set(['.woff2', '.woff', '.ttf', '.otf', '.eot'])

function* walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) yield* walk(path)
    else if (entry.isFile()) yield path
  }
}

const rows = []
let jsBytes = 0
let cssBytes = 0
let fontBytes = 0
let otherBytes = 0
const badFonts = []

for (const path of walk(DIST)) {
  const size = statSync(path).size
  const ext = path.slice(path.lastIndexOf('.')).toLowerCase()
  const rel = relative(DIST, path)
  if (ext === '.js') jsBytes += size
  else if (ext === '.css') cssBytes += size
  else if (FONT_EXTENSIONS.has(ext)) {
    fontBytes += size
    if (ext !== '.woff2') badFonts.push(rel)
  } else otherBytes += size
  rows.push({ rel, size })
}

rows.sort((a, b) => b.size - a.size)

const fmt = (bytes) => `${(bytes / 1024).toFixed(1)} KiB`
const totalBytes = jsBytes + cssBytes + fontBytes + otherBytes

console.log('\nBundle budget report')
console.log('====================')
for (const { rel, size } of rows.slice(0, 12)) {
  console.log(`  ${fmt(size).padStart(10)}  ${rel}`)
}
if (rows.length > 12) console.log(`  ... and ${rows.length - 12} more files`)
console.log('--------------------')
console.log(
  `  JS total:    ${fmt(jsBytes).padStart(10)}  (budget ${fmt(JS_BUDGET)})`,
)
console.log(
  `  JS chunk:    ${fmt(JS_CHUNK_BUDGET).padStart(10)}  (per .js file)`,
)
console.log(
  `  CSS total:   ${fmt(cssBytes).padStart(10)}  (budget ${fmt(CSS_BUDGET)})`,
)
console.log(`  Fonts total: ${fmt(fontBytes).padStart(10)}  (woff2 only)`)
console.log(`  Other:       ${fmt(otherBytes).padStart(10)}`)
console.log(`  All files:   ${fmt(totalBytes).padStart(10)}  (${rows.length} files)`)

const failures = []
if (jsBytes > JS_BUDGET) {
  failures.push(`JS total ${fmt(jsBytes)} exceeds budget ${fmt(JS_BUDGET)}`)
}
const oversized = rows.filter(({ rel, size }) => rel.endsWith('.js') && size > JS_CHUNK_BUDGET)
for (const { rel, size } of oversized) {
  failures.push(`JS chunk ${rel} is ${fmt(size)} (limit ${fmt(JS_CHUNK_BUDGET)})`)
}
if (cssBytes > CSS_BUDGET) {
  failures.push(`CSS total ${fmt(cssBytes)} exceeds budget ${fmt(CSS_BUDGET)}`)
}
if (badFonts.length > 0) {
  failures.push(`non-woff2 font assets: ${badFonts.join(', ')}`)
}

if (failures.length > 0) {
  console.error('\nBudget check FAILED:')
  for (const failure of failures) console.error(`  - ${failure}`)
  process.exit(1)
}

console.log('\nBudget check passed.')
