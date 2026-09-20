import type { ExecutionItem, ExecutionView } from '../../api/chat'
import type { GuiControlRequest, GuiPauseRequest } from '../../api/contracts'
import type { NodeExecutionWire } from '../../api/types'
import { NODE_EXECUTION_LABELS } from './labels'
import { chatPath } from '../../api/chat'

/**
 * Shared execution-state selection for the Chat surface. Header, composer,
 * session actions and stop routing all read these helpers so run state and
 * input capability can never diverge between surfaces.
 *
 * The projection is advisory: every routed control is still validated by its
 * own endpoint against durable facts.
 */

export function executionBusy(execution: ExecutionView | null | undefined): boolean {
  return !!execution && execution.state !== 'idle'
}

/** True while the user's stop intent is recorded but not yet settled. */
export function executionStopping(execution: ExecutionView | null | undefined): boolean {
  return !!execution && (execution.state === 'stopping' || execution.state === 'pausing')
}

/** Header/status text for the current execution, or null when idle. */
export function executionLabel(execution: ExecutionView | null | undefined): string | null {
  if (!executionBusy(execution) || execution === null || execution === undefined) return null
  if (execution.state === 'needs_recovery') return '执行待恢复'
  if (execution.state === 'waiting_approval') return '等待工具审批'
  if (execution.state === 'stopping') return '正在停止…'
  if (execution.state === 'pausing') return '正在暂停…'
  if (execution.state === 'paused') return '已暂停'
  if (execution.state === 'queued') return '排队中'
  const who =
    execution.owner === 'workflow'
      ? '工作流执行中'
      : execution.owner === 'planning'
        ? '正在生成计划'
        : '正在运行'
  return who + (execution.node_run_ids.length > 1 ? ` · ${execution.node_run_ids.length} 个并行节点` : '')
}

export interface ExecutionStopTarget {
  kind: 'chat' | 'workflow' | 'planning' | 'planning_generation'
  workflowRunId?: string
  operationId?: string
}

/**
 * Where the composer stop button must route for the current primary
 * execution: ordinary chat keeps its queue stop, Workflow runs go through
 * the run cancel endpoint, plan generation cancels its operation. Null when
 * nothing is running or the backend advertises no stop action.
 */
export function executionStopTarget(
  execution: ExecutionView | null | undefined,
): ExecutionStopTarget | null {
  if (!executionBusy(execution) || execution === null || execution === undefined) return null
  if (!execution.allowed_actions.includes('stop')) return null
  if (execution.owner === 'workflow' && execution.workflow_run_id !== null) {
    return { kind: 'workflow', workflowRunId: execution.workflow_run_id }
  }
  if (execution.owner === 'planning' && execution.planning_operation_id !== null) {
    return { kind: 'planning', operationId: execution.planning_operation_id }
  }
  return { kind: 'chat' }
}

/**
 * Whether the composer should accept ordinary input. Isolated workflow node
 * sessions are execution detail; the backend rejects submissions regardless.
 */
export function executionAcceptsInput(execution: ExecutionView | null | undefined): boolean {
  if (!execution) return true
  return execution.accepts_chat_input !== false
}

/** Pick the execution of one owner from the projection, if present. */
export function executionOfOwner(
  execution: ExecutionView | null | undefined,
  owner: ExecutionItem['owner'],
): ExecutionItem | null {
  if (!execution) return null
  if (execution.owner === owner) return execution
  return execution.executions.find((item) => item.owner === owner) ?? null
}

// ---------------------------------------------------------------------------
// P09.1: unified pause/continue derivation (D01). The composer's primary
// control and its independent secondary cancel both derive from ONE
// execution/control snapshot: the server-projected `state` plus
// `allowed_actions`. The client never invents an action the projection did
// not offer, and cancel is never folded into pause/resume.
// ---------------------------------------------------------------------------

export type ExecutionControlKind = 'pause' | 'resume'

/**
 * Control scope (B-line coordination, A09): the planning owner exposes two
 * disjoint action pairs — run-scoped `pause`/`resume` (a workflow run exists)
 * and generation-scoped `pause_generation`/`resume_generation` (only the
 * planning operation exists). They never co-occur; the scope decides which
 * store path runs, so a generation press can never hit the run endpoints.
 */
