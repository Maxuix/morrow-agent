import { useRef, useState } from 'react'
import type { ApiClient, ApiError } from '../../api/client'
import type { ApprovalDecisionWire, ApprovalResolveResultWire, ApprovalWire } from '../../api/types'
import {
  approvalDecisionOptions,
  approvalEffectLabel,
  approvalResultLabel,
  approvalScopeLabel,
  APPROVAL_DECISION_HINTS,
  isApprovalExpired,
  notifyPermissionChanged,
  riskBadgeClass,
  type ApprovalDecisionState,
} from '../../state/approvalDecision'
import { RISK_LEVEL_LABELS, formatTimestamp, APPROVAL_DECISION_LABELS } from '../lib/labels'
import { commandId } from '../lib/editor'
import { useApprovalDecision } from './useApprovalDecision'

const PREVIEW_LINES = 8
const AFFECTED_OBJECTS = 8

type RevokePhase = 'idle' | 'loading' | 'revoked' | 'unknown'

function isApiConflict(error: unknown): boolean {
  return typeof error === 'object' && error !== null && 'status' in error
    && (error as ApiError).status === 409
}

function approvalText(approval: ApprovalWire): string {
  if (approval.node_id) return `来自工作流步骤「${approval.node_id}」`
  return '来自当前会话'
}

function currentApproval(state: ApprovalDecisionState, fallback: ApprovalWire): ApprovalWire {
  return state.result?.approval ?? fallback
}

function decisionButtonLabel(decision: ApprovalDecisionWire): string {
  return APPROVAL_DECISION_LABELS[decision]
}

function resolutionCopy(state: ApprovalDecisionState): string {
  if (state.phase === 'outcome_unknown') return '决定结果暂时未知；未重新执行工具。'
  if (state.phase === 'conflict') return '此请求已在其他页面发生变化，正在核对最新状态。'
  return approvalResultLabel(state)
}

/**
 * Inline approval surface shared by the transcript and activity region.
 * It exposes only the server-bounded preview and keeps all command/request
 * identifiers out of visible copy and cross-page messages.
 */
