import { describe, expect, it } from 'vitest'
import type { ApprovalResolveResultWire, ApprovalWire } from '../api/types'
import {
  approvalDecisionOptions,
  approvalPhaseFromWire,
  classifyApprovalFailure,
  initialApprovalDecisionState,
  mergeApprovalViews,
  reduceApprovalDecision,
  riskBadgeClass,
  uniqueApprovals,
} from './approvalDecision'
import { APPROVAL_DECISION_HINTS } from './approvalDecision'

const approval = (overrides: Partial<ApprovalWire> = {}): ApprovalWire => ({
  approval_id: 'approval_1', tool_execution_id: 'execution_1', tool_name: 'read',
  session_id: 'session_1', task_run_id: 'task_1', agent_run_id: 'agent_1',
  workflow_run_id: null, node_run_id: null, node_id: null, agent_id: null,
  effect_class: 'workspace_read', risk_level: 'medium', session_scope_allowed: true,
  affected_objects: ['README.md'], requested_scope: 'workspace_read', granted_scope: null,
  preview: ['read README.md'], resolution: 'pending', created_at: '2026-09-11T00:00:00Z',
  expires_at: '2099-01-01T00:00:00Z', resolved_at: null, row_version: 1, ...overrides,
})

const result = (decision: 'allow_once' | 'deny' | 'allow_session'): ApprovalResolveResultWire => ({
  approval: approval({
    resolution: decision === 'deny' ? 'denied' : 'approved',
    granted_scope: decision === 'allow_session' ? 'session:workspace_read' : null,
  }),
  delivery: 'live', executed: decision !== 'deny',
})

describe('approval decision policy', () => {
  it('only advertises the session exemption when the server allows it', () => {
    expect(approvalDecisionOptions(approval({session_scope_allowed: true}))).toEqual([
      'allow_once', 'deny', 'allow_session',
    ])
    expect(approvalDecisionOptions(approval({session_scope_allowed: false}))).toEqual([
      'allow_once', 'deny',
    ])
    expect(riskBadgeClass('high')).toContain('border-')
    expect(APPROVAL_DECISION_HINTS.allow_session).toContain('相同工具')
  })

  it('never adds a session exemption to a high-risk approval', () => {
    expect(approvalDecisionOptions(approval({risk_level: 'high'}))).toEqual(['allow_once', 'deny'])
  })

  it('derives expiry and protects a resolved wire value from stale pending state', () => {
    expect(approvalPhaseFromWire(approval({expires_at: '2020-01-01T00:00:00Z'}), Date.parse('2021-01-01T00:00:00Z'))).toBe('expired')
    const state = initialApprovalDecisionState(approval())
    const submitted = reduceApprovalDecision(state, {type: 'submit', decision: 'allow_once', commandId: 'cmd_1'})
    const resolved = reduceApprovalDecision(submitted, {type: 'resolved', decision: 'allow_once', result: result('allow_once')})
    expect(resolved.phase).toBe('approved')
    expect(resolved.commandId).toBe('cmd_1')
    expect(reduceApprovalDecision(resolved, {type: 'reconciled', approval: approval()}).phase).toBe('approved')
  })

  it('keeps a lost response retryable and marks a conflict separately', () => {
    const state = initialApprovalDecisionState(approval())
    const unknown = reduceApprovalDecision(
      reduceApprovalDecision(state, {type: 'submit', decision: 'allow_once', commandId: 'cmd_1'}),
      {type: 'outcome_unknown'},
    )
    expect(unknown.phase).toBe('outcome_unknown')
    expect(unknown.decision).toBe('allow_once')
    expect(reduceApprovalDecision(unknown, {type: 'submit', decision: 'allow_once', commandId: 'cmd_1'}).phase).toBe('submitting')
    expect(reduceApprovalDecision(unknown, {type: 'conflict'}).phase).toBe('conflict')
    expect(classifyApprovalFailure({status: 409})).toBe('conflict')
    expect(classifyApprovalFailure({status: 503})).toBe('outcome_unknown')
  })

  it('deduplicates root, leaf and activity projections by durable approval id', () => {
    const first = approval({approval_id: 'a1'})
    const duplicate = approval({approval_id: 'a1', resolution: 'approved'})
    const second = approval({approval_id: 'a2'})
    expect(uniqueApprovals([first, duplicate, second])).toEqual([first, second])
    expect(mergeApprovalViews([first, second], [duplicate])).toEqual([duplicate, second])
  })
})
