import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { INSPECTOR_KINDS, type InspectorKind } from '../../state/inspector'
import { INSPECTOR_META, IconPlus } from './meta'

/**
 * 加号菜单：Enter/Space 打开（原生 button 行为）、方向键在可用项间移动、
 * Escape 关闭并回到加号、点击外部关闭。已打开的工具置为 disabled；五种
 * 全部打开时菜单仍可打开，加号保持可操作。
 */
export function InspectorAddMenu({ openKinds, onSelect }: {
  openKinds: readonly InspectorKind[]
  onSelect: (kind: InspectorKind) => void
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const itemRefs = useRef(new Map<InspectorKind, HTMLButtonElement>())
  const enabledKinds = INSPECTOR_KINDS.filter(kind => !openKinds.includes(kind))

  useEffect(() => {
    if (!open) return
    const root = rootRef.current
    const onPointerDown = (event: PointerEvent) => {
      if (root && !root.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  // 只在开合时聚焦首项；openKinds 变化不抢焦点。
  const firstEnabled = enabledKinds[0]
  useEffect(() => {
    if (open && firstEnabled !== undefined) itemRefs.current.get(firstEnabled)?.focus()
  }, [open])

  const close = (focusButton: boolean) => {
    setOpen(false)
    if (focusButton) buttonRef.current?.focus()
  }

  const onMenuKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      close(true)
      return
    }
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
    event.preventDefault()
    if (enabledKinds.length === 0) return
    const focused = enabledKinds.findIndex(kind => itemRefs.current.get(kind) === document.activeElement)
    const step = event.key === 'ArrowDown' ? 1 : -1
    const next = enabledKinds[(focused + step + enabledKinds.length) % enabledKinds.length]
    itemRefs.current.get(next)?.focus()
  }

  return (
    <div className="inspector-add" ref={rootRef} onKeyDown={onMenuKeyDown}>
      <button type="button" ref={buttonRef} className="inspector-add-button" aria-label="添加执行工具"
        title="添加执行工具" aria-haspopup="menu" aria-expanded={open}
        onClick={() => setOpen(current => !current)}>
        <IconPlus />
      </button>
      {open && (
        <div role="menu" aria-label="添加执行工具" className="inspector-add-menu">
          {INSPECTOR_KINDS.map(kind => {
            const meta = INSPECTOR_META[kind]
            const opened = openKinds.includes(kind)
            return (
              <button key={kind} type="button" role="menuitem" className="inspector-add-item" disabled={opened}
                ref={element => {
                  if (element) itemRefs.current.set(kind, element)
                  else itemRefs.current.delete(kind)
                }}
                onClick={() => { onSelect(kind); setOpen(false) }}>
                <span>{meta.label}</span>
                {opened && <span className="menu-note">已打开</span>}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
