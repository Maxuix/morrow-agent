import {
  ACTIVITY_FRAME_TEXT_BYTES,
  ACTIVITY_METADATA_LIMIT,
  ACTIVITY_PREVIEW_ITEM_BYTES,
  type ActivityFrame,
  type ActivityItem,
  type ActivityRecovery,
  type ActivityState as WireActivityState,
} from '../api/activity'
import type { ActivitySnapshot } from '../api/chat'
import type { ApiClient } from '../api/client'

/** One scope (workspace:session), one activity socket, bounded memory (P2.5). */

const RECONNECT_BASE_MS = 300
const RECONNECT_MAX_MS = 5000

export interface ActivityContentState { text: string; truncated: boolean }

export interface ActivityState {
  status: 'connecting' | 'live' | 'reconnecting' | 'offline'
  items: ActivityItem[]
  content: Record<string, ActivityContentState>
  activity_epoch: string
  activity_sequence: number
}

const TERMINAL_STATES = new Set<WireActivityState>(['succeeded', 'failed', 'cancelled', 'skipped'])
const ACTIVE_STATES = new Set<WireActivityState>(['preparing', 'waiting', 'running'])
const encoder = new TextEncoder()

const bytes = (value: string) => encoder.encode(value).length

/** Upsert by activity_id; stale revisions and terminal regressions are dropped. */
export function applyUpsert(items: ActivityItem[], incoming: ActivityItem): ActivityItem[] {
  const index = items.findIndex(item => item.activity_id === incoming.activity_id)
  if (index >= 0) {
    const current = items[index]
    if (incoming.revision < current.revision) return items
    if (TERMINAL_STATES.has(current.state) && !TERMINAL_STATES.has(incoming.state)) return items
    if (incoming.revision === current.revision) return items
    const next = items.slice()
    // Preserve the original start of the action; the newest facts win elsewhere.
    next[index] = {...incoming, started_at: incoming.started_at <= current.started_at ? incoming.started_at : current.started_at}
    return next
  }
  const grown = [...items, incoming]
  if (grown.length <= ACTIVITY_METADATA_LIMIT) return grown
  // Prefer evicting the oldest completed metadata; running items stay visible.
  const evictIndex = grown.findIndex(item => TERMINAL_STATES.has(item.state))
  if (evictIndex >= 0) return grown.filter((_, i) => i !== evictIndex)
  return grown.slice(1)
}

/** Append bounded projected text; unknown ids and oversize deltas are dropped. */
export function applyDelta(
  content: Record<string, ActivityContentState>,
  activityId: string,
  delta: string,
): Record<string, ActivityContentState> {
  if (!delta || bytes(delta) > ACTIVITY_FRAME_TEXT_BYTES) return content
  const current = content[activityId] ?? {text: '', truncated: false}
  if (bytes(current.text) + bytes(delta) > ACTIVITY_PREVIEW_ITEM_BYTES) {
    return {...content, [activityId]: {text: current.text, truncated: true}}
  }
  return {...content, [activityId]: {text: current.text + delta, truncated: current.truncated}}
}

/** Clear cached content for one activity (content gap contract). */
export function applyReset(
  content: Record<string, ActivityContentState>,
  activityId: string,
): Record<string, ActivityContentState> {
  if (!(activityId in content)) return content
  const next = {...content}
  delete next[activityId]
  return next
}

/** Rewrite one item's availability (delta/reset frames carry the server's). */
export function applyAvailability(
  items: ActivityItem[],
  activityId: string,
  availability: ActivityItem['availability'],
): ActivityItem[] {
  const index = items.findIndex(item => item.activity_id === activityId)
  if (index < 0 || items[index].availability === availability) return items
  const next = items.slice()
  next[index] = {...next[index], availability}
  return next
}

/**
 * Admission rekey (act_call_* → act_tool_*): the item and its cached content
 * move to the stable identity. An existing stable row wins (recovery may have
 * rebuilt it); the stale prepared row is dropped either way.
 */
