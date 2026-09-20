// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import {
  THEME_STORAGE_KEY,
  applyTheme,
  parseTheme,
  persistTheme,
  readStoredTheme,
} from './theme'

describe('theme persistence', () => {
  it('falls back to system for missing or invalid values', () => {
    expect(parseTheme(null)).toBe('system')
    expect(parseTheme('')).toBe('system')
    expect(parseTheme('nope')).toBe('system')
    expect(parseTheme('dark')).toBe('dark')
  })

  it('reads, persists, and applies a valid theme', () => {
    const store: Record<string, string> = {}
    const storage = {
      getItem: (key: string) => store[key] ?? null,
      setItem: (key: string, value: string) => { store[key] = value },
    }
    expect(readStoredTheme(storage)).toBe('system')
    persistTheme('dark', storage)
    expect(store[THEME_STORAGE_KEY]).toBe('dark')
    expect(readStoredTheme(storage)).toBe('dark')

    const root = document.createElement('html')
    applyTheme('dark', root)
    expect(root.dataset.theme).toBe('dark')
    applyTheme('system', root)
    expect(root.dataset.theme).toBeUndefined()
  })

  it('treats unreadable storage as system', () => {
    const storage = {
      getItem: () => { throw new Error('blocked') },
    }
    expect(readStoredTheme(storage)).toBe('system')
  })
})