export type ExecutionControlScope = 'run' | 'generation'

const PAUSE_ACTIONS: ReadonlyArray<{action: string; scope: ExecutionControlScope}> = [
  {action: 'pause', scope: 'run'},
  {action: 'pause_generation', scope: 'generation'},
]
const RESUME_ACTIONS: ReadonlyArray<{action: string; scope: ExecutionControlScope}> = [
  {action: 'resume', scope: 'run'},
  {action: 'resume_generation', scope: 'generation'},
]

export interface ExecutionPrimaryControl {
  kind: ExecutionControlKind
  scope: ExecutionControlScope
  /** Visible label; settling states announce the pending transition. */
  label: string
  /** Intent accepted but not settled; the button waits, disabled. */
  settling: boolean
}

export interface ExecutionControlView {
  primary: ExecutionPrimaryControl | null
  /** Independent secondary cancel; separate from pause/resume (D01). */
  cancel: { available: boolean; settling: boolean }
}

const IDLE_CONTROLS: ExecutionControlView = {
  primary: null,
  cancel: { available: false, settling: false },
}

export function executionControls(
  execution: ExecutionView | null | undefined,
): ExecutionControlView {
  if (!execution || execution.state === 'idle') return IDLE_CONTROLS
  const allowed = new Set(execution.allowed_actions)
  let primary: ExecutionPrimaryControl | null = null
  if (execution.state === 'pausing') {
    primary = {kind: 'pause', scope: 'run', label: '正在暂停…', settling: true}
  } else if (execution.state === 'stopping') {
    primary = null
  } else if (execution.state === 'paused') {
    const resume = RESUME_ACTIONS.find(candidate => allowed.has(candidate.action))
    primary = resume
      ? {
          kind: 'resume',
          scope: resume.scope,
          label: resume.scope === 'generation' ? '继续生成' : '继续',
          settling: false,
        }
      : null
  } else {
    const pause = PAUSE_ACTIONS.find(candidate => allowed.has(candidate.action))
    primary = pause
      ? {
          kind: 'pause',
          scope: pause.scope,
          label: pause.scope === 'generation' ? '暂停生成' : '暂停',
          settling: false,
        }
      : null
  }
  return {
    primary,
    cancel: {
      available: allowed.has('stop') && execution.state !== 'stopping',
      settling: execution.state === 'stopping',
    },
  }
}

/**
 * Route a pause/resume press by execution ownership. Workflow runs go through
 * the run pause/resume endpoints, planning through its own control channel
 * (run scope) or the planning-operation endpoints (generation scope), and the
 * ordinary chat owner through the session control channel. Null when the
 * projection did not offer the action or the owner carries no identity.
 */
export function executionControlTarget(
  execution: ExecutionView | null | undefined,
  kind: ExecutionControlKind,
): ExecutionStopTarget | null {
  if (!execution || execution.state === 'idle') return null
  const allowed = new Set(execution.allowed_actions)
  const candidates = kind === 'pause' ? PAUSE_ACTIONS : RESUME_ACTIONS
  const matched = candidates.find(candidate => allowed.has(candidate.action))
  if (!matched) return null
  if (execution.owner === 'workflow' && execution.workflow_run_id !== null) {
    return { kind: 'workflow', workflowRunId: execution.workflow_run_id }
  }
  if (execution.owner === 'planning' && execution.planning_operation_id !== null) {
    return matched.scope === 'generation'
      ? { kind: 'planning_generation', operationId: execution.planning_operation_id }
      : { kind: 'planning', operationId: execution.planning_operation_id }
  }
  if (execution.owner === 'chat') return { kind: 'chat' }
  return null
}

/**
 * How a plain "send" routes while the execution is paused (D07): the paused
 * task owns the continuation, independent of the workflowOn toggle. Pure
 * continue text on a paused chat queue takes the durable queue continuation;
 * any other text on a paused planning/workflow execution goes through the
 * server-side control classification (continue / correction / question).
 * Null means ordinary send routing applies.
 */
