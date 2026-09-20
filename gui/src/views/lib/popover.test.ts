import { describe, expect, it } from 'vitest'
import { pressDismissesPopover } from './popover'

describe('popover outside-press dismissal', () => {
  const popover = { contains: (node: unknown) => node === 'inside' }

  it('dismisses an open popover only for presses outside it', () => {
    expect(pressDismissesPopover(popover, 'outside')).toBe(true)
    expect(pressDismissesPopover(popover, 'inside')).toBe(false)
  })

  it('dismisses when the press target cannot be located', () => {
    expect(pressDismissesPopover(null, 'outside')).toBe(false)
    expect(pressDismissesPopover(popover, null)).toBe(true)
  })
})
