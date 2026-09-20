import type {
  ApprovalDecisionWire,
  ApprovalResolution,
  ApprovalResolveResultWire,
  ApprovalWire,
} from '../api/types'
import { APPROVAL_DECISION_LABELS } from '../views/lib/labels'

/** Local UI phases; the durable approval resolution remains server-owned. */
export type ApprovalDecisionPhase =
  | 'pending'
  | 'submitting'
  | 'approved'
  | 'denied'
  | 'expired'
  | 'already_resolved'
  | 'conflict'
  | 'outcome_unknown'

export interface ApprovalDecisionState {
  approvalId: string
  phase: ApprovalDecisionPhase
  decision: ApprovalDecisionWire | null
  /** Kept only for idempotent retries; never rendered in the UI. */
  commandId: string | null
  result: ApprovalResolveResultWire | null
  message: string | null
}

export type ApprovalDecisionEvent =
  | { type: 'submit'; decision: ApprovalDecisionWire; commandId: string }
  | { type: 'resolved'; decision: ApprovalDecisionWire; result: ApprovalResolveResultWire }
  | { type: 'expired'; message?: string }
  | { type: 'conflict'; message?: string }
  | { type: 'outcome_unknown'; message?: string }
  | { type: 'reconciled'; approval: ApprovalWire }

/** The server advertises whether a session-scoped decision is legal. */
export function approvalDecisionOptions(
  approval: Pick<ApprovalWire, 'session_scope_allowed' | 'risk_level'>,
): ApprovalDecisionWire[] {
  return approval.session_scope_allowed && approval.risk_level !== 'high'
    ? ['allow_once', 'deny', 'allow_session']
    : ['allow_once', 'deny']
}

/** Badge treatment per risk tier: every tone also has a visible border. */
export function riskBadgeClass(riskLevel: ApprovalWire['risk_level']): string {
  switch (riskLevel) {
    case 'low':
      return 'border-completed text-completed'
    case 'medium':
      return 'border-paused text-paused'
    case 'high':
      return 'border-blocked text-blocked'
  }
}

export const APPROVAL_DECISION_HINTS: Record<ApprovalDecisionWire, string> = {
  allow_once: '仅允许此次工具调用；同范围的后续请求仍需单独审批。',
  deny: '拒绝此次工具调用；节点会收到拒绝结果并据此收尾。',
  allow_session: '仅相同工具与操作范围在本会话内免审批；该决定会被持久记录。',
}

/** Human copy for safe server-projected effect categories. */
export function approvalEffectLabel(effectClass: string): string {
  const labels: Record<string, string> = {
    read_only: '读取信息',
    workspace_read: '读取工作区',
    workspace_write: '修改工作区文件',
    reconcileable_file_write: '修改工作区文件',
    external_side_effect: '执行外部操作',
    network_request: '发起网络请求',
    unconfined_host: '使用 Host 环境',
  }
  return labels[effectClass] ?? '执行受控工具操作'
}

/** Keep protocol scope values understandable without exposing grant internals. */
export function approvalScopeLabel(scope: string): string {
  const clean = scope.replace(/^session:/, '')
  const labels: Record<string, string> = {
    workspace_read: '工作区读取',
    workspace_write: '工作区写入',
    reconcileable_file_write: '可恢复的文件写入',
    external_side_effect: '外部副作用',
  }
  return labels[clean] ?? clean
}

/** Do not let a stale client render a decision button after the expiry time. */
export function isApprovalExpired(approval: Pick<ApprovalWire, 'expires_at'>, now = Date.now()): boolean {
  const expiresAt = Date.parse(approval.expires_at)
  return Number.isFinite(expiresAt) && now >= expiresAt
}

export function approvalPhaseFromWire(
  approval: Pick<ApprovalWire, 'resolution' | 'expires_at'>,
  now = Date.now(),
): ApprovalDecisionPhase {
  if (approval.resolution === 'pending') return isApprovalExpired(approval, now) ? 'expired' : 'pending'
  if (approval.resolution === 'approved') return 'approved'
  if (approval.resolution === 'denied') return 'denied'
  return 'expired'
}

