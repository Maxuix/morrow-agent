import type {
  PlanningOperationWire,
  TaskPlanViewWire,
  WorkflowDefinitionSourceWire,
  WorkflowStatus,
} from '../../api/types'

/**
 * Server-derived phases for the Chat task-plan panel. Every phase maps to a
 * text label — state is never conveyed by color alone.
 */
export type TaskPlanPhase =
  | 'idle'
  | 'generating'
  | 'needs_input'
  | 'invalid'
  | 'ready'
  | 'error'
  | 'running'
  | 'pausing'
  | 'paused'
  | 'completed'

const RUN_TERMINAL: ReadonlySet<WorkflowStatus> = new Set([
  'completed',
  'failed',
  'cancelled',
  'superseded',
])

export function runIsTerminal(status: WorkflowStatus): boolean {
  return RUN_TERMINAL.has(status)
}

/** Last operation by creation order; the wire keeps the page sorted by id/time. */
export function latestOperation(view: TaskPlanViewWire): PlanningOperationWire | null {
  const items = view.operations
  if (items.length === 0) return null
  return items.reduce((last, item) => (item.created_at >= last.created_at ? item : last))
}

/** Phase derived exclusively from the current server control projection. */
const CONTROL_PHASE: Record<TaskPlanViewWire['control']['state'], TaskPlanPhase> = {
  none: 'idle',
  draft: 'ready',
  generating: 'generating',
  // A terminal generation left no draft: the failure must surface as an
  // error, never as a perpetual "generating" state.
  generate_failed: 'error',
  running: 'running',
  draining: 'pausing',
  paused: 'paused',
  repair_ready: 'ready',
  repair_draft: 'needs_input',
  terminal: 'completed',
}

export function taskPlanPhase(view: TaskPlanViewWire | null): TaskPlanPhase {
  if (view === null) return 'idle'
  if (view.binding === null) return 'idle'
  const phase = CONTROL_PHASE[view.control.state]
  if (phase === 'ready') {
    // A draft that is present but not admissible is needs_input/invalid.
    if (view.draft !== null && view.draft.draft.status === 'invalid') return 'invalid'
    if (!view.execution.allowed) return 'needs_input'
  }
  return phase
}

const PHASE_LABELS: Record<TaskPlanPhase, string> = {
  idle: '未生成计划',
  generating: '正在生成计划…',
  needs_input: '计划需补充后才能开始',
  invalid: '计划无效，需修复',
  ready: '计划就绪',
  error: '生成失败',
  running: '计划执行中',
  pausing: '正在暂停…',
  paused: '已暂停',
  completed: '计划已结束',
}

export function taskPlanPhaseLabel(phase: TaskPlanPhase): string {
  return PHASE_LABELS[phase]
}

/** Matches Core `range(3)` / `attempt BETWEEN 0 AND 2` on planning requests. */
export const PLANNING_ATTEMPT_LIMIT = 3

/** In-flight planning copy; attempt counts come from the server projection.
 *  No wall-clock stopwatch: live elapsed text is never shown (D14). */
export function planningProgressText(operation: PlanningOperationWire): string {
  const recorded = operation.usage?.attempts ?? 0
  const current = Math.min(Math.max(recorded, 1), PLANNING_ATTEMPT_LIMIT)
  return `正在生成计划 · ${current}/${PLANNING_ATTEMPT_LIMIT}`
}

/** Sanitized provider failure codes → display reason; unknown codes stay generic. */
const PROVIDER_REASON_LABELS: Record<string, string> = {
  auth: '凭据被拒绝',
  network: '网络不可达',
  rate_limit: '请求被限流',
  timeout: '等待超时',
  invalid_response: '响应无效',
  context_overflow: '任务上下文超限',
  internal: '服务端内部错误',
  credential_missing: '未配置凭据',
}

