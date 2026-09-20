import { describe, expect, it, vi } from 'vitest'
import {
  applyAssetCommand,
  knowledgeCommands,
  resolveAssetCommand,
} from './knowledgeCommands'

describe('resolveAssetCommand destinations', () => {
  it('routes preference and workspace commands onto knowledge sections with focus when known', () => {
    expect(resolveAssetCommand('/preferences')).toEqual({
      kind: 'navigate', location: { kind: 'knowledge', section: 'preferences' },
    })
    expect(resolveAssetCommand('/preferences list')).toEqual({
      kind: 'navigate', location: { kind: 'knowledge', section: 'preferences', focus: 'list' },
    })
    expect(resolveAssetCommand('/workspace')).toEqual({
      kind: 'navigate', location: { kind: 'knowledge', section: 'profile' },
    })
  })

  it('routes memory commands to the knowledge library without implementation notices', () => {
    expect(resolveAssetCommand('/memory')).toEqual({
      kind: 'navigate', location: { kind: 'knowledge', section: 'knowledge', focus: 'records' },
    })
    expect(resolveAssetCommand('/memory disable')).toEqual({
      kind: 'navigate', location: { kind: 'knowledge', section: 'knowledge', focus: 'records' },
    })
    const selection = resolveAssetCommand('/memory selection show')
    expect(selection).toMatchObject({
      kind: 'navigate',
      location: { kind: 'knowledge', section: 'knowledge', focus: 'selection' },
    })
    expect(selection && selection.kind === 'navigate' && selection.notice).toBeUndefined()
  })

  it('keeps session learning review on the current inspector and sends management commands to the library', () => {
    expect(resolveAssetCommand('/learn')).toEqual({ kind: 'learning-review' })
    expect(resolveAssetCommand('/learn accept')).toEqual({ kind: 'learning-review' })
    expect(resolveAssetCommand('/learn edit')).toEqual({ kind: 'learning-review' })
    expect(resolveAssetCommand('/learn reject')).toEqual({ kind: 'learning-review' })
    expect(resolveAssetCommand('/learn show')).toEqual({ kind: 'learning-review' })
    expect(resolveAssetCommand('/learn mode')).toEqual({
      kind: 'navigate', location: { kind: 'knowledge', section: 'knowledge', focus: 'learning' },
    })
    expect(resolveAssetCommand('/learn inbox')).toEqual({
      kind: 'navigate', location: { kind: 'knowledge', section: 'knowledge', focus: 'learning' },
    })
    expect(resolveAssetCommand('/learn promotions abort')).toEqual({
      kind: 'navigate', location: { kind: 'knowledge', section: 'knowledge', focus: 'promotions' },
    })
    expect(resolveAssetCommand('/learn undo')).toEqual({
      kind: 'navigate', location: { kind: 'knowledge', section: 'knowledge', focus: 'promotions' },
    })
  })

  it('routes skills and mcp names onto the tools pages', () => {
    expect(resolveAssetCommand('/skills')).toEqual({
      kind: 'navigate', location: { kind: 'tools', section: 'skills' },
    })
    expect(resolveAssetCommand('/skills 报告助手')).toEqual({
      kind: 'navigate', location: { kind: 'tools', section: 'skills', item: '报告助手' },
    })
    expect(resolveAssetCommand('/mcp')).toEqual({
      kind: 'navigate', location: { kind: 'tools', section: 'mcp' },
    })
    expect(resolveAssetCommand('/mcp mcp_demo')).toEqual({
      kind: 'navigate', location: { kind: 'tools', section: 'mcp', item: 'mcp_demo' },
    })
  })

  it('explains unknown subcommands instead of guessing a target', () => {
    const unknown = resolveAssetCommand('/preferences foobar')
    expect(unknown?.kind).toBe('unknown')
    expect(unknown && unknown.kind === 'unknown' && unknown.message).toMatch(/无法解析/)
    expect(resolveAssetCommand('/learn mystery')?.kind).toBe('unknown')
    expect(resolveAssetCommand('/memory no-such')?.kind).toBe('unknown')
    expect(resolveAssetCommand('/workspace drop')?.kind).toBe('unknown')
    expect(resolveAssetCommand('/preference add')).toBeNull()
    expect(resolveAssetCommand('/profile edit')).toBeNull()
    expect(resolveAssetCommand('/config edit')).toBeNull()
    expect(resolveAssetCommand('/task')).toBeNull()
  })

  it('keeps the composer catalog for every documented alias', () => {
    expect(knowledgeCommands['/preferences add']).toBeDefined()
    expect(knowledgeCommands['/learn promotions finalize']).toBeDefined()
    expect(knowledgeCommands['/memory selection list']).toBeDefined()
    expect(knowledgeCommands['/config reset']).toBeUndefined()
  })
})

describe('applyAssetCommand', () => {
  it('calls navigate with returnTo chat for every management command', () => {
    const navigate = vi.fn()
    const openLearningReview = vi.fn()
    const notify = vi.fn()
    const cases: [string, { kind: string; section?: string; focus?: string; item?: string }][] = [
      ['/preferences', { kind: 'knowledge', section: 'preferences' }],
      ['/workspace edit', { kind: 'knowledge', section: 'profile', focus: 'edit' }],
      ['/memory list', { kind: 'knowledge', section: 'knowledge', focus: 'records' }],
      ['/memory selection', { kind: 'knowledge', section: 'knowledge', focus: 'selection' }],
      ['/learn set-mode', { kind: 'knowledge', section: 'knowledge', focus: 'learning' }],
      ['/learn reviews', { kind: 'knowledge', section: 'knowledge', focus: 'learning' }],
      ['/learn promotions', { kind: 'knowledge', section: 'knowledge', focus: 'promotions' }],
      ['/skills demo', { kind: 'tools', section: 'skills', item: 'demo' }],
      ['/mcp demo', { kind: 'tools', section: 'mcp', item: 'demo' }],
    ]
    for (const [command, location] of cases) {
      navigate.mockClear()
      notify.mockClear()
      expect(applyAssetCommand(command, { navigate, openLearningReview, notify })).toBe(true)
      expect(navigate).toHaveBeenCalledTimes(1)
      expect(navigate.mock.calls[0][0]).toEqual(location)
      expect(navigate.mock.calls[0][1]).toEqual({ kind: 'chat' })
    }
    expect(openLearningReview).not.toHaveBeenCalled()
  })

  it('opens session review in the current learning inspector', () => {
    const navigate = vi.fn()
    const openLearningReview = vi.fn()
    for (const command of ['/learn', '/learn accept', '/learn edit', '/learn reject', '/learn show']) {
      expect(applyAssetCommand(command, { navigate, openLearningReview, notify: vi.fn() })).toBe(true)
    }
    expect(openLearningReview).toHaveBeenCalledTimes(5)
    expect(navigate).not.toHaveBeenCalled()
  })

  it('notifies on unknown commands and does not navigate', () => {
    const navigate = vi.fn()
    const notify = vi.fn()
    expect(applyAssetCommand('/preferences nope', {
      navigate, openLearningReview: vi.fn(), notify,
    })).toBe(true)
    expect(navigate).not.toHaveBeenCalled()
    expect(notify).toHaveBeenCalledWith(expect.stringContaining('无法解析'))
  })
})
