/**
 * Browser appearance preference. The document `data-theme` attribute is the
 * live preview; localStorage is the persistence slot. Invalid, missing, or
 * unreadable values fall back to `system` (OS prefers-color-scheme).
 * Credentials never enter this store.
 */

export type Theme = 'light' | 'dark' | 'system'

export const THEME_VALUES = ['system', 'light', 'dark'] as const satisfies readonly Theme[]

export const THEME_LABELS: Record<Theme, string> = {
  system: '跟随系统',
  light: '浅色',
  dark: '深色',
}

export const THEME_STORAGE_KEY = 'morrow.appearance.theme'

export function parseTheme(value: string | null | undefined): Theme {
  return value === 'light' || value === 'dark' || value === 'system' ? value : 'system'
}

function defaultStorage(): Pick<Storage, 'getItem' | 'setItem'> | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

function defaultRoot(): HTMLElement | null {
  return typeof document === 'undefined' ? null : document.documentElement
}

export function readStoredTheme(
  storage: Pick<Storage, 'getItem'> | null = defaultStorage(),
): Theme {
  if (storage === null) return 'system'
  try {
    return parseTheme(storage.getItem(THEME_STORAGE_KEY))
  } catch {
    return 'system'
  }
}

export function persistTheme(
  theme: Theme,
  storage: Pick<Storage, 'setItem'> | null = defaultStorage(),
): void {
  if (storage === null) return
  try {
    storage.setItem(THEME_STORAGE_KEY, theme)
  } catch {
    // Quota / private mode: keep the in-memory theme; next load falls back.
  }
}

export function applyTheme(
  theme: Theme,
  root: HTMLElement | null = defaultRoot(),
): void {
  if (root === null) return
  if (theme === 'system') {
    delete root.dataset.theme
  } else {
    root.dataset.theme = theme
  }
}
