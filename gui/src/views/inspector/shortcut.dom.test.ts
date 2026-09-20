// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { InspectorStore } from '../../state/inspector'
import { installInspectorShortcut, isInspectorToggle } from './shortcut'

const base = { code: 'KeyB', altKey: false, metaKey: false, ctrlKey: false, shiftKey: false, repeat: false, isComposing: false }

describe('isInspectorToggle', () => {
  it('requires the exact per-platform combination', () => {
    expect(isInspectorToggle({ ...base, altKey: true, metaKey: true }, 'mac')).toBe(true)
    expect(isInspectorToggle({ ...base, ctrlKey: true, altKey: true }, 'other')).toBe(true)
    // 平台组合不互换
    expect(isInspectorToggle({ ...base, ctrlKey: true, altKey: true }, 'mac')).toBe(false)
    expect(isInspectorToggle({ ...base, altKey: true, metaKey: true }, 'other')).toBe(false)
    // 多按修饰键、缺键、错键都不算精确组合
    expect(isInspectorToggle({ ...base, altKey: true, metaKey: true, ctrlKey: true }, 'mac')).toBe(false)
    expect(isInspectorToggle({ ...base, altKey: true, metaKey: true, shiftKey: true }, 'mac')).toBe(false)
    expect(isInspectorToggle({ ...base, metaKey: true }, 'mac')).toBe(false)
    expect(isInspectorToggle({ ...base, code: 'KeyV', altKey: true, metaKey: true }, 'mac')).toBe(false)
  })

  it('ignores IME composition and key repeat', () => {
    expect(isInspectorToggle({ ...base, altKey: true, metaKey: true, isComposing: true }, 'mac')).toBe(false)
    expect(isInspectorToggle({ ...base, ctrlKey: true, altKey: true, repeat: true }, 'other')).toBe(false)
  })
})

describe('installInspectorShortcut', () => {
  it('toggles visibility exactly once per real keydown, even from a focused input', () => {
    const store = new InspectorStore()
    const detach = installInspectorShortcut(store, 'other')
    const input = document.createElement('input')
    document.body.appendChild(input)
    input.focus()

    const event = new KeyboardEvent('keydown', { code: 'KeyB', ctrlKey: true, altKey: true, bubbles: true, cancelable: true })
    input.dispatchEvent(event)
    expect(store.getState().visible).toBe(true)
    expect(event.defaultPrevented).toBe(true)

    // 按住重复不触发
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyB', ctrlKey: true, altKey: true, repeat: true, bubbles: true }))
    expect(store.getState().visible).toBe(true)

    // IME composition 中不触发
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyB', ctrlKey: true, altKey: true, isComposing: true, bubbles: true }))
    expect(store.getState().visible).toBe(true)

    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyB', ctrlKey: true, altKey: true, bubbles: true }))
    expect(store.getState().visible).toBe(false)

    detach()
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyB', ctrlKey: true, altKey: true, bubbles: true }))
    expect(store.getState().visible).toBe(false)
    input.remove()
  })
})
