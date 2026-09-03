#!/usr/bin/env node
/*
 * Bundle budget check for the GUI build output. Plain node, no deps.
 * Fails (exit 1) if:
 *   - total JS across dist exceeds 700 KiB
 *   - total CSS across dist exceeds 120 KiB
 *   - any font asset is not .woff2
 */
import { readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const DIST = fileURLToPath(new URL('../../src/morrow/gui_static', import.meta.url))

const JS_BUDGET = 700 * 1024
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
  `  CSS total:   ${fmt(cssBytes).padStart(10)}  (budget ${fmt(CSS_BUDGET)})`,
)
console.log(`  Fonts total: ${fmt(fontBytes).padStart(10)}  (woff2 only)`)
console.log(`  Other:       ${fmt(otherBytes).padStart(10)}`)
console.log(`  All files:   ${fmt(totalBytes).padStart(10)}  (${rows.length} files)`)

const failures = []
if (jsBytes > JS_BUDGET) {
  failures.push(`JS total ${fmt(jsBytes)} exceeds budget ${fmt(JS_BUDGET)}`)
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
