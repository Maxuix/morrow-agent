import { describe, expect, it } from 'vitest'
import {
  APPROVAL_DECISION_HINTS,
  approvalDecisionOptions,
  riskBadgeClass,
} from './ApprovalDialog'
import { APPROVAL_DECISION_LABELS, RISK_LEVEL_LABELS } from './lib/labels'

describe('approvalDecisionOptions', () => {
  it('always offers allow_once and deny', () => {
    expect(approvalDecisionOptions({ session_scope_allowed: false })).toEqual([
      'allow_once',
      'deny',
    ])
  })

  it('adds the session-scope exemption only when the approval permits it', () => {
    expect(approvalDecisionOptions({ session_scope_allowed: true })).toEqual([
      'allow_once',
      'deny',
      'allow_session',
    ])
  })
})

describe('RISK_LEVEL_LABELS', () => {
  it('covers every risk tier with the Chinese labels', () => {
    expect(RISK_LEVEL_LABELS).toEqual({ low: '低风险', medium: '中风险', high: '高风险' })
  })
})

describe('riskBadgeClass', () => {
  it('maps tiers to ok / paused / blocked tones', () => {
    expect(riskBadgeClass('low')).toBe('border-completed text-completed')
    expect(riskBadgeClass('medium')).toBe('border-paused text-paused')
    expect(riskBadgeClass('high')).toBe('border-blocked text-blocked')
  })

  it('always renders a border so tone is not color-only', () => {
    for (const level of ['low', 'medium', 'high'] as const) {
      expect(riskBadgeClass(level)).toContain('border-')
    }
  })
})

describe('approval decision labels and hints', () => {
  it('labels every decision', () => {
    expect(APPROVAL_DECISION_LABELS).toEqual({
      allow_once: '允许一次',
      deny: '拒绝',
      allow_session: '本会话同范围免批',
    })
  })

  it('explains the session exemption as same-scope and durable', () => {
    expect(APPROVAL_DECISION_HINTS['allow_session']).toMatch(/同范围/)
    expect(APPROVAL_DECISION_HINTS['allow_session']).toMatch(/持久记录/)
    expect(APPROVAL_DECISION_HINTS['allow_once']).toBeTruthy()
    expect(APPROVAL_DECISION_HINTS['deny']).toBeTruthy()
  })
})
