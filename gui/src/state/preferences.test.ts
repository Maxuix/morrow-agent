import { describe, expect, it } from 'vitest'
import type { Preference } from '../api/management'
import {
  PREFERENCE_STATEMENT_MAX,
  buildIntent,
  charCount,
  collectRows,
  filterRows,
  globalImpactText,
  revisionFor,
  rowCounts,
  rowKey,
  statementError,
  type PreferenceDocuments,
} from './preferences'

function entry(overrides: Partial<Preference> = {}): Preference {
  return {
    preference_id: 'pref_1',
    statement: '先给结论',
    scope: 'workspace',
    status: 'active',
    revision: 1,
    updated_at: '2026-09-08T00:00:00Z',
    ...overrides,
  }
}

function documents(workspace: Preference[], global: Preference[]): PreferenceDocuments {
  return {
    workspace: { status: 'ready', revision: 4, entries: workspace, history: [], error: '' },
    global: { status: 'ready', revision: 2, entries: global, history: [], error: '' },
  }
}

describe('preference document primitives', () => {
  it('counts code points so one emoji is one character', () => {
    expect(charCount('abc')).toBe(3)
    expect(charCount('结论🙂')).toBe(3)
    expect(charCount('👍🏽')).toBe(2)
    expect(charCount('x'.repeat(PREFERENCE_STATEMENT_MAX))).toBe(PREFERENCE_STATEMENT_MAX)
  })

  it('keeps workspace rows before global rows and each document order', () => {
    const rows = collectRows(
      documents(
        [entry({ preference_id: 'pref_a' }), entry({ preference_id: 'pref_b', statement: 'B' })],
        [entry({ preference_id: 'pref_c', scope: 'global', statement: 'C' })],
      ),
    )
    expect(rows.map(row => row.preference_id)).toEqual(['pref_a', 'pref_b', 'pref_c'])
    expect(rows.map(row => row.scope)).toEqual(['workspace', 'workspace', 'global'])
    expect(rows[0].key).toBe(rowKey('workspace', 'pref_a'))
  })

  it('filters by source, record state and search text without reordering', () => {
    const rows = collectRows(
      documents(
        [
          entry({ preference_id: 'pref_a', statement: '先给结论' }),
          entry({ preference_id: 'pref_b', statement: '中文说明', status: 'disabled' }),
          entry({ preference_id: 'pref_c', statement: '已删除的规则', status: 'deleted' }),
        ],
        [entry({ preference_id: 'pref_d', scope: 'global', statement: 'GLOBAL RULE' })],
      ),
    )
    expect(filterRows(rows, '', 'all', 'all').map(row => row.preference_id)).toEqual([
      'pref_a', 'pref_b', 'pref_d',
    ])
    expect(filterRows(rows, '', 'workspace', 'all').map(row => row.preference_id)).toEqual(['pref_a', 'pref_b'])
    expect(filterRows(rows, '', 'all', 'disabled').map(row => row.preference_id)).toEqual(['pref_b'])
    expect(filterRows(rows, '中文', 'all', 'all').map(row => row.preference_id)).toEqual(['pref_b'])
    expect(filterRows(rows, 'global rule', 'all', 'all').map(row => row.preference_id)).toEqual(['pref_d'])
    expect(filterRows(rows, 'pref_d', 'all', 'all').map(row => row.preference_id)).toEqual(['pref_d'])
    expect(filterRows(rows, '不存在', 'all', 'all')).toEqual([])
  })

  it('counts loaded and enabled records instead of run injections', () => {
    const rows = collectRows(
      documents(
        [entry({ preference_id: 'pref_a' }), entry({ preference_id: 'pref_b', status: 'disabled' })],
        [],
      ),
    )
    expect(rowCounts(rows)).toEqual({ loaded: 2, enabled: 1 })
  })

  it('rejects empty and over-long statements in the server character口径', () => {
    expect(statementError('   ')).toBe('规则正文不能为空')
    expect(statementError('先给结论')).toBe('')
    expect(statementError('x'.repeat(PREFERENCE_STATEMENT_MAX + 1))).toContain('512')
    expect(statementError('🙂'.repeat(PREFERENCE_STATEMENT_MAX))).toBe('')
  })

  it('builds one-operation intents with the target document revision', () => {
    expect(buildIntent('workspace', 'add', 4, { statement: '新规则' })).toEqual({
      scope: 'workspace',
      expected_revision: 4,
      operations: [{ operation: 'add', statement: '新规则' }],
    })
    expect(buildIntent('global', 'replace', 2, { preferenceId: 'pref_g', statement: '改后' })).toEqual({
      scope: 'global',
      expected_revision: 2,
      operations: [{ operation: 'replace', preference_id: 'pref_g', statement: '改后' }],
    })
    expect(buildIntent('workspace', 'disable', 4, { preferenceId: 'pref_a' })).toEqual({
      scope: 'workspace',
      expected_revision: 4,
      operations: [{ operation: 'disable', preference_id: 'pref_a' }],
    })
    expect(buildIntent('workspace', 'remove', 4, { preferenceId: 'pref_a' }).operations[0]).toEqual({
      operation: 'remove',
      preference_id: 'pref_a',
    })
  })

  it('uses the row scope revision and never a Profile revision', () => {
    const docs = documents([entry()], [entry({ preference_id: 'pref_g', scope: 'global' })])
    expect(revisionFor(docs, 'workspace')).toBe(4)
    expect(revisionFor(docs, 'global')).toBe(2)
    expect(globalImpactText(collectRows(docs)[1])).toContain('影响所有工作区')
  })
})
