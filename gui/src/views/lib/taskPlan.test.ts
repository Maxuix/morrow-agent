import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { isExplicitPauseCommand } from './taskPlan'

const corpus = JSON.parse(
  readFileSync(new URL('../../../../tests/fixtures/control_text_corpus.json', import.meta.url), 'utf8'),
) as { pause: { positive: string[]; negative: string[] } }

describe('isExplicitPauseCommand mirrors the server-side pause gate', () => {
  it.each(corpus.pause.positive)('routes an explicit pause word: %s', (text) => {
    expect(isExplicitPauseCommand(text)).toBe(true)
  })

  it.each(corpus.pause.negative)('never routes guarded wording: %s', (text) => {
    expect(isExplicitPauseCommand(text)).toBe(false)
  })

  it('never routes empty input and keeps generation wording pause-shaped', () => {
    expect(isExplicitPauseCommand('  ')).toBe(false)
    // "暂停生成" stays pause-shaped: the server scopes it to the generation
    // gate first; the client mirror must not self-negate it either.
    expect(isExplicitPauseCommand('暂停生成')).toBe(true)
  })
})