export function initialApprovalDecisionState(approval: ApprovalWire): ApprovalDecisionState {
  const phase = approvalPhaseFromWire(approval)
  return {
    approvalId: approval.approval_id,
    phase: phase === 'pending' || phase === 'expired' ? phase : 'already_resolved',
    decision: null,
    commandId: null,
    result: null,
    message: phase === 'expired' ? '此请求已过期，工具不会执行。' : null,
  }
}

function finalPhase(resolution: ApprovalResolution): ApprovalDecisionPhase {
  if (resolution === 'approved') return 'approved'
  if (resolution === 'denied') return 'denied'
  return 'expired'
}

function decisionMatchesResolution(decision: ApprovalDecisionWire | null, resolution: ApprovalResolution): boolean {
  if (decision === 'deny') return resolution === 'denied'
  if (decision === 'allow_once' || decision === 'allow_session') return resolution === 'approved'
  return false
}

function resolvedMessage(decision: ApprovalDecisionWire | null, resolution: ApprovalResolution): string {
  if (resolution === 'approved' && decision === 'allow_session') {
    return '已允许本次执行，并记录本会话相同工具与操作范围免批。'
  }
  if (resolution === 'approved') return '已允许本次执行，等待工具结果；决定不等于工具已成功。'
  if (resolution === 'denied') return '已拒绝此次操作，工具不会执行。'
  return '此请求已过期，工具不会执行。'
}

/** Pure transition table used by the hook and by node-level tests. */
export function reduceApprovalDecision(
  state: ApprovalDecisionState,
  event: ApprovalDecisionEvent,
): ApprovalDecisionState {
  switch (event.type) {
    case 'submit':
      if (!['pending', 'outcome_unknown', 'conflict'].includes(state.phase)) return state
      return {
        ...state,
        phase: 'submitting',
        decision: event.decision,
        commandId: event.commandId,
        result: null,
        message: '正在提交决定…',
      }
    case 'resolved': {
      const resolution = event.result.approval.resolution
      if (resolution === 'pending') {
        return {
          ...state,
          phase: 'outcome_unknown',
          decision: event.decision,
          result: event.result,
          message: '服务端尚未收敛决定；工具未被视为已执行。',
        }
      }
      return {
        ...state,
        phase: finalPhase(resolution),
        decision: event.decision,
        result: event.result,
        message: resolvedMessage(event.decision, resolution),
      }
    }
    case 'expired':
      return {...state, phase: 'expired', message: event.message ?? '此请求已过期，工具不会执行。'}
    case 'conflict':
      return {...state, phase: 'conflict', message: event.message ?? '此请求已在其他页面发生变化，正在核对最新状态。'}
    case 'outcome_unknown':
      return {
        ...state,
        phase: 'outcome_unknown',
        message: event.message ?? '决定结果暂时未知；未重新执行工具，请先核对状态。',
      }
    case 'reconciled': {
      const phase = approvalPhaseFromWire(event.approval)
      if (phase === 'pending') {
        if (['approved', 'denied', 'expired', 'already_resolved'].includes(state.phase)) return state
        return {
          ...state,
          phase: 'pending',
          message: state.decision === null ? null : '服务端仍显示待处理，可用相同决定重试。',
        }
      }
      const local = decisionMatchesResolution(state.decision, event.approval.resolution)
      return {
        ...state,
        phase: local ? phase : 'already_resolved',
        message: local
          ? resolvedMessage(state.decision, event.approval.resolution)
          : '此请求已在其他页面处理，当前显示的是最新状态。',
        result: local ? state.result : null,
      }
    }
  }
}

export type ApprovalFailureKind = 'conflict' | 'outcome_unknown'

/** ApiError is intentionally duck-typed so this module has no transport dependency. */
export function classifyApprovalFailure(error: unknown): ApprovalFailureKind {
  const status = typeof error === 'object' && error !== null && 'status' in error
    ? (error as {status?: unknown}).status
    : undefined
  return status === 409 ? 'conflict' : 'outcome_unknown'
}

