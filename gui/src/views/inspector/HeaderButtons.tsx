import { useMemo, useSyncExternalStore, type RefObject } from 'react'
import type { InspectorStore } from '../../state/inspector'
import type { InspectorLayout } from './layout'
import { IconPanelRight } from './meta'
import { inspectorShortcutHint } from './shortcut'

/**
 * 聊天顶栏的 Inspector 图标按钮：显隐切换。
 * 面板宽度已改为在双栏分界线上直接左右手动拖拽调整。
 * `toggleRef` 指向显隐按钮，面板隐藏后焦点回到它（见 ChatInspector）。
 */
export function InspectorHeaderButtons({ store, toggleRef }: {
  store: InspectorStore
  toggleRef?: RefObject<HTMLButtonElement | null>
  layout?: InspectorLayout | null
}) {
  const state = useSyncExternalStore(store.subscribe, store.getState)
  const hint = useMemo(() => inspectorShortcutHint(), [])
  return (
    <button type="button" ref={toggleRef} className="icon-button inspector-icon-button" aria-pressed={state.visible}
      aria-label={state.visible ? '隐藏执行面板' : '显示执行面板'} title={`显示/隐藏执行面板（${hint}）`}
      onClick={() => store.toggleVisible()}><IconPanelRight/></button>
  )
}
