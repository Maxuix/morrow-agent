/**
 * 有界的行级差异（计划 output-preview-editor，子计划 2 的“查看差异”）。
 *
 * 冲突时用户需要看到磁盘版本与本地草稿的差别，但这里不引入 diff 库：行数
 * 超过上限时直接声明无法展示，绝不截断后假装是完整差异。纯函数，便于单测。
 */

export type DiffKind = 'equal' | 'add' | 'remove' | 'gap'

export interface DiffLine {
  kind: DiffKind
  text: string
  /** 1 起算的磁盘版本行号；新增行没有。 */
  before?: number
  /** 1 起算的草稿行号；删除行没有。 */
  after?: number
}

export interface LineDiffResult {
  lines: DiffLine[]
  /** 行数超限时为 true，lines 为空。 */
  tooLarge: boolean
  added: number
  removed: number
}

const MAX_DIFF_LINES = 800
const CONTEXT = 3

function splitLines(text: string): string[] {
  return text.split('\n')
}

/** 对比磁盘正文与草稿；每侧超过上限时声明 tooLarge，不做近似。 */
export function lineDiff(before: string, after: string, context = CONTEXT): LineDiffResult {
  const a = splitLines(before)
  const b = splitLines(after)
  if (a.length > MAX_DIFF_LINES || b.length > MAX_DIFF_LINES) {
    return { lines: [], tooLarge: true, added: 0, removed: 0 }
  }
  const width = b.length + 1
  const table = new Uint32Array((a.length + 1) * width)
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i * width + j] =
        a[i] === b[j]
          ? table[(i + 1) * width + (j + 1)] + 1
          : Math.max(table[(i + 1) * width + j], table[i * width + (j + 1)])
    }
  }
  const raw: DiffLine[] = []
  let added = 0
  let removed = 0
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      raw.push({ kind: 'equal', text: a[i], before: i + 1, after: j + 1 })
      i += 1
      j += 1
    } else if (table[(i + 1) * width + j] >= table[i * width + (j + 1)]) {
      raw.push({ kind: 'remove', text: a[i], before: i + 1 })
      removed += 1
      i += 1
    } else {
      raw.push({ kind: 'add', text: b[j], after: j + 1 })
      added += 1
      j += 1
    }
  }
  while (i < a.length) {
    raw.push({ kind: 'remove', text: a[i], before: i + 1 })
    removed += 1
    i += 1
  }
  while (j < b.length) {
    raw.push({ kind: 'add', text: b[j], after: j + 1 })
    added += 1
    j += 1
  }
  return { lines: withContext(raw, context), tooLarge: false, added, removed }
}

/** 折叠两端与中间的连续相同行，只保留改动附近 context 行。 */
function withContext(raw: DiffLine[], context: number): DiffLine[] {
  const keep = new Array<boolean>(raw.length).fill(false)
  raw.forEach((line, index) => {
    if (line.kind === 'equal') return
    for (let cursor = Math.max(0, index - context); cursor <= Math.min(raw.length - 1, index + context); cursor += 1) {
      keep[cursor] = true
    }
  })
  const result: DiffLine[] = []
  let skipping = false
  raw.forEach((line, index) => {
    if (keep[index]) {
      result.push(line)
      skipping = false
      return
    }
    if (!skipping) {
      result.push({ kind: 'gap', text: '…' })
      skipping = true
    }
  })
  return result
}