export function approvalResultLabel(state: Pick<ApprovalDecisionState, 'phase' | 'decision' | 'message'>): string {
  if (state.phase === 'approved') {
    return state.message ?? (state.decision === 'allow_session' ? APPROVAL_DECISION_LABELS.allow_session : '已允许本次执行')
  }
  if (state.phase === 'denied') return state.message ?? '已拒绝此次操作'
  if (state.phase === 'expired') return state.message ?? '请求已过期'
  if (state.phase === 'already_resolved') return state.message ?? '已在其他页面处理'
  return state.message ?? '等待处理'
}

/** Stable de-duplication for root/leaf/activity projections. */
export function uniqueApprovals<T extends {approval_id: string}>(approvals: readonly T[]): T[] {
  const seen = new Set<string>()
  return approvals.filter(approval => {
    if (seen.has(approval.approval_id)) return false
    seen.add(approval.approval_id)
    return true
  })
}

/** Merge local result strips over a fresh pending projection without duplicates. */
export function mergeApprovalViews(
  pending: readonly ApprovalWire[],
  settled: readonly ApprovalWire[],
): ApprovalWire[] {
  const merged = new Map<string, ApprovalWire>()
  for (const approval of pending) merged.set(approval.approval_id, approval)
  for (const approval of settled) merged.set(approval.approval_id, approval)
  return [...merged.values()]
}

// ---------------------------------------------------------------------------
// Cross-page notification. Only an opaque approval id and its durable
// resolution cross the channel; preview, arguments, credentials and command
// ids never do.
// ---------------------------------------------------------------------------

export interface ApprovalSyncMessage {
  approval_id: string
  resolution: ApprovalResolution
  source: string
}

const APPROVAL_SYNC_EVENT = 'morrow:approval-sync'
const APPROVAL_SYNC_CHANNEL = 'morrow.approvals.v1'
const SOURCE_ID = `approval-page-${Math.random().toString(36).slice(2)}`
const syncListeners = new Set<(message: ApprovalSyncMessage) => void>()
let channel: BroadcastChannel | null = null

function readSyncMessage(value: unknown): ApprovalSyncMessage | null {
  if (typeof value !== 'object' || value === null) return null
  const candidate = value as Record<string, unknown>
  if (typeof candidate.approval_id !== 'string' || typeof candidate.resolution !== 'string') return null
  if (!['pending', 'approved', 'denied', 'expired'].includes(candidate.resolution)) return null
  return {
    approval_id: candidate.approval_id,
    resolution: candidate.resolution as ApprovalResolution,
    source: typeof candidate.source === 'string' ? candidate.source : '',
  }
}

function publishToSubscribers(message: ApprovalSyncMessage): void {
  if (message.source === SOURCE_ID) return
  for (const listener of syncListeners) listener(message)
}

function ensureChannel(): BroadcastChannel | null {
  if (channel !== null || typeof BroadcastChannel === 'undefined') return channel
  try {
    channel = new BroadcastChannel(APPROVAL_SYNC_CHANNEL)
    channel.addEventListener('message', event => {
      const message = readSyncMessage(event.data)
      if (message) publishToSubscribers(message)
    })
  } catch {
    channel = null
  }
  return channel
}

export function publishApprovalSync(
  message: Omit<ApprovalSyncMessage, 'source'>,
): void {
  const full = {...message, source: SOURCE_ID}
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(APPROVAL_SYNC_EVENT, {detail: full}))
  }
  ensureChannel()?.postMessage(full)
}

export function subscribeApprovalSync(
  listener: (message: ApprovalSyncMessage) => void,
): () => void {
  syncListeners.add(listener)
  const onWindow = (event: Event) => {
    const message = readSyncMessage((event as CustomEvent).detail)
    if (message) publishToSubscribers(message)
  }
  if (typeof window !== 'undefined') window.addEventListener(APPROVAL_SYNC_EVENT, onWindow)
  ensureChannel()
  return () => {
    syncListeners.delete(listener)
    if (typeof window !== 'undefined') window.removeEventListener(APPROVAL_SYNC_EVENT, onWindow)
  }
}

export function notifyPermissionChanged(): void {
  if (typeof window !== 'undefined') window.dispatchEvent(new Event('morrow:permission-changed'))
}