export function applyRekey(
  items: ActivityItem[],
  content: Record<string, ActivityContentState>,
  fromActivityId: string,
  toActivityId: string,
): { items: ActivityItem[]; content: Record<string, ActivityContentState> } {
  const fromIndex = items.findIndex(item => item.activity_id === fromActivityId)
  const nextContent = {...content}
  const carried = nextContent[fromActivityId]
  delete nextContent[fromActivityId]
  if (carried && !(toActivityId in nextContent)) nextContent[toActivityId] = carried
  if (fromIndex < 0) return {items, content: nextContent}
  if (items.some(item => item.activity_id === toActivityId)) {
    return {items: items.filter((_, index) => index !== fromIndex), content: nextContent}
  }
  const next = items.slice()
  next[fromIndex] = {...next[fromIndex], activity_id: toActivityId}
  return {items: next, content: nextContent}
}

/**
 * Merge durable recovery skeletons with live items: identical tool execution
 * identity (agent_run_id + ordinal, durable call ids are normalized) never
 * appears twice; leftovers render as recovered history marked `unsaved`.
 */
export function mergeDurable(items: ActivityItem[], recovery: ActivityRecovery): ActivityItem[] {
  const liveKeys = new Set(items.map(liveKey))
  const seenIds = new Set(items.map(item => item.activity_id))
  const merged = [...items]
  for (const item of recovery.items) {
    if (seenIds.has(item.activity_id)) continue
    const key = liveKey(item)
    if (liveKeys.has(key)) continue
    seenIds.add(item.activity_id)
    merged.push(item)
  }
  return merged
}

const liveKey = (item: ActivityItem) =>
  item.kind === 'tool' && item.identity.agent_run_id && item.payload.kind === 'tool' && item.payload.ordinal
  ? `${item.identity.agent_run_id}#${item.payload.ordinal}`
  : item.activity_id

const parseTs = (value: string | null | undefined): number | null => {
  if (!value) return null
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? null : parsed
}

/**
 * Wall clock with frozen spans removed (P10.3, D14): paused, disconnected,
 * needs-recovery and settled-owner spans must not run a ghost timer, so the
 * elapsed time of a region excludes every span while its clock was frozen.
 * The readNow seam keeps this testable with fake timers.
 */
export class FrozenClock {
  private frozenAt: number | null = null
  private frozenTotalMs = 0

  constructor(private readonly readNow: () => number = Date.now) {}

  get frozen(): boolean {
    return this.frozenAt !== null
  }

  freeze(): void {
    if (this.frozenAt === null) this.frozenAt = this.readNow()
  }

  unfreeze(): void {
    if (this.frozenAt !== null) {
      this.frozenTotalMs += this.readNow() - this.frozenAt
      this.frozenAt = null
    }
  }

  /** Observation instant with all frozen spans excluded. */
  now(): number {
    const now = this.readNow()
    const frozenNow = this.frozenAt !== null ? now - this.frozenAt : 0
    return now - this.frozenTotalMs - frozenNow
  }
}

/** Total elapsed from server facts; hides the span when timing is unknown. */
export function durationMsText(startMs: number | null, endMs: number | null, now: number): string {
  if (startMs === null) return ''
  const total = Math.max(0, Math.floor(((endMs ?? now) - startMs) / 1000))
  if (total < 1) return ''
  if (total < 60) return `${total} 秒`
  return `${Math.floor(total / 60)} 分 ${total % 60} 秒`
}

export function durationText(startedAt: string, endedAt: string | null | undefined, now: number): string {
  return durationMsText(parseTs(startedAt), endedAt ? parseTs(endedAt) : null, now)
}

/**
 * Owner-terminal precedence (D13): when the run/turn that owns these items is
 * already terminal, a stale in-memory item may never keep the region "running".
 * Completed tool rows and thinking text stay visible; a step that was cut off
 * before its own end fact is reported as interrupted, never as a success.
 */
