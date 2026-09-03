import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

// Deterministic smoke test: the Warm Paper accent token must exist for both
// themes (light default + dark overrides), per
// docs/decisions/stage-8-gui-design-language.md.
const css = readFileSync(new URL('./tokens.css', import.meta.url), 'utf8')

describe('tokens.css', () => {
  it('defines --accent for the light theme', () => {
    expect(css).toContain('--accent: #c15f3c;')
  })

  it('defines --accent for the dark theme (media query and data-theme)', () => {
    expect(css).toContain('--accent: #d97757;')
    expect(css).toContain('@media (prefers-color-scheme: dark)')
    expect(css).toContain("[data-theme='dark']")
  })
})
