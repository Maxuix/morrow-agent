import { useEffect, useRef } from 'react'

/**
 * Floating header menus are native <details> elements, which stay open until
 * their own summary is toggled again — so two menus overlap on screen. Shared
 * dismissal behavior: a pointer press outside the element (a blank area, or
 * another menu's button) and the Escape key collapse it, mirroring the
 * composer command menu. Presses inside keep their own handlers, including
 * the summary toggle and the per-item close in the tools popover.
 */

/** A press on an open popover dismisses it only when it lands outside. */
export function pressDismissesPopover(popover: {contains(node: unknown): boolean} | null, target: unknown): boolean {
  if (!popover) return false
  return !popover.contains(target)
}

export function useDismissablePopover() {
  const ref = useRef<HTMLDetailsElement | null>(null)
  useEffect(() => {
    const element = ref.current
    if (!element) return
    const onPointerDown = (event: PointerEvent) => {
      if (element.open && pressDismissesPopover(element, event.target)) element.open = false
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && element.open) element.open = false
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [])
  return ref
}