export function pausedSendRoute(
  execution: ExecutionView | null | undefined,
  text: string,
  queuePaused: boolean | undefined,
  isPureContinue: (value: string) => boolean,
): 'queue_continue' | 'control' | null {
  if (!execution || execution.state !== 'paused') return null
  if (execution.owner === 'chat') {
    return queuePaused && isPureContinue(text) ? 'queue_continue' : null
  }
  if (execution.owner === 'planning' || execution.owner === 'workflow') {
    return text.trim() ? 'control' : null
  }
  return null
}

/**
 * BUG-GUI-001: an explicit pause word typed while a planning/workflow
 * execution is live routes to the same server-side control channel as the
 * pause button, instead of being queued as an ordinary message. 'pausing'
 * covers an idempotent repeat pause; the chat owner is never rerouted here.
 * Null means ordinary send routing applies.
 */
export function runningControlRoute(
  execution: ExecutionView | null | undefined,
  text: string,
  isExplicitPause: (value: string) => boolean,
): 'control' | null {
  if (!execution) return null
  if (execution.owner !== 'planning' && execution.owner !== 'workflow') return null
  if (
    execution.state !== 'running' &&
    execution.state !== 'pausing' &&
    execution.state !== 'waiting_approval'
  ) {
    return null
  }
  if (!text.trim()) return null
  return isExplicitPause(text) ? 'control' : null
}

// ---------------------------------------------------------------------------
// Frozen wire request builders (contract §7). The bodies match the frozen
// `GuiControlRequest` / `GuiPauseRequest` shapes from the contracts module
// exactly; the server-side acceptance of the text/pause shapes is wired by the
// coordinator, so until then the endpoints may reject these bodies.
// ---------------------------------------------------------------------------

export function guiControlRequestBody(input: {
  commandId: string
  sessionId: string
  text: string
  clientMessageId?: string | null
}): GuiControlRequest {
  return {
    command_id: input.commandId,
    session_id: input.sessionId,
    text: input.text,
    client_message_id: input.clientMessageId ?? null,
  }
}

export function guiPauseRequestBody(input: {
  commandId: string
  sessionId: string
  expectedRunRowVersion?: number | null
}): GuiPauseRequest {
  return {
    command_id: input.commandId,
    session_id: input.sessionId,
    expected_run_row_version: input.expectedRunRowVersion ?? null,
  }
}

/**
 * BUG-GUI-002 (P02): per-node execution projection derivation. A paused run
 * keeps its interrupted nodes' business status at `running`; the server
 * projects the execution state separately. `undefined` (pre-P02 server) is
 * conservative: only the business status shows, never a fabricated pause.
 */
export function nodeExecutionBadge(
  execution: NodeExecutionWire | null | undefined,
): string | null {
  if (execution === undefined || execution === null) return null
  if (execution.state === 'running') return null
  return NODE_EXECUTION_LABELS[execution.state]
}

/** "运行中 · 已暂停" — business label plus execution badge when projected. */
export function nodeStatusText(
  businessStatus: string,
  execution: NodeExecutionWire | null | undefined,
): string {
  const badge = nodeExecutionBadge(execution)
  return badge === null ? businessStatus : `${businessStatus} · ${badge}`
}

/**
 * Region clocks freeze while a pause is requested or settled: elapsed time
 * excludes the pausing/paused span instead of running a ghost timer. `stopping`
 * keeps its own semantics (cancel settlement, not a pause wait).
 */
export function executionTimingFrozen(
  execution: ExecutionView | null | undefined,
): boolean {
  return (
    execution?.state === 'pausing' ||
    execution?.state === 'paused' ||
    execution?.state === 'needs_recovery'
  )
}

/**
 * Pause entry for the ordinary chat turn (frozen `GuiPauseRequest`). The
 * session-scoped path mirrors the run- and plan-scoped pause routes; the
 * server route is coordinator-wired, so the GUI keeps this in one place.
 */
export const chatPausePath = (workspace: string, session: string) =>
  `${chatPath(workspace, session)}/pause`