export function InlineApprovalCard({
  approval,
  client,
  workspace,
  onSettled,
  onPermissionChanged,
}: {
  approval: ApprovalWire
  client: ApiClient
  workspace: string
  onSettled?: (result: ApprovalResolveResultWire) => void
  onPermissionChanged?: () => void
}) {
  const controller = useApprovalDecision({approval, client, onSettled})
  const {state, busy, decide, reconcile, retry} = controller
  const [revokePhase, setRevokePhase] = useState<RevokePhase>('idle')
  const revokeCommand = useRef(commandId('revoke_session_scope'))
  const latest = currentApproval(state, approval)
  const sessionGrant = (state.phase === 'approved' && state.decision === 'allow_session') || latest.granted_scope !== null
  const options = approvalDecisionOptions(approval)
  const pending = state.phase === 'pending' || state.phase === 'submitting'
  const expired = isApprovalExpired(approval)

  const revoke = async () => {
    if (revokePhase === 'loading' || revokePhase === 'revoked') return
    setRevokePhase('loading')
    try {
      const view = await client.chatPermissions(workspace, approval.session_id)
      const matching = view.session_scopes.items.find(item => item.approval_id === approval.approval_id)
        ?? view.session_scopes.items.find(item => item.scope === latest.granted_scope)
        ?? view.session_scopes.items.find(item => item.scope === approval.requested_scope)
      if (!matching) {
        setRevokePhase('revoked')
        notifyPermissionChanged()
        onPermissionChanged?.()
        return
      }
      await client.revokeChatPermission(workspace, approval.session_id, {
        command_id: revokeCommand.current,
        kind: 'session_scope',
        subject_id: matching.approval_id,
        expected_revision: matching.revision,
      })
      setRevokePhase('revoked')
      notifyPermissionChanged()
      onPermissionChanged?.()
    } catch (error) {
      if (isApiConflict(error)) {
        try {
          const fresh = await client.chatPermissions(workspace, approval.session_id)
          const stillActive = fresh.session_scopes.items.some(item =>
            item.approval_id === approval.approval_id || item.scope === latest.granted_scope,
          )
          setRevokePhase(stillActive ? 'unknown' : 'revoked')
          if (!stillActive) {
            notifyPermissionChanged()
            onPermissionChanged?.()
          }
          return
        } catch {
          // Preserve the same command id for the explicit retry below.
        }
      }
      setRevokePhase('unknown')
    }
  }

  return (
    <article className="inline-approval-card" data-approval-phase={state.phase} aria-label="工具审批请求">
      {pending ? (
        <>
          <header className="inline-approval-header">
            <div>
              <p className="inline-approval-kicker">需要确认 · {approvalText(approval)}</p>
              <h3>{approvalEffectLabel(approval.effect_class)}</h3>
            </div>
            <span className={`approval-risk-badge ${riskBadgeClass(approval.risk_level)}`}>
              {RISK_LEVEL_LABELS[approval.risk_level]}
            </span>
          </header>
          <p className="inline-approval-tool"><span className="font-mono">{approval.tool_name}</span> · {approvalScopeLabel(approval.requested_scope)}</p>
          <dl className="inline-approval-facts">
            <div><dt>影响对象</dt><dd>{approval.affected_objects.length > 0 ? approval.affected_objects.slice(0, AFFECTED_OBJECTS).join('、') : '服务端未提供具体对象'}</dd></div>
            <div><dt>有效期</dt><dd>{formatTimestamp(approval.expires_at)}</dd></div>
          </dl>
          {approval.preview.length > 0 && <details className="inline-approval-preview">
            <summary>查看安全预览</summary>
            <pre>{approval.preview.slice(0, PREVIEW_LINES).join('\n')}</pre>
            {approval.preview.length > PREVIEW_LINES && <p>预览已按安全上限截断。</p>}
          </details>}
          {expired && <p role="status" className="inline-approval-message">此请求已过期，工具不会执行。</p>}
          {state.message && !expired && <p role="status" className="inline-approval-message">{state.message}</p>}
          <div className="inline-approval-actions">
            {options.map(decision => <button
              key={decision}
              type="button"
              className={decision === 'deny' ? 'editor-button' : 'editor-button approval-primary-action'}
              disabled={busy || expired}
              title={APPROVAL_DECISION_HINTS[decision]}
              onClick={() => void decide(decision)}
            >{decisionButtonLabel(decision)}</button>)}
          </div>
        </>
      ) : (
        <div className="inline-approval-result" role="status">
          <div>
            <p className="inline-approval-kicker">审批状态</p>
            <p>{resolutionCopy(state)}</p>
          </div>
          <div className="inline-approval-result-actions">
            {(state.phase === 'outcome_unknown' || state.phase === 'conflict') && <>
              <button type="button" className="editor-button" onClick={() => void reconcile()}>核对最新状态</button>
              {state.phase === 'outcome_unknown' && state.decision !== null && <button type="button" className="editor-button" onClick={() => void retry()}>重试相同决定</button>}
            </>}
            {state.phase === 'already_resolved' && <button type="button" className="editor-button" onClick={() => void reconcile()}>核对最新状态</button>}
            {sessionGrant && <button type="button" className="editor-button" disabled={revokePhase === 'loading' || revokePhase === 'revoked'} onClick={() => void revoke()}>
              {revokePhase === 'revoked' ? '已撤销此授权' : revokePhase === 'loading' ? '正在撤销…' : revokePhase === 'unknown' ? '重试撤销此授权' : '撤销此授权'}
            </button>}
          </div>
          {revokePhase === 'unknown' && <p role="alert" className="inline-approval-message">撤销结果暂时未知；未重复执行其他操作。</p>}
        </div>
      )}
    </article>
  )
}
