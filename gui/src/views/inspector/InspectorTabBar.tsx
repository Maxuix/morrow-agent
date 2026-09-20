import { useEffect, useRef, type KeyboardEvent } from 'react'
import type { InspectorKind } from '../../state/inspector'
import { INSPECTOR_META, IconClose } from './meta'
import { InspectorAddMenu } from './InspectorAddMenu'

export const inspectorTabId = (kind: InspectorKind) => `inspector-tab-${kind}`
export const inspectorPanelId = (kind: InspectorKind) => `inspector-panel-${kind}`

/**
 * tablist/tab 语义 + roving tabindex：方向键/Home/End 切换（自动激活），
 * Delete 关闭当前标签。关闭按钮是独立 button（不能 button 嵌套 button），
 * 触屏常显。标签区横向滚动，加号固定在滚动区外始终可见。
 */
export function InspectorTabBar({ tabs, activeTab, onActivate, onClose, onAdd }: {
  tabs: readonly InspectorKind[]
  activeTab: InspectorKind | null
  onActivate: (kind: InspectorKind) => void
  onClose: (kind: InspectorKind) => void
  onAdd: (kind: InspectorKind) => void
}) {
  const tabRefs = useRef(new Map<InspectorKind, HTMLElement>())
  const addRef = useRef<HTMLDivElement>(null)
  const pendingFocus = useRef<InspectorKind | 'add' | null>(null)

  useEffect(() => {
    const target = pendingFocus.current
    if (target === null) return
    pendingFocus.current = null
    if (target === 'add') addRef.current?.querySelector('button')?.focus()
    else tabRefs.current.get(target)?.focus()
  }, [tabs])

  const close = (kind: InspectorKind) => {
    const index = tabs.indexOf(kind)
    if (index < 0) return
    // 关闭活动标签：焦点到右邻/左邻/加号；关闭非活动标签：焦点回到当前标签。
    pendingFocus.current = kind === activeTab
      ? tabs[index + 1] ?? tabs[index - 1] ?? 'add'
      : activeTab ?? 'add'
    onClose(kind)
  }

  const onKeyDown = (event: KeyboardEvent) => {
    if (tabs.length === 0) return
    const focused = tabs.findIndex(kind => tabRefs.current.get(kind)?.contains(document.activeElement))
    if (event.key === 'Delete') {
      if (focused >= 0) {
        event.preventDefault()
        close(tabs[focused])
      }
      return
    }
    const from = focused >= 0 ? focused : tabs.indexOf(activeTab ?? tabs[0])
    let next: number | null = null
    if (event.key === 'ArrowRight') next = (from + 1) % tabs.length
    else if (event.key === 'ArrowLeft') next = (from - 1 + tabs.length) % tabs.length
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = tabs.length - 1
    if (next === null) return
    event.preventDefault()
    onActivate(tabs[next])
    tabRefs.current.get(tabs[next])?.focus()
  }

  return (
    <div className="inspector-tabbar">
      <div role="tablist" aria-label="执行工具" className="inspector-tabs" onKeyDown={onKeyDown}>
        {tabs.map(kind => {
          const meta = INSPECTOR_META[kind]
          const selected = kind === activeTab
          return (
            <div key={kind} role="tab" id={inspectorTabId(kind)} aria-label={meta.label}
              aria-selected={selected} aria-controls={inspectorPanelId(kind)}
              tabIndex={selected ? 0 : -1} className="inspector-tab"
              ref={element => {
                if (element) tabRefs.current.set(kind, element)
                else tabRefs.current.delete(kind)
              }}
              onClick={() => onActivate(kind)}>
              <span className="inspector-tab-label">{meta.label}</span>
              <button type="button" className="inspector-tab-close" aria-label={`关闭${meta.label}`}
                onClick={event => { event.stopPropagation(); close(kind) }}>
                <IconClose />
              </button>
            </div>
          )
        })}
      </div>
      <div ref={addRef} className="inspector-add-anchor">
        <InspectorAddMenu openKinds={tabs} onSelect={onAdd} />
      </div>
    </div>
  )
}
