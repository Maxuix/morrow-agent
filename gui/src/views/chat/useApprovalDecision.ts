import { useCallback, useEffect, useRef, useState } from 'react'
import type { ApiClient } from '../../api/client'
import type { ApprovalDecisionWire, ApprovalResolveResultWire, ApprovalWire } from '../../api/types'
import {
  approvalPhaseFromWire,
  classifyApprovalFailure,
  initialApprovalDecisionState,
  isApprovalExpired,
  reduceApprovalDecision,
  publishApprovalSync,
  subscribeApprovalSync,
  type ApprovalDecisionState,
} from '../../state/approvalDecision'
import { commandId } from '../lib/editor'

export interface ApprovalDecisionController {
  state: ApprovalDecisionState
  busy: boolean
  decide: (decision: ApprovalDecisionWire) => Promise<void>
  reconcile: () => Promise<ApprovalWire | null>
  retry: () => Promise<void>
}

/**
 * One transport seam for every approval surface. The command id is retained
 * per decision so a lost response can be retried as the same command; a
 * second click while the first request is in flight is ignored before React
 * has a chance to render the submitting state.
 */
export function useApprovalDecision({
  approval,
  client,
  onSettled,
}: {
  approval: ApprovalWire
  client: ApiClient
  onSettled?: (result: ApprovalResolveResultWire) => void
}): ApprovalDecisionController {
  const approvalId = approval.approval_id
  const [state, setState] = useState<ApprovalDecisionState>(() => initialApprovalDecisionState(approval))
  const stateRef = useRef(state)
  const inFlight = useRef(false)
  const commandIds = useRef(new Map<ApprovalDecisionWire, string>())
  const alive = useRef(true)
  const onSettledRef = useRef(onSettled)
  onSettledRef.current = onSettled

  const update = useCallback((event: Parameters<typeof reduceApprovalDecision>[1]) => {
    setState(current => {
      const next = reduceApprovalDecision(current, event)
      stateRef.current = next
      return next
    })
  }, [])

  useEffect(() => {
    alive.current = true
    inFlight.current = false
    commandIds.current = new Map()
    const next = initialApprovalDecisionState(approval)
    stateRef.current = next
    setState(next)
    return () => {
      alive.current = false
      inFlight.current = false
    }
  }, [approvalId])

  // Pending projections can remain in a local store for one event turn after
  // another page has resolved them. A non-pending wire value is authoritative,
  // but a stale pending projection must not erase a local final result.
  useEffect(() => {
    if (approval.resolution !== 'pending') update({type: 'reconciled', approval})
  }, [approval.approval_id, approval.resolution, approval.row_version, approval.expires_at, update])

  const reconcile = useCallback(async (): Promise<ApprovalWire | null> => {
    try {
      const approvals = await client.listApprovals(false)
      const latest = approvals.find(item => item.approval_id === approvalId) ?? null
      if (!alive.current) return latest
      if (latest === null) {
        update({type: 'outcome_unknown', message: '暂时找不到此请求的最新记录；未重新执行工具。'})
        return null
      }
      update({type: 'reconciled', approval: latest})
      return latest
    } catch {
      if (alive.current) update({type: 'outcome_unknown'})
      return null
    }
  }, [approvalId, client, update])

  useEffect(() => subscribeApprovalSync(message => {
    if (message.approval_id !== approvalId || message.resolution === 'pending') return
    void reconcile()
  }), [approvalId, reconcile])

  useEffect(() => {
    if (state.phase !== 'pending') return
    const expiresAt = Date.parse(approval.expires_at)
    if (!Number.isFinite(expiresAt)) return
    const delay = Math.min(Math.max(0, expiresAt - Date.now()), 2_147_000_000)
    const timer = window.setTimeout(() => {
      if (alive.current && isApprovalExpired(approval)) update({type: 'expired'})
    }, delay)
    return () => window.clearTimeout(timer)
  }, [approval, state.phase, update])

  const decide = useCallback(async (decision: ApprovalDecisionWire): Promise<void> => {
    const current = stateRef.current
    if (inFlight.current || !['pending', 'outcome_unknown', 'conflict'].includes(current.phase)) return
    if (approvalPhaseFromWire(approval) === 'expired' || isApprovalExpired(approval)) {
      update({type: 'expired'})
      return
    }
    const command = commandIds.current.get(decision) ?? commandId(`approval_${decision}`)
    commandIds.current.set(decision, command)
    inFlight.current = true
    update({type: 'submit', decision, commandId: command})
    try {
      const result = await client.resolveApproval(approvalId, decision, command)
      if (!alive.current) return
      update({type: 'resolved', decision, result})
      publishApprovalSync({approval_id: approvalId, resolution: result.approval.resolution})
      onSettledRef.current?.(result)
    } catch (error) {
      if (!alive.current) return
      const failure = classifyApprovalFailure(error)
      update(failure === 'conflict' ? {type: 'conflict'} : {type: 'outcome_unknown'})
      if (failure === 'conflict') await reconcile()
    } finally {
      inFlight.current = false
    }
  }, [approval, approvalId, client, reconcile, update])

  const retry = useCallback(async () => {
    const decision = stateRef.current.decision
    if (decision !== null) await decide(decision)
    else await reconcile()
  }, [decide, reconcile])

  return {state, busy: inFlight.current || state.phase === 'submitting', decide, reconcile, retry}
}
