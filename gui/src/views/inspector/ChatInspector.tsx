import {
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ComponentType,
  type RefObject,
} from 'react'
import type { InspectorKind, InspectorStore } from '../../state/inspector'
import {
  CHAT_MIN_WIDTH,
  INSPECTOR_DEFAULT_WIDTH,
  INSPECTOR_DIVIDER,
  INSPECTOR_MIN_WIDTH,
  inspectorLayout,
  type InspectorLayout,
} from './layout'
import { installInspectorShortcut } from './shortcut'
import { InspectorTabBar, inspectorPanelId, inspectorTabId } from './InspectorTabBar'
import { InspectorEmptyState } from './InspectorEmptyState'
import { ContextInspector } from './ContextInspector'
import { TaskArtifactsInspector } from './TaskArtifactsInspector'
import { TerminalOutputInspector } from './TerminalOutputInspector'
import { LearningReviewInspector } from './LearningReviewInspector'
import { FileInspector } from './FileInspector'
import type { InspectorBodyExtras, InspectorBodyProps } from './InspectorBodyProps'

const PlanWorkflowInspector = lazy(() =>
  import('./PlanWorkflowInspector').then((module) => ({ default: module.PlanWorkflowInspector })),
)

const INSPECTOR_BODIES: Record<InspectorKind, ComponentType<InspectorBodyProps>> = {
  context: ContextInspector,
  workflow: PlanWorkflowInspector,
  artifacts: TaskArtifactsInspector,
  terminal: TerminalOutputInspector,
  learning: LearningReviewInspector,
  file: FileInspector,
}

/**
 * Inspector 外壳容器：TabBar + 加号 + 活动标签正文。所有已打开标签的正文
 * 保持挂载，非活动面板用 hidden/inert（不能只透明化），显隐面板也不卸载，
 * 因此显示/隐藏/切换不会重建任何运行 store。面板本身隐藏时同样 hidden/inert，
 * 键盘与屏幕阅读器不可进入。
 *
 * 焦点规则：显式打开（显隐切换、首次从空状态/加号打开、切换激活标签）后聚焦
 * 目标标签；隐藏后焦点返回触发按钮（returnFocusRef）。后台状态更新不改变
 * activeTab，因此不会抢焦点。
 */
