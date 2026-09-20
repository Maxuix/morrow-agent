import { useCallback, useState, useSyncExternalStore, type ReactNode, type RefObject } from 'react'
import type { InspectorStore } from '../../state/inspector'
import { ChatInspector } from './ChatInspector'
import type { InspectorLayout } from './layout'
import type { InspectorBodyExtras } from './InspectorBodyProps'

/**
 * 聊天双栏容器（2.5 紧凑模式）：桌面模式下聊天正文与右侧面板并排；容器宽度
 * 低于 ./layout 的临界值且面板可见时，面板在主内容区「替换」聊天正文（不是
 * 全屏遮罩）——正文保持挂载并置 hidden/inert，不卸载、不取消运行、草稿与
 * 滚动位置不丢；面板的「返回对话」收起面板后正文原样恢复。布局模式只来自
 * 容器宽度探测，不写入持久化状态，也不触碰浏览器 fullscreen。
 *
 * `center` 为渲染函数以便正文头部（宽度切换按钮 title）拿到布局信息。
 */
export function ChatColumns({ store, returnFocusRef, center, bodyProps }: {
  store: InspectorStore
  returnFocusRef?: RefObject<HTMLElement | null>
  center: (layout: InspectorLayout | null) => ReactNode
  bodyProps?: InspectorBodyExtras
}) {
  const visible = useSyncExternalStore(store.subscribe, store.getState).visible
  const [layout, setLayout] = useState<InspectorLayout | null>(null)
  const handleLayout = useCallback((next: InspectorLayout | null) => {
    setLayout(current => {
      if (current === next) return current
      if (current !== null && next !== null && current.mode === next.mode && current.width === next.width) return current
      return next
    })
  }, [])
  // 面板隐藏时不进入紧凑替换：聊天正文始终占满可用宽度。
  const compact = layout?.mode === 'compact' && visible
  return (
    <>
      <div className="chat-center" data-layout-mode={compact ? 'compact' : 'regular'} hidden={compact} inert={compact}>
        {center(layout)}
      </div>
      <ChatInspector store={store} returnFocusRef={returnFocusRef} onLayoutChange={handleLayout} bodyProps={bodyProps} />
    </>
  )
}