export function settleRunFacts(
  items: ActivityItem[],
  ownerStatus: string | null,
): RunFacts {
  const facts = runFacts(items)
  if (ownerStatus === null || !OWNER_TERMINAL.has(ownerStatus)) return facts
  const interrupted = items.some(
    item => !TERMINAL_STATES.has(item.state) || item.ended_at === null,
  )
  let endMs = facts.endMs
  for (const item of items) {
    const ended = parseTs(item.ended_at) ?? parseTs(item.updated_at)
    if (ended !== null && (endMs === null || ended > endMs)) endMs = ended
  }
  return {
    ...facts,
    active: false,
    waiting: false,
    endMs,
    interrupted,
    cancelled: facts.cancelled || ownerStatus === 'cancelled' || ownerStatus === 'superseded',
    failed: facts.failed || ownerStatus === 'failed',
  }
}

const OWNER_TERMINAL = new Set(['completed', 'failed', 'cancelled', 'superseded'])

// ---------------------------------------------------------------------------
// Run attribution: one execution region per request (chat turn) or workflow
// run. Regions render inside the transcript after their user message, so the
// activity belongs to the exchange it came from (never a detached tail list).
// ---------------------------------------------------------------------------

/** Stable region key: workflow run first, then chat turn, else session-level. */
export function runKeyOf(item: ActivityItem): string {
  if (item.identity.workflow_run_id) return `wf:${item.identity.workflow_run_id}`
  if (item.identity.turn_id) return `turn:${item.identity.turn_id}`
  return 'session'
}

export interface ActivityRun { key: string; items: ActivityItem[] }

/** Insertion-ordered run partition; each run renders as one execution region. */
export function groupByRun(items: ActivityItem[]): ActivityRun[] {
  const runs = new Map<string, ActivityRun>()
  for (const item of items) {
    const key = runKeyOf(item)
    let run = runs.get(key)
    if (!run) { run = {key, items: []}; runs.set(key, run) }
    run.items.push(item)
  }
  return [...runs.values()]
}

export interface RunFacts {
  hasItems: boolean
  /** Any non-terminal item exists. */
  active: boolean
  /** An approval is pending inside this run. */
  waiting: boolean
  /** Settled and at least one item failed. */
  failed: boolean
  /** Settled via cancellation/steer without a successful model response. */
  cancelled: boolean
  /** Owner ended while a step had no end fact of its own (D13). */
  interrupted: boolean
  startMs: number | null
  /** Settled wall-clock end; null while any item is still active. */
  endMs: number | null
  /** Distinct executing node runs (workflow); parallel count for the overview. */
  activeNodes: Set<string>
  modelFocus: ActivityItem | null
  toolFocus: ActivityItem | null
  /** Newest active item; the "last known state" after a disconnect. */
  focus: ActivityItem | null
  failedCount: number
}

/**
 * Run-level facts from item metadata only. The total duration comes from the
 * run's own start and settled end — never the sum of parallel node spans.
 */
export function runFacts(items: ActivityItem[]): RunFacts {
  const facts: RunFacts = {
    hasItems: items.length > 0, active: false, waiting: false, failed: false, cancelled: false, interrupted: false,
    startMs: null, endMs: null, activeNodes: new Set(), modelFocus: null, toolFocus: null,
    focus: null, failedCount: 0,
  }
  let endMs: number | null = null
  for (const item of items) {
    const start = parseTs(item.started_at)
    if (start !== null && (facts.startMs === null || start < facts.startMs)) facts.startMs = start
    if (ACTIVE_STATES.has(item.state)) {
      facts.active = true
      if (item.state === 'waiting') facts.waiting = true
      const node = item.identity.node_run_id
      if (node) facts.activeNodes.add(node)
      facts.focus = item
      if (item.kind === 'model') facts.modelFocus = item
      if (item.kind === 'tool') facts.toolFocus = item
    } else {
      const ended = parseTs(item.ended_at) ?? parseTs(item.updated_at)
      if (ended !== null && (endMs === null || ended > endMs)) endMs = ended
      if (item.state === 'failed') facts.failedCount++
    }
  }
  if (!facts.active) {
    facts.endMs = endMs
    // Run-level failure keys on the model outcome: a failed tool that the
    // model recovered from is a normal end with a per-item failure note.
    const modelSucceeded = items.some(i => i.kind === 'model' && i.state === 'succeeded')
    const modelFailed = items.some(i => i.kind === 'model' && i.state === 'failed')
    facts.failed = modelFailed || (!modelSucceeded && facts.failedCount > 0)
    // A steered turn closes the steered request as cancelled, then runs on;
    // only a run with no successful model response counts as stopped.
    facts.cancelled = !modelSucceeded && items.some(i => i.state === 'cancelled')
  }
  return facts
}