/** Stable user-facing text for planning diagnostics; unknown codes stay as-is. */
export function planningDiagnosticText(diagnostics: string[]): string {
  return diagnostics
    .map((item) => {
      if (item.startsWith('invalid_plan')) {
        return '计划校验失败，请修改任务描述后重试。'
      }
      if (item.includes('planning_provider_unavailable')) {
        const detail = /(?:^|:)planning_provider_unavailable(?::([\w-]+))?/.exec(item)?.[1]
        const reason = detail ? PROVIDER_REASON_LABELS[detail] : undefined
        return `规划模型暂时不可用${reason ? `（${reason}）` : ''}。请稍后重试。`
      }
      return item
    })
    .join('；')
}

export function taskPlanStatusHint(
  phase: TaskPlanPhase,
  view: TaskPlanViewWire | null,
  operation: PlanningOperationWire | null,
): string {
  if (phase === 'generating' && operation !== null && view?.binding) return planningProgressText(operation)
  return ''
}

/** Human-readable blockers from `AdmissionPreview.blockers`. */
export function blockerText(blockers: string[]): string {
  return blockers
    .map((code) =>
      code === 'invalid_draft'
        ? '当前草稿未通过完整校验'
        : code === 'execution_selection_incompatible'
          ? '模型或工具与可用执行设置不兼容，请调整后重试'
          : code === 'plan_missing'
            ? '尚无当前计划草稿'
            : code,
    )
    .join('；')
}

/**
 * Stable retry identity for a planning submission: the same objective, base
 * version and operation reuse one command ID so a lost response can be
 * reconciled without duplicating work (Core replays the receipt).
 */
export function planningRequestKey(
  operation: 'generate' | 'revise',
  objective: string,
  baseDraftVersion: number,
): string {
  return JSON.stringify({ operation, objective, baseDraftVersion })
}

/**
 * `/workflow <目标>` and `/plan <目标>` keep the parameter text as the task
 * objective; the bare command keeps the existing input. Anything quoted,
 * inside code blocks or under a path never reaches this parser because the
 * command must be the first token of the message.
 */
export function workflowTarget(fullText: string): string {
  const trimmed = fullText.trim()
  const match = /^\/(?:workflow|plan)\b\s*/.exec(trimmed)
  return match === null ? '' : trimmed.slice(match[0].length).trim()
}

/** Dependency parents of one node from the draft source edges. */
export function nodeParents(source: WorkflowDefinitionSourceWire, nodeId: string): string[] {
  return source.edges
    .filter((edge) => edge.to_node_id === nodeId)
    .map((edge) => edge.from_node_id)
}

/**
 * The explicit start stays disabled with unsaved edits, a lost connection or
 * any non-ready phase — text next to the button names the blocking reason.
 */
export function canStartPlan(options: {
  phase: TaskPlanPhase
  busy: boolean
  unsaved: boolean
  connected: boolean
}): boolean {
  return options.phase === 'ready' && !options.busy && !options.unsaved && options.connected
}

/** `preset:x` / `custom:id` → display label. */
export function agentSelectionLabel(agent: string): string {
  if (agent === 'preset:general') return 'General · 通用执行'
  if (agent === 'preset:explore') return 'Explore · 只读探查'
  if (agent === 'preset:review') return 'Review · 只读审查'
  if (agent.startsWith('custom:')) return `自定义 ${agent.slice('custom:'.length)}`
  return agent
}

/**
 * Scenario rule for which surface leads the panel: while a run exists and no
 * modification candidate / change-mode draft is open, the frozen run topology
 * is the default view (executing, paused-without-candidate, or finished
 * history). Any current draft — initial, change, or repair — leads instead as
 * the editable review. The user can toggle while both exist.
 */
export function planRunTopologyDefault(view: TaskPlanViewWire | null): boolean {
  if (view === null || view.run === null) return false
  if (view.binding?.mode === 'change' || view.binding?.mode === 'repair') return false
  if (view.candidate !== null) return false
  return true
}