export function ChatInspector({ store, returnFocusRef, onLayoutChange, bodyProps }: {
  store: InspectorStore
  returnFocusRef?: RefObject<HTMLElement | null>
  /** 容器宽度驱动的布局（桌面/紧凑）；卸载或无法探测时收到 null。 */
  onLayoutChange?: (layout: InspectorLayout | null) => void
  bodyProps?: InspectorBodyExtras
}) {
  const state = useSyncExternalStore(store.subscribe, store.getState)
  const rootRef = useRef<HTMLElement>(null)
  const previous = useRef({ visible: state.visible, activeTab: state.activeTab })
  const [parentWidth, setParentWidth] = useState<number | null>(null)
  const [isDragging, setIsDragging] = useState(false)

  useEffect(() => installInspectorShortcut(store), [store])

  // 容器宽度探测：结合手动拖拽自定义宽度，受聊天最小宽度约束；断点逻辑
  // 集中在 ./layout。紧凑模式下 ChatColumns 用面板替换聊天正文（保活
  // hidden/inert），下面的「返回对话」按钮收起面板即回到聊天。
  useEffect(() => {
    const parent = rootRef.current?.parentElement
    if (!parent || typeof ResizeObserver === 'undefined') {
      setParentWidth(null)
      onLayoutChange?.(null)
      return
    }
    const measure = () => {
      setParentWidth(parent.clientWidth)
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(parent)
    return () => {
      observer.disconnect()
      setParentWidth(null)
      onLayoutChange?.(null)
    }
  }, [onLayoutChange])

  const layout = useMemo(() => {
    if (parentWidth === null) return null
    return inspectorLayout(parentWidth, state.size, state.width)
  }, [parentWidth, state.size, state.width])

  useEffect(() => {
    onLayoutChange?.(layout)
  }, [layout, onLayoutChange])

  useEffect(() => {
    const root = rootRef.current
    const was = previous.current
    previous.current = { visible: state.visible, activeTab: state.activeTab }
    if (!root) return
    if (!state.visible) {
      if (was.visible && root.contains(document.activeElement)) returnFocusRef?.current?.focus()
      return
    }
    if (state.activeTab !== null && (!was.visible || was.activeTab !== state.activeTab)) {
      root.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.focus()
    }
  }, [state.visible, state.activeTab, returnFocusRef])

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    e.preventDefault()
    const handleEl = e.currentTarget
    const pointerId = e.pointerId
    const startX = e.clientX
    const rectW = rootRef.current?.getBoundingClientRect().width
    const startWidth = (rectW && rectW > 0 ? rectW : undefined) ?? (layout?.width ?? state.width ?? INSPECTOR_DEFAULT_WIDTH)
    const parentEl = rootRef.current?.parentElement
    const parentW = parentEl?.clientWidth ?? window.innerWidth
    const maxAllowed = Math.max(INSPECTOR_MIN_WIDTH, parentW - CHAT_MIN_WIDTH - INSPECTOR_DIVIDER)

    setIsDragging(true)
    if (typeof handleEl.setPointerCapture === 'function') {
      try {
        handleEl.setPointerCapture(pointerId)
      } catch {}
    }

    const onPointerMove = (ev: PointerEvent) => {
      const deltaX = startX - ev.clientX
      const rawWidth = startWidth + deltaX
      const clampedWidth = Math.round(Math.min(maxAllowed, Math.max(INSPECTOR_MIN_WIDTH, rawWidth)))
      store.setWidth(clampedWidth)
    }

    const onPointerUp = (ev: PointerEvent) => {
      setIsDragging(false)
      if (typeof handleEl.releasePointerCapture === 'function') {
        try {
          handleEl.releasePointerCapture(ev.pointerId)
        } catch {}
      }
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', onPointerUp)
      window.removeEventListener('pointercancel', onPointerUp)
      document.body.style.removeProperty('cursor')
      document.body.style.removeProperty('user-select')
    }

    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    window.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', onPointerUp)
    window.addEventListener('pointercancel', onPointerUp)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const currentWidth = layout?.width ?? state.width ?? INSPECTOR_DEFAULT_WIDTH
    const parentEl = rootRef.current?.parentElement
    const parentW = parentEl?.clientWidth ?? window.innerWidth
    const maxAllowed = Math.max(INSPECTOR_MIN_WIDTH, parentW - CHAT_MIN_WIDTH - INSPECTOR_DIVIDER)
    const step = e.shiftKey ? 50 : 10

    if (e.key === 'ArrowLeft') {
      e.preventDefault()
      store.setWidth(Math.min(maxAllowed, currentWidth + step))
    } else if (e.key === 'ArrowRight') {
      e.preventDefault()
      store.setWidth(Math.max(INSPECTOR_MIN_WIDTH, currentWidth - step))
    } else if (e.key === 'Home') {
      e.preventDefault()
      store.setWidth(INSPECTOR_MIN_WIDTH)
    } else if (e.key === 'End') {
      e.preventDefault()
      store.setWidth(maxAllowed)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      store.setWidth(INSPECTOR_DEFAULT_WIDTH)
    }
  }

  const warning = store.getStorageWarning()
  return (
    <aside ref={rootRef} className={`chat-inspector${state.size === 'expanded' ? ' is-expanded' : ''}`}
      style={layout ? { width: layout.width } : undefined}
      data-layout-mode={layout?.mode}
      aria-label="执行面板" hidden={!state.visible} inert={!state.visible}>
      {layout?.mode !== 'compact' && state.visible && (
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="拖拽调整面板宽度"
          aria-valuenow={layout?.width ?? INSPECTOR_DEFAULT_WIDTH}
          aria-valuemin={INSPECTOR_MIN_WIDTH}
          tabIndex={0}
          className={`inspector-resizer${isDragging ? ' is-dragging' : ''}`}
          onPointerDown={handlePointerDown}
          onDoubleClick={() => store.setWidth(INSPECTOR_DEFAULT_WIDTH)}
          onKeyDown={handleKeyDown}
        />
      )}
      {warning && <p role="status" className="inspector-storage-note">{warning}</p>}
      {layout?.mode === 'compact' && (
        <div className="inspector-compact-bar">
          <button type="button" className="editor-button" onClick={() => store.setVisible(false)}>← 返回对话</button>
        </div>
      )}
      <InspectorTabBar tabs={state.tabs} activeTab={state.activeTab}
        onActivate={kind => store.activateTab(kind)}
        onClose={kind => store.closeTab(kind)}
        onAdd={kind => store.openInspectorTab(kind)} />
      {state.tabs.length === 0 ? (
        <InspectorEmptyState onOpen={kind => store.openInspectorTab(kind)} />
      ) : (
        state.tabs.map(kind => {
          const Body = INSPECTOR_BODIES[kind]
          const active = kind === state.activeTab
          return (
            <div key={kind} role="tabpanel" id={inspectorPanelId(kind)} aria-labelledby={inspectorTabId(kind)}
              tabIndex={0} className="inspector-panel" hidden={!active} inert={!active}
              style={kind === 'file' ? { padding: 0, overflow: 'hidden' } : undefined}>
              <Suspense fallback={<p className="p-4 text-sm text-secondary">正在打开工具…</p>}>
                <Body {...bodyProps} inspectorStore={store} target={state.targets[kind]} active={active} />
              </Suspense>
            </div>
          )
        })
      )}
    </aside>
  )
}