// ---------------------------------------------------------------------------
// Human-readable action copy. Everything derives from existing wire fields
// (payload.tool_name, safe_title target projected by Core); missing facts
// degrade to category nouns — targets are never invented.
// ---------------------------------------------------------------------------

export type ToolIcon = 'file' | 'search' | 'list' | 'terminal' | 'edit' | 'tool'

interface ToolVerb { icon: ToolIcon; phrases: Record<'done' | 'running' | 'failed', [string, string]> }

/** `{t}` is the projected target; the second phrase is the no-target form. */
const TOOL_VERBS: Record<string, ToolVerb> = {
  read: {icon: 'file', phrases: {done: ['已读取 {t}', '已读取文件'], running: ['正在读取 {t}', '正在读取文件'], failed: ['读取 {t} 失败', '读取文件失败']}},
  read_artifact: {icon: 'file', phrases: {done: ['已读取 {t}', '已读取产物'], running: ['正在读取 {t}', '正在读取产物'], failed: ['读取 {t} 失败', '读取产物失败']}},
  find: {icon: 'search', phrases: {done: ['已搜索 {t}', '已搜索'], running: ['正在搜索 {t}', '正在搜索'], failed: ['搜索 {t} 失败', '搜索失败']}},
  grep: {icon: 'search', phrases: {done: ['已搜索 {t}', '已搜索'], running: ['正在搜索 {t}', '正在搜索'], failed: ['搜索 {t} 失败', '搜索失败']}},
  search: {icon: 'search', phrases: {done: ['已搜索 {t}', '已搜索'], running: ['正在搜索 {t}', '正在搜索'], failed: ['搜索 {t} 失败', '搜索失败']}},
  ls: {icon: 'list', phrases: {done: ['已列出 {t} 中的文件', '已列出目录内容'], running: ['正在列出 {t} 中的文件', '正在列出目录内容'], failed: ['列出 {t} 失败', '列出目录内容失败']}},
  bash: {icon: 'terminal', phrases: {done: ['已运行 {t}', '已运行命令'], running: ['正在运行 {t}', '正在运行命令'], failed: ['运行 {t} 失败', '运行命令失败']}},
  run_command: {icon: 'terminal', phrases: {done: ['已运行 {t}', '已运行命令'], running: ['正在运行 {t}', '正在运行命令'], failed: ['运行 {t} 失败', '运行命令失败']}},
  edit: {icon: 'edit', phrases: {done: ['已修改 {t}', '已修改文件'], running: ['正在修改 {t}', '正在修改文件'], failed: ['修改 {t} 失败', '修改文件失败']}},
  write: {icon: 'edit', phrases: {done: ['已写入 {t}', '已写入文件'], running: ['正在写入 {t}', '正在写入文件'], failed: ['写入 {t} 失败', '写入文件失败']}},
}
/** Unknown tools stay factual with their real name — never a guessed verb. */
const FALLBACK_PHRASES: ToolVerb = {
  icon: 'tool',
  phrases: {done: ['已调用 {t}', '已调用'], running: ['正在调用 {t}', '正在调用'], failed: ['调用 {t} 失败', '调用失败']},
}

export interface ToolCopy {
  icon: ToolIcon
  /** Settled success phrasing, e.g. 已搜索 drawing.html. */
  rowLabel: string
  runningLabel: string
  failedLabel: string
  target: string | null
}

