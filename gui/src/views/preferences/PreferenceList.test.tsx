import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../api/client'
import type { Preference } from '../../api/management'
import type { PreferenceDocuments } from '../../state/preferences'
import { PreferenceBatch } from '../PreferenceBatch'
import { batchInvalid, batchPayload, batchPreview } from './BatchPanel'
import { PreferenceEditorDialog } from './PreferenceEditorDialog'
import { PreferenceList } from './PreferenceList'

const client = new ApiClient({ baseUrl: '', token: '' })

function entry(overrides: Partial<Preference> = {}): Preference {
  return {
    preference_id: 'pref_1',
    statement: '先给结论，再说明理由。',
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

function list(documentsValue: PreferenceDocuments, rows = {}) {
  return renderToStaticMarkup(
    <PreferenceList client={client} documents={documentsValue} rows={rows} write={vi.fn()} mutate={vi.fn()} onRefresh={() => {}} />,
  )
}

describe('PreferenceList contract', () => {
  it('shows the rule text first with its scope and record state', () => {
    const html = list(
      documents(
        [entry(), entry({ preference_id: 'pref_2', statement: '已停用的规则', status: 'disabled' })],
        [entry({ preference_id: 'pref_g', scope: 'global', statement: '全局规则' })],
      ),
    )
    expect(html).toContain('先给结论，再说明理由。')
    expect(html).toContain('当前工作区 · 已启用')
    expect(html).toContain('当前工作区 · 已停用')
    expect(html).toContain('全局 · 影响所有工作区 · 已启用')
    expect(html).toContain('aria-checked="true"')
    expect(html).toContain('aria-checked="false"')
    expect(html).toContain('role="switch"')
  })

  it('keeps deleted records out of the list and never offers enable', () => {
    const html = list(documents([entry({ preference_id: 'pref_d', statement: '已删除的规则', status: 'deleted' })], []))
    expect(html).not.toContain('已删除的规则')
    expect(html).toContain('没有匹配的规则。')
    expect(html).not.toContain('role="switch"')
  })

  it('counts loaded and enabled records and says what the count means', () => {
    const html = list(
      documents([entry(), entry({ preference_id: 'pref_2', status: 'disabled' })], []),
    )
    expect(html).toContain('已加载 2 条 · 已启用 1 条（记录状态，不是本次运行使用数量）')
  })

  it('never invents an evidence source label', () => {
    const html = list(documents([entry()], [entry({ preference_id: 'pref_g', scope: 'global', evidence_ids: ['ev_1', 'ev_2'] })]))
    expect(html).toContain('无关联证据')
    expect(html).toContain('证据 2 条')
    expect(html).not.toContain('AGENTS.md')
    expect(html).not.toContain('用户直接设定')
  })

  it('exposes search, source and status filters as pressed buttons', () => {
    const html = list(documents([entry()], []))
    expect(html).toContain('aria-label="搜索规则"')
    expect(html).toContain('aria-label="来源"')
    expect(html).toContain('aria-label="状态"')
    expect(html).toContain('aria-pressed="true">全部')
    expect(html).toContain('＋ 新增偏好')
  })

  it('reports one failed scope without hiding the loaded one', () => {
    const documentsValue = documents([entry()], [])
    documentsValue.global = { status: 'error', revision: 0, entries: [], history: [], error: '全局偏好加载失败，请重试。' }
    const html = list(documentsValue)
    expect(html).toContain('全局偏好加载失败，请重试。')
    expect(html).toContain('先给结论，再说明理由。')
    expect(html).toContain('重试')
  })

  it('keeps a busy row disabled and explains the failure inline', () => {
    const documentsValue = documents([entry()], [])
    const busy = list(documentsValue, { 'workspace:pref_1': { status: 'busy', message: '正在提交…' } })
    expect(busy).toContain('正在提交…')
    expect(busy).toMatch(/<button[^>]*role="switch"[^>]*disabled/)
    const failed = list(documentsValue, { 'workspace:pref_1': { status: 'error', message: '该作用域内容已变化，请刷新后重试；开关保持最后确认状态。' } })
    expect(failed).toContain('role="alert">该作用域内容已变化')
  })

  it('renders untrusted rule text as escaped text', () => {
    const html = list(documents([entry({ statement: '<img src=x onerror=alert(1)>' })], []))
    expect(html).toContain('&lt;img')
    expect(html).not.toContain('<img src=x')
  })
})

describe('PreferenceEditorDialog contract', () => {
  it('adds with an explicit scope choice and impact sentence', () => {
    const html = renderToStaticMarkup(
      <PreferenceEditorDialog mode="add" scope="global" onSubmit={() => {}} onCancel={() => {}} />,
    )
    expect(html).toContain('新增偏好')
    expect(html).toContain('当前工作区')
    expect(html).toContain('所有工作区（全局）')
    expect(html).toContain('所有工作区（全局）')
    expect(html).toContain('例如：先给结论，再说明理由。')
    expect(html).not.toContain('value="例如：先给结论，再说明理由。"')
  })

  it('edits without offering a scope move and shows the counter', () => {
    const html = renderToStaticMarkup(
      <PreferenceEditorDialog mode="edit" scope="workspace" initialStatement="先给结论🙂" onSubmit={() => {}} onCancel={() => {}} />,
    )
    expect(html).toContain('编辑偏好')
    expect(html).toContain('作用范围：当前工作区')
    expect(html).not.toContain('name="preference-scope"')
    expect(html).toContain('5 / 512 字符')
  })

  it('names the dialog and keeps a cancel path', () => {
    const html = renderToStaticMarkup(
      <PreferenceEditorDialog mode="add" scope="workspace" onSubmit={() => {}} onCancel={() => {}} />,
    )
    expect(html).toContain('aria-labelledby="pp-editor-title"')
    expect(html).toContain('取消')
    expect(html).toContain('添加偏好')
  })
})

describe('ordered batch contract', () => {
  it('previews human actions and rejects an incomplete row', () => {
    expect(batchPreview([
      { operation: 'add', statement: '先给结论' },
      { operation: 'disable', preference_id: 'pref_a' },
    ])).toBe('新增：先给结论；停用')
    expect(batchInvalid([{ operation: 'add', statement: '   ' }])).toBe(true)
    expect(batchInvalid([{ operation: 'disable' }])).toBe(true)
    expect(batchInvalid([{ operation: 'replace', preference_id: 'pref_a', statement: '改后' }])).toBe(false)
    expect(batchInvalid([{ operation: 'remove', preference_id: 'pref_a' }])).toBe(false)
  })

  it('caps the panel at eight rows and shows the scope and document revision', () => {
    const html = renderToStaticMarkup(
      <PreferenceBatch scope="global" revision={4} entries={[entry()]} mutate={async () => true} />,
    )
    expect(html).toContain('最多 8 项')
    expect(html).toContain('全局（影响所有工作区）')
    expect(html).not.toContain('基于文档 r4')
    expect(html).toContain('value="add"')
    expect(html).toContain('aria-label="第 1 项操作"')
    expect(html).toContain('aria-label="第 1 项正文"')
  })
})

describe('history contract', () => {
  it('labels only finalized batches as a revision change and names the read limit', () => {
    const documentsValue = documents([entry()], [])
    documentsValue.workspace.history = [
      { batch_id: 'batch_ok', status: 'finalized', expected_document_revision: 3, created_at: '2026-09-08T10:00:00Z',
        before: [entry()], after: [entry({ statement: '改后' })],
        operations: [{ operation: 'replace', statement: '改后', preference_id: 'pref_1' }] },
      { batch_id: 'batch_failed', status: 'failed', expected_document_revision: 4, created_at: '2026-09-08T11:00:00Z',
        before: [], after: [], operations: [{ operation: 'add', statement: '未提交', preference_id: null }] },
    ]
    const html = list(documentsValue)
    expect(html).toContain('最近的操作历史')
    expect(html).not.toContain('500 批次')
    expect(html).toContain('r3 → r4')
    expect(html).toContain('未提交修订')
    expect(html).toContain('替换：改后')
    expect(html).toContain('新增：未提交')
  })
})

describe('batch wire shape', () => {
  it('omits the target id for add and the statement for lifecycle changes', () => {
    expect(batchPayload([
      { operation: 'add', preference_id: '', statement: '先给结论' },
      { operation: 'replace', preference_id: 'pref_a', statement: '改后' },
      { operation: 'disable', preference_id: 'pref_b', statement: '' },
      { operation: 'remove', preference_id: 'pref_c' },
    ])).toEqual([
      { operation: 'add', statement: '先给结论' },
      { operation: 'replace', preference_id: 'pref_a', statement: '改后' },
      { operation: 'disable', preference_id: 'pref_b' },
      { operation: 'remove', preference_id: 'pref_c' },
    ])
  })
})
