import type { InspectorStore } from '../../state/inspector'

export type InspectorPlatform = 'mac' | 'other'

export function detectPlatform(): InspectorPlatform {
  if (typeof navigator === 'undefined') return 'other'
  const text = `${navigator.platform ?? ''} ${navigator.userAgent ?? ''}`.toUpperCase()
  return text.includes('MAC') ? 'mac' : 'other'
}

export interface InspectorShortcutEvent {
  code: string
  altKey: boolean
  metaKey: boolean
  ctrlKey: boolean
  shiftKey: boolean
  repeat: boolean
  isComposing: boolean
}

/**
 * macOS Option+Command+B / 其他平台 Ctrl+Alt+B 切换 Inspector。
 * 仅精确组合（不多按 Shift 或第三个修饰键）、非 IME composition、非按住
 * 重复时处理；用 event.code 判定，兼容 mac 上 Option+B 产生 '∫' 的情况。
 * 监听挂在 window 上，输入框聚焦时同样可用。
 */
export function isInspectorToggle(event: InspectorShortcutEvent, platform: InspectorPlatform): boolean {
  if (event.code !== 'KeyB' || event.repeat || event.isComposing || event.shiftKey) return false
  return platform === 'mac'
    ? event.altKey && event.metaKey && !event.ctrlKey
    : event.ctrlKey && event.altKey && !event.metaKey
}

export function inspectorShortcutHint(platform: InspectorPlatform = detectPlatform()): string {
  return platform === 'mac' ? '⌥⌘B' : 'Ctrl+Alt+B'
}

export function installInspectorShortcut(
  store: InspectorStore,
  platform: InspectorPlatform = detectPlatform(),
): () => void {
  if (typeof window === 'undefined') return () => {}
  const onKeyDown = (event: KeyboardEvent) => {
    if (!isInspectorToggle(event, platform)) return
    event.preventDefault()
    store.toggleVisible()
  }
  window.addEventListener('keydown', onKeyDown)
  return () => window.removeEventListener('keydown', onKeyDown)
}
