import type { InspectorSessionState } from '../../state/inspector'

/**
 * 双栏尺寸与断点的唯一出处：
 * 主内容区可用宽度小于「480 聊天 + 400 面板 + 分隔线」时判定为紧凑；
 * 紧凑模式下 ChatColumns 用面板替换聊天正文（保活 hidden/inert），面板
 * 提供「返回对话」（见 ChatInspector / ChatColumns）。
 */
export const CHAT_MIN_WIDTH = 480
export const INSPECTOR_MIN_WIDTH = 280
export const INSPECTOR_DEFAULT_WIDTH = 400
export const INSPECTOR_WIDTHS: Record<InspectorSessionState['size'], number> = { normal: 400, expanded: 620 }
export const INSPECTOR_DIVIDER = 1
export const COMPACT_THRESHOLD = CHAT_MIN_WIDTH + INSPECTOR_WIDTHS.normal + INSPECTOR_DIVIDER

export type InspectorLayoutMode = 'regular' | 'compact'

export interface InspectorLayout {
  mode: InspectorLayoutMode
  width: number
}

export function inspectorLayout(
  availableWidth: number,
  size: InspectorSessionState['size'],
  customWidth?: number,
): InspectorLayout {
  const desired = customWidth ?? INSPECTOR_WIDTHS[size]
  if (availableWidth >= COMPACT_THRESHOLD) {
    // 宽模式受剩余宽度约束：聊天至少保留 CHAT_MIN_WIDTH。
    const room = availableWidth - CHAT_MIN_WIDTH - INSPECTOR_DIVIDER
    const minW = customWidth !== undefined ? INSPECTOR_MIN_WIDTH : INSPECTOR_WIDTHS.normal
    return { mode: 'regular', width: Math.max(minW, Math.min(desired, room)) }
  }
  return { mode: 'compact', width: Math.max(INSPECTOR_MIN_WIDTH, Math.min(desired, availableWidth - INSPECTOR_DIVIDER)) }
}

/**
 * 加宽（expanded 620）受限时的明确说明，挂在宽度切换按钮的 title 上；
 * 未受限或为常规宽度时返回 null。布局切换只改 CSS 尺寸，不触碰浏览器
 * fullscreen 状态。
 */
export function expandedClampNote(size: InspectorSessionState['size'], layout: InspectorLayout | null): string | null {
  if (size !== 'expanded' || layout === null) return null
  if (layout.mode === 'compact') return '可用宽度不足，加宽面板已回退为紧凑模式（替换对话正文）'
  if (layout.width < INSPECTOR_WIDTHS.expanded) {
    return `可用宽度不足，面板限制为 ${layout.width}px（目标 ${INSPECTOR_WIDTHS.expanded}px）`
  }
  return null
}
