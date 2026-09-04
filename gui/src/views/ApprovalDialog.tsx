import { useEffect, useRef, useState } from 'react'
import { ApiError, type ApiClient } from '../api/client'
import type { ApprovalDecisionWire, ApprovalWire } from '../api/types'
import { commandId } from './lib/editor'
import {
  APPROVAL_DECISION_LABELS,
  formatTimestamp,
  RISK_LEVEL_LABELS,
  shortId,
} from './lib/labels'

/**
 * Which resolution buttons are offered for an approval (§8.5). `allow_once`
 * and `deny` are always available; the session-scope exemption is an opt-in
 * exposed only when the server says the approval permits it.
 */
export function approvalDecisionOptions(
  approval: Pick<ApprovalWire, 'session_scope_allowed'>,
): ApprovalDecisionWire[] {
  return approval.session_scope_allowed
    ? ['allow_once', 'deny', 'allow_session']
    : ['allow_once', 'deny']
}

/** Badge treatment per risk tier: ok tone (low), paused tone (medium), blocked tone (high). */
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

/** Consequence copy for each resolution button (shown as a tooltip). */
export const APPROVAL_DECISION_HINTS: Record<ApprovalDecisionWire, string> = {
  allow_once: '仅允许此次工具调用；同范围的后续请求仍会单独审批。',
  deny: '拒绝此次工具调用；节点会收到拒绝错误并据此收尾。',
  allow_session:
    '本次执行，且本会话内相同工具与操作范围的请求将免审批（同范围免批）；该决定会被持久记录。',
}

/**
 * Full §8.5 approval surface — requester (task/workflow/node/agent), operation
 * type with risk tier, affected objects, bounded redacted preview and expiry.
 * Never a bare "Agent wants to run a tool" notice.
 */
export function ApprovalDialog({
  approval,
  client,
  onResolved,
  onClose,
}: {
  approval: ApprovalWire
  client: ApiClient
  /** Fired after a successful resolution, before the store event lands. */
  onResolved: (approvalId: string) => void
  onClose: () => void
}) {
  const [pendingDecision, setPendingDecision] = useState<ApprovalDecisionWire | null>(null)
  const [error, setError] = useState<string | null>(null)
  const primaryActionRef = useRef<HTMLButtonElement>(null)

  // Keyboard access: Escape closes; focus the primary action on open.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    const focusHandle = window.setTimeout(() => primaryActionRef.current?.focus(), 0)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      window.clearTimeout(focusHandle)
    }
  }, [onClose])

  const resolve = async (decision: ApprovalDecisionWire) => {
    setPendingDecision(decision)
    setError(null)
    try {
      await client.resolveApproval(
        approval.approval_id,
        decision,
        commandId(`approval_${decision}_${approval.approval_id}_${approval.row_version}`),
      )
      onResolved(approval.approval_id)
      onClose()
    } catch (error) {
      setError(error instanceof ApiError ? error.message : '操作失败，请重试。')
      setPendingDecision(null)
    }
  }

  const requester = approval.agent_id ?? shortId(approval.agent_run_id)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div aria-hidden="true" className="absolute inset-0 bg-black/40" />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="approval-dialog-title"
        className="relative max-h-full w-full max-w-lg overflow-y-auto rounded-[10px] border border-subtle bg-raised p-5 shadow-[var(--shadow-overlay)]"
      >
        <header className="flex items-baseline gap-2">
          <h2 id="approval-dialog-title" className="font-serif text-lg font-medium">
            审批请求
          </h2>
          <span className="ml-auto font-mono text-xs text-secondary">
            {shortId(approval.approval_id)}
          </span>
        </header>

        <dl className="mt-4 flex flex-col gap-3 text-sm">
          <div className="flex items-baseline gap-2">
            <dt className="w-20 shrink-0 text-secondary">请求方</dt>
            <dd className="font-mono text-xs">{requester}</dd>
          </div>
          {approval.workflow_run_id !== null && (
            <div className="flex items-baseline gap-2">
              <dt className="w-20 shrink-0 text-secondary">工作流</dt>
              <dd className="font-mono text-xs">{shortId(approval.workflow_run_id)}</dd>
            </div>
          )}
          {approval.node_run_id !== null && (
            <div className="flex items-baseline gap-2">
              <dt className="w-20 shrink-0 text-secondary">节点</dt>
              <dd className="font-mono text-xs">
                {approval.node_id ?? shortId(approval.node_run_id)}
              </dd>
            </div>
          )}
          <div className="flex items-baseline gap-2">
            <dt className="w-20 shrink-0 text-secondary">任务</dt>
            <dd className="font-mono text-xs">{shortId(approval.task_run_id)}</dd>
          </div>
          <div className="flex flex-wrap items-baseline gap-2">
            <dt className="w-20 shrink-0 text-secondary">操作类型</dt>
            <dd className="flex flex-wrap items-center gap-2 font-mono text-xs">
              <span className="text-accent">{approval.tool_name}</span>
              <span className="text-secondary">{approval.effect_class}</span>
              <span
                className={`rounded-[8px] border px-1.5 py-0.5 ${riskBadgeClass(approval.risk_level)}`}
              >
                {RISK_LEVEL_LABELS[approval.risk_level]}
              </span>
            </dd>
          </div>
          <div className="flex items-baseline gap-2">
            <dt className="w-20 shrink-0 text-secondary">请求范围</dt>
            <dd className="font-mono text-xs">{approval.requested_scope}</dd>
          </div>
          <div className="flex items-baseline gap-2">
            <dt className="w-20 shrink-0 text-secondary">受影响对象</dt>
            <dd>
              {approval.affected_objects.length > 0 ? (
                <ul className="flex list-disc flex-col gap-0.5 pl-5 font-mono text-xs">
                  {approval.affected_objects.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              ) : (
                <span className="text-secondary">无已记录对象</span>
              )}
            </dd>
          </div>
          <div className="flex items-baseline gap-2">
            <dt className="w-20 shrink-0 text-secondary">过期时间</dt>
            <dd className="font-mono text-xs">{formatTimestamp(approval.expires_at)}</dd>
          </div>
        </dl>

        {approval.preview.length > 0 && (
          <div className="mt-4">
            <h3 className="text-xs font-medium tracking-wide text-secondary">脱敏预览</h3>
            <pre className="mt-1 overflow-x-auto rounded-[8px] border border-subtle bg-base px-3 py-2 font-mono text-xs">
              {approval.preview.join('\n')}
            </pre>
          </div>
        )}

        {error !== null && (
          <p role="alert" className="mt-3 text-xs text-failed">
            {error}
          </p>
        )}

        <div className="mt-5 flex flex-wrap justify-end gap-2">
          {approvalDecisionOptions(approval).map((decision, index) => (
            <button
              key={decision}
              ref={index === 0 ? primaryActionRef : undefined}
              type="button"
              disabled={pendingDecision !== null}
              title={APPROVAL_DECISION_HINTS[decision]}
              onClick={() => resolve(decision)}
              className={`rounded-[8px] border px-3 py-1.5 text-sm transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-55 ${
                decision === 'allow_once'
                  ? 'border-accent text-accent hover:bg-base'
                  : decision === 'deny'
                    ? 'border-subtle text-secondary hover:border-accent hover:text-primary'
                    : 'border-subtle text-primary hover:border-accent'
              }`}
            >
              {pendingDecision === decision ? '处理中…' : APPROVAL_DECISION_LABELS[decision]}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