export function toolCopy(item: ActivityItem): ToolCopy {
  const payload = item.payload.kind === 'tool' ? item.payload : null
  const name = payload?.tool_name ?? ''
  const verb = TOOL_VERBS[name] ?? FALLBACK_PHRASES
  // Core projects safe_title as `{tool_name} {target}`; strip that known
  // prefix instead of parsing arguments. Absent target → category noun.
  const title = item.safe_title
  const target = name && title.startsWith(name) ? title.slice(name.length).trim() || null : title || null
  const fill = (template: string) => {
    const effective = verb === FALLBACK_PHRASES ? target ?? name : target
    return template.replace('{t}', effective ?? '').trim()
  }
  return {
    icon: verb.icon,
    rowLabel: fill(verb.phrases.done[target === null && verb !== FALLBACK_PHRASES ? 1 : 0]),
    runningLabel: fill(verb.phrases.running[target === null && verb !== FALLBACK_PHRASES ? 1 : 0]),
    failedLabel: fill(verb.phrases.failed[target === null && verb !== FALLBACK_PHRASES ? 1 : 0]),
    target,
  }
}

const GROUP_NOUNS: Record<ToolIcon, string> = {
  file: '个文件', search: '次', list: '个目录', terminal: '条命令', edit: '个文件', tool: '次',
}
const GROUP_VERBS: Record<Exclude<ToolIcon, 'tool'>, string> = {
  file: '读取', search: '搜索', list: '列出', terminal: '运行', edit: '修改',
}

