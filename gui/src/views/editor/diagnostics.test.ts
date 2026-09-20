import { describe, expect, it } from 'vitest'
import type { WorkflowDraftDiagnosticWire } from '../../api/types'
import { diagnosticSummary, presentDiagnostic, safeUiText, uniqueDiagnostics } from './diagnostics'

const item = (overrides: Partial<WorkflowDraftDiagnosticWire> = {}): WorkflowDraftDiagnosticWire => ({
  severity: 'error',
  code: 'optional_removed',
  message: 'node implement: optional tool git is absent or denied',
  node_id: 'implement',
  edge_id: null,
  ...overrides,
})

describe('workflow diagnostic presentation', () => {
  it('maps optional tools to an actionable explanation with the actual tool name', () => {
    const view = presentDiagnostic(item())
    expect(view.title).toContain('工具')
    expect(view.guidance).toContain('git')
    expect(view.targetLabel).toBe('步骤 implement')
  })

  it('keeps unknown codes visible without guessing a repair', () => {
    const view = presentDiagnostic(item({ code: 'future_code', message: 'server note' }))
    expect(view.title).toContain('未识别')
    expect(view.detail).toBe('server note')
    expect(diagnosticSummary([item(), item({ severity: 'warning', code: 'future_code' })])).toBe('1 项阻断，1 项提醒')
  })

  it('clips traceback and secret-shaped text before it reaches the UI', () => {
    const safe = safeUiText('token=sk-secret Traceback (most recent call last):\nsecret details', 'fallback')
    expect(safe).toContain('token=已隐藏')
    expect(safe).not.toContain('secret details')
    expect(safe).not.toContain('Traceback')
  })

  it('deduplicates the same Core item when a publish failure repeats saved diagnostics', () => {
    const duplicate = item({ code: 'graph_cycle', message: 'cycle', edge_id: 'a->b' })
    expect(uniqueDiagnostics([duplicate, duplicate])).toEqual([duplicate])
    expect(diagnosticSummary([duplicate, duplicate])).toBe('1 项阻断')
  })
})
