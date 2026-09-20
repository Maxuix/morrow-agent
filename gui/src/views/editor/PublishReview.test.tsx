// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PublishReview, publishBlockReason } from './PublishReview'

afterEach(cleanup)

const base = {
  frozenRevisionId: null,
  saveState: 'saved' as const,
  checkState: 'passed' as const,
  dirty: false,
  staleReasons: [],
  diagnostics: [],
  busy: false,
  unresolved: false,
  message: null,
  onCheck: vi.fn(),
  onUpdateDefinition: vi.fn(),
  onPublish: vi.fn(),
  onReconcile: vi.fn(),
  onUseServer: vi.fn(),
  onNewDraft: vi.fn(),
}

describe('PublishReview', () => {
  it('keeps check, version and save states separate and blocks dirty publish', () => {
    render(<PublishReview {...base} status="valid" dirty saveState="scheduled" checkState="idle" />)
    expect(screen.getByText('等待保存')).toBeTruthy()
    expect(screen.queryByText('检查：尚未检查')).toBeNull()
    expect(screen.queryByText('版本：尚未生成发布版本')).toBeNull()
    expect((screen.getByRole('button', { name: '发布版本' }) as HTMLButtonElement).disabled).toBe(false)
    expect(publishBlockReason({ status: 'valid', saveState: 'scheduled', checkState: 'idle', dirty: true, staleReasons: [], unresolved: false })).toContain('本地修改')
  })

  it('offers a new draft after freeze and explicit recovery for unresolved saves', () => {
    const { rerender } = render(<PublishReview {...base} status="frozen" frozenRevisionId="wrev_1" />)
    expect(screen.getByText('已发布 · wrev_1')).toBeTruthy()
    expect(screen.getByRole('button', { name: '创建新草稿继续编辑' })).toBeTruthy()

    rerender(<PublishReview {...base} status="valid" unresolved />)
    expect(screen.getByRole('button', { name: '核对服务器结果' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '采用服务器版本' })).toBeTruthy()
    expect((screen.getByRole('button', { name: '发布版本' }) as HTMLButtonElement).disabled).toBe(true)
  })
})