/**
 * Conservative client-side matcher for explicit start phrasing, used only to
 * enforce the shared unsaved-edit guard in the chat path. The server remains
 * the final authority on every start request (version + digest re-checked).
 */
export function looksLikeStartCommand(text: string): boolean {
  const trimmed = text.trim()
  if (trimmed === '') return false
  return /^(开始|启动|start\b|run\b|go\b)/i.test(trimmed) || /开始执行|开始吧|启动吧/.test(trimmed)
}

/** Model resolution provenance → display label. */
export function modelSourceLabel(source: string): string {
  if (source === 'session') return '会话'
  if (source === 'agent') return 'Agent 设置'
  if (source === 'adapter_default') return '适配器默认'
  return source
}

export const RESPONSIBILITY_LABELS: Record<string, string> = {
  implementation: '实现',
  research: '调研',
  review: '审查',
  synthesis: '汇总',
  general: '通用',
}

/**
 * One-line durable receipt label for a control input. The status is the
 * server's own acceptance outcome (D06/D07) — never a local guess.
 */
export function controlReceiptLabel(receipt: {
  status: string
  message?: string | null
  error_code?: string | null
}): string {
  switch (receipt.status) {
    case 'accepted': return '发送中 · 已接纳，等待处理'
    case 'executed': return '已执行'
    case 'acknowledged': return receipt.message ?? '无需操作'
    case 'answered': return receipt.message ?? '已回答'
    case 'needs_choice': return receipt.message ?? '需要明确选择'
    case 'unresolved': return receipt.message ?? '无操作'
    case 'steer': return '已作为运行中纠正发送'
    case 'failed': return `处理失败${receipt.error_code ? ` · ${receipt.error_code}` : ''}`
    default: return receipt.message ?? '已接纳'
  }
}

/**
 * Client-side mirror of Core's pure continue words. Used only to route a
 * paused *chat queue* continuation away from workflow resume (S2.6/S4.6);
 * the server remains the authority for every workflow control decision.
 */
export function isPureContinueCommand(text: string): boolean {
  const value = text.trim().toLowerCase()
  if (!value) return false
  if (/(别|不要|先不|取消|暂停|stop|cancel|don'?t|do not|如果|若|一旦|除非|\?|？|吗)/.test(value)) return false
  if (!/(继续|接着|恢复|continue|resume)/.test(value)) return false
  const remainder = value.replace(/继续|接着|恢复|continue|resume|执行|运行|下去|吧|了|一下|现在|请|。|！|，|!|,|\.|\s/g, '')
  return remainder === ''
}

/**
 * Client-side mirror of Core's tightened `classify_pause_request`
 * (BUG-GUI-001): an unqualified pause word with no negation, no change
 * wording, no condition/question/reference, no explanation request and no
 * backtick code reference. Routing-only — the server re-validates every
 * control text against durable facts.
 */
export function isExplicitPauseCommand(text: string): boolean {
  const value = text.trim().toLowerCase()
  if (!value) return false
  if (value.includes('`')) return false
  if (/(别|不要|不用|先不|取消|cancel|abort|don'?t|do not)/.test(value)) return false
  if (/(改|修改|修复|增加|加一|删|加上|调整|换|change|edit|add|remove|update)/.test(value)) return false
  if (/(如果|若|一旦|只要|除非|先[\s\S]{0,16}再|等[\s\S]{0,16}再|(完成|做完|改完|改好)再|再开始|\b(if|when|after|until|once|then)\b)/.test(value)) return false
  if (/(？|\?|吗|呢|能不能|是否|可不可以|好不好)/.test(value)) return false
  if (/(他说|她说|资料显示|文档里|文档中|引用|据说|quoted|according to)/.test(value)) return false
  if (/(解释|说明|什么是|什么意思|原理|含义|explain|what is)/.test(value)) return false
  return /(暂停|先停|停一下|pause|hold)/.test(value)
}