/** Aggregate header for consecutive tool rows: 读取了 3 个文件，运行了 2 条命令. */
export function toolGroupSummary(items: ActivityItem[]): string {
  const counts = new Map<string, number>()
  for (const item of items) {
    const payload = item.payload.kind === 'tool' ? item.payload : null
    const key = TOOL_VERBS[payload?.tool_name ?? '']?.icon ?? 'tool'
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  const named = new Map<string, number>()
  for (const item of items) {
    const payload = item.payload.kind === 'tool' ? item.payload : null
    if (payload && !TOOL_VERBS[payload.tool_name]) {
      named.set(payload.tool_name, (named.get(payload.tool_name) ?? 0) + 1)
    }
  }
  const parts = [...counts.entries()]
    .filter(([icon]) => icon !== 'tool')
    .map(([icon, count]) => `${GROUP_VERBS[icon as Exclude<ToolIcon, 'tool'>]}了 ${count} ${GROUP_NOUNS[icon as ToolIcon]}`)
  for (const [name, count] of named) parts.push(`调用 ${name} ${count} 次`)
  return parts.join('，')
}

const MODEL_RUNNING: Record<string, string> = {
  awaiting_model: '等待模型响应',
  thinking: '正在思考',
  responding: '正在生成回复',
  tool_preparing: '正在准备工具调用',
}

/** Live label of any activity; the overview's "last known state" reuses it. */
export function activityRunningLabel(item: ActivityItem): string {
  if (item.kind === 'model' && item.payload.kind === 'model') {
    return MODEL_RUNNING[item.payload.stage] ?? '正在执行'
  }
  if (item.kind === 'tool' && item.payload.kind === 'tool') return toolCopy(item).runningLabel
  return '正在执行'
}

export type OverviewTone = 'normal' | 'attention' | 'failed'
export interface OverviewView {
  kind: 'empty' | 'waiting' | 'running' | 'sync' | 'settled'
  text: string
  tone: OverviewTone
}

/**
 * The collapsed overview answers exactly two questions: how is it going, and
 * how long. Event counts, idle hints and node durations stay in the details.
 */
export function runOverview(facts: RunFacts, opts: {
  busy?: boolean
  syncing?: boolean
  /** Stage-frame fallback when a node streams no activity items yet. */
  stageText?: string
  stageNodes?: number
  now: number
} = {now: 0}): OverviewView {
  if (!facts.hasItems) {
    if (opts.stageText) {
      return {kind: 'running', text: joinElapsed(opts.stageText, durationMsText(facts.startMs, null, opts.now)), tone: 'normal'}
    }
    return {kind: 'empty', text: opts.busy ? '等待执行' : '', tone: 'normal'}
  }
  if (facts.waiting) return {kind: 'waiting', text: '等待批准', tone: 'attention'}
  const elapsed = durationMsText(facts.startMs, null, opts.now)
  if (facts.active) {
    if (opts.syncing) {
      const last = facts.focus ? activityRunningLabel(facts.focus) : '未知状态'
      return {kind: 'sync', text: `状态同步中 · 上次状态：${last}`, tone: 'normal'}
    }
    if (facts.activeNodes.size > 1) {
      return {kind: 'running', text: joinElapsed(`${facts.activeNodes.size} 个节点执行中`, elapsed), tone: 'normal'}
    }
    if (facts.modelFocus) {
      return {kind: 'running', text: joinElapsed(activityRunningLabel(facts.modelFocus), elapsed), tone: 'normal'}
    }
    if (facts.toolFocus) {
      return {kind: 'running', text: joinElapsed(activityRunningLabel(facts.toolFocus), elapsed), tone: 'normal'}
    }
    return {kind: 'running', text: joinElapsed('正在执行', elapsed), tone: 'normal'}
  }
  const total = durationMsText(facts.startMs, facts.endMs, opts.now)
  // A settled run without its own start fact never shows an invented span.
  const totalLabel = total || (facts.startMs === null ? '时长未知' : '')
  if (facts.failed) return {kind: 'settled', text: joinElapsed('执行失败', totalLabel), tone: 'failed'}
  if (facts.interrupted) return {kind: 'settled', text: joinElapsed('已中断', totalLabel), tone: 'attention'}
  if (facts.cancelled) return {kind: 'settled', text: joinElapsed('已停止', totalLabel), tone: 'normal'}
  return {kind: 'settled', text: joinElapsed(total ? `用时 ${total}` : '已完成', facts.failedCount > 0 ? `${facts.failedCount} 项失败` : ''), tone: facts.failedCount > 0 ? 'attention' : 'normal'}
}

const joinElapsed = (text: string, elapsed: string, extra = '') =>
  [text, elapsed, extra].filter(Boolean).join(' · ')

// ---------------------------------------------------------------------------
// Content grouping inside one run: ordered parts per the display contract —
// progress/thinking text, consecutive tool groups, asset references, retries,
// approvals and control receipts keep their execution order.
// ---------------------------------------------------------------------------

export type RegionPart =
  | {kind: 'thinking'; item: ActivityItem}
  | {kind: 'stage'; item: ActivityItem}
  | {kind: 'tools'; items: ActivityItem[]}
  | {kind: 'assets'; items: ActivityItem[]}
  | {kind: 'retry'; item: ActivityItem}
  | {kind: 'approval'; item: ActivityItem}
  | {kind: 'control'; item: ActivityItem}
  | {kind: 'compaction'; item: ActivityItem}
  | {kind: 'node_output'; item: ActivityItem}

/**
 * Ordered parts for one run. Transient model stages fold away once finished
 * unless they carried projected thinking text; consecutive tool calls merge
 * into one group; items with a verifiable `preview_ref` become asset cards
 * instead of duplicate rows.
 */
export function buildParts(
  items: ActivityItem[],
  content: Record<string, ActivityContentState>,
): RegionPart[] {
  const parts: RegionPart[] = []
  let tools: ActivityItem[] = []
  let assets: ActivityItem[] = []
  const flushTools = () => { if (tools.length) { parts.push({kind: 'tools', items: tools}); tools = [] } }
  const flushAssets = () => { if (assets.length) { parts.push({kind: 'assets', items: assets}); assets = [] } }
  for (const item of items) {
    if (item.kind === 'tool') {
      if (item.preview_ref) {
        flushTools()
        assets.push(item)
      } else {
        flushAssets()
        tools.push(item)
      }
      continue
    }
    flushTools()
    flushAssets()
    if (item.kind === 'model') {
      const text = content[item.activity_id]?.text ?? ''
      if (text) parts.push({kind: 'thinking', item})
      else if (item.state === 'running') parts.push({kind: 'stage', item})
      // Finished stage markers without text fold away — no "✓ 正在思考 已完成".
    } else if (item.kind === 'approval' || item.kind === 'control' || item.kind === 'compaction' || item.kind === 'node_output') {
      parts.push({kind: item.kind, item})
    }
    // retry is a tool-adjacent marker rendered inside the flow below.
    if (item.kind === 'retry') parts.push({kind: 'retry', item})
  }
  flushTools()
  flushAssets()
  return parts
}

export interface RunNode {
  key: string
  label: string
  parts: RegionPart[]
  items: ActivityItem[]
}

/**
 * Node layer for workflow runs only; plain chat returns one unlabelled group.
 * Same-named nodes are disambiguated with their actual execution id suffix.
 */
export function buildRunNodes(
  items: ActivityItem[],
  content: Record<string, ActivityContentState>,
): {nodes: RunNode[]; plain: boolean} {
  const order: string[] = []
  const grouped = new Map<string, ActivityItem[]>()
  let plain = true
  for (const item of items) {
    const nodeRun = item.identity.node_run_id
    if (!nodeRun) continue
    plain = false
    const bucket = grouped.get(nodeRun)
    if (bucket) bucket.push(item)
    else { grouped.set(nodeRun, [item]); order.push(nodeRun) }
  }
  if (plain) return {nodes: [{key: 'run', label: '', parts: buildParts(items, content), items}], plain: true}
  const nodeIdCounts = new Map<string, number>()
  for (const nodeRun of order) {
    const nodeId = grouped.get(nodeRun)![0].identity.node_id
    if (nodeId) nodeIdCounts.set(nodeId, (nodeIdCounts.get(nodeId) ?? 0) + 1)
  }
  const trailing = items.filter(item => !item.identity.node_run_id)
  const nodes = order.map(nodeRun => {
    const nodeItems = grouped.get(nodeRun)!
    const nodeId = nodeItems[0].identity.node_id
    const label = nodeId
      ? (nodeIdCounts.get(nodeId)! > 1 ? `${nodeId} · ${nodeRun.slice(-6)}` : nodeId)
      : nodeRun.slice(-8)
    return {key: nodeRun, label, parts: buildParts(nodeItems, content), items: nodeItems}
  })
  if (trailing.length) {
    nodes.push({key: '__run__', label: '', parts: buildParts(trailing, content), items: trailing})
  }
  return {nodes, plain: false}
}

export interface ActivitySocket {
  onopen: (() => void) | null; onmessage: ((event: {data: string}) => void) | null
  onclose: (() => void) | null; send(text: string): void; close(): void
}

/** Transport frames on the activity socket: the three activity kinds plus the
 * shared control frames every stream carries. */
type ActivityStreamFrame =
  | ActivityFrame
  | {type: 'resync_required' | 'heartbeat'; stream_epoch: string; sequence: number}

export class ActivityStore {
  private state: ActivityState = {
    status: 'connecting', items: [], content: {}, activity_epoch: '', activity_sequence: 0,
  }
  private listeners = new Set<() => void>()
  private generation = 0
  private socket: ActivitySocket | null = null
  private timer: ReturnType<typeof setTimeout> | null = null
  private attempts = 0
  private stopped = false

  constructor(
    private client: ApiClient, readonly workspace: string, readonly session: string,
    private factory: (url: string) => ActivitySocket = url => new WebSocket(url) as ActivitySocket,
  ) {}

  getState = () => this.state
  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }
  private patch = (partial: Partial<ActivityState>) => {
    this.state = {...this.state, ...partial}
    for (const listener of this.listeners) listener()
  }

  async start(): Promise<void> {
    this.stopped = false
    const generation = ++this.generation
    this.closeSocket()
    this.patch({status: this.state.items.length ? 'reconnecting' : 'connecting'})
    try {
      const snapshot: ActivitySnapshot = await this.client.chatActivitySnapshot(this.workspace, this.session)
      if (generation !== this.generation) return
      let items = snapshot.activities ?? []
      try {
        const recovery = await this.client.chatActivities(this.workspace, this.session)
        if (generation !== this.generation) return
        items = mergeDurable(items, recovery)
      } catch { /* Recovery fetch failed: retain the live items. */ }
      this.patch({
        items, content: {}, activity_epoch: snapshot.activity_epoch, activity_sequence: snapshot.activity_sequence,
      })
      this.connect(generation, snapshot.activity_epoch, snapshot.activity_sequence)
    } catch {
      // Like the sync engine's retry loop: a transient snapshot failure backs
      // off and recovers instead of parking on offline forever.
      if (generation === this.generation && !this.stopped) this.reconnect()
    }
  }

  stop(): void {
    this.stopped = true
    this.generation++
    if (this.timer) { clearTimeout(this.timer); this.timer = null }
    this.closeSocket()
  }

  private closeSocket(): void {
    const socket = this.socket
    this.socket = null
    if (socket) {
      // Handlers go first: a closing socket must not re-enter reconnect().
      socket.onopen = null
      socket.onmessage = null
      socket.onclose = null
      try { socket.close() } catch { /* a half-open socket may throw */ }
    }
  }

  private connect(generation: number, epoch: string, after: number): void {
    if (this.stopped || generation !== this.generation || !epoch) return
    const socket = this.factory(this.client.chatSocketUrl(this.workspace, this.session))
    this.socket = socket
    socket.onopen = () => {
      if (generation !== this.generation) return
      this.attempts = 0
      socket.send(JSON.stringify({type: 'subscribe', stream_epoch: epoch, after_sequence: after, activity_schema: 1}))
    }
    socket.onmessage = event => {
      if (generation !== this.generation) return
      let frame: ActivityStreamFrame | null = null
      try { frame = JSON.parse(event.data) as ActivityStreamFrame } catch { return }
      if (!frame) return
      if (frame.type === 'resync_required' || frame.stream_epoch !== this.state.activity_epoch
        || frame.sequence > this.state.activity_sequence + 1) { this.reconnect(); return }
      if (frame.type === 'heartbeat') return
      if (frame.sequence <= this.state.activity_sequence) return
      this.applyFrame(frame as ActivityFrame)
    }
    socket.onclose = () => {
      if (generation === this.generation && !this.stopped) this.reconnect()
    }
  }

  private reconnect(): void {
    const generation = this.generation
    this.patch({status: 'reconnecting'})
    // Gap detection runs while the old socket is still open; close it (and
    // detach its handlers) so a retry can never leave a ghost connection.
    this.closeSocket()
    if (this.timer) clearTimeout(this.timer)
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** this.attempts, RECONNECT_MAX_MS)
    this.attempts++
    this.timer = setTimeout(() => {
      this.timer = null
      if (generation === this.generation && !this.stopped) void this.start()
    }, delay)
  }

  private applyFrame(frame: ActivityFrame): void {
    if (frame.type === 'activity_upsert') {
      const previous = this.state.items
      const items = applyUpsert(previous, frame.payload.item)
      // Bounded eviction drops the oldest completed row; its cached content
      // must leave with it or long sessions leak text without bound.
      let content = this.state.content
      if (items !== previous) {
        const kept = new Set(items.map(row => row.activity_id))
        const dropped = previous.filter(row => !kept.has(row.activity_id))
        if (dropped.length > 0) {
          content = {...content}
          for (const row of dropped) delete content[row.activity_id]
        }
      }
      this.patch({items, content, activity_sequence: frame.sequence, status: 'live'})
    } else if (frame.type === 'activity_rekey') {
      const {items, content} = applyRekey(
        this.state.items, this.state.content,
        frame.payload.from_activity_id, frame.payload.to_activity_id,
      )
      this.patch({items, content, activity_sequence: frame.sequence, status: 'live'})
    } else if (frame.type === 'activity_delta') {
      const content = applyDelta(this.state.content, frame.payload.activity_id, frame.payload.delta)
      const items = frame.payload.availability
        ? applyAvailability(this.state.items, frame.payload.activity_id, frame.payload.availability)
        : this.state.items
      this.patch({content, items, activity_sequence: frame.sequence, status: 'live'})
    } else if (frame.type === 'activity_content_reset') {
      const content = applyReset(this.state.content, frame.payload.activity_id)
      const items = applyAvailability(this.state.items, frame.payload.activity_id, 'evicted')
      this.patch({content, items, activity_sequence: frame.sequence, status: 'live'})
    }
  }
}
