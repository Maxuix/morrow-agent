import type { ComponentType } from 'react'
import type { InspectorKind } from '../../state/inspector'

/** 与 Sidebar 相同的 16px 描边图标规格；复用 currentColor 与既有 token。 */
function Icon({ d, size = 16 }: { d: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
  )
}

const PATHS = {
  context: 'M3 2.8h10a1.2 1.2 0 0 1 1.2 1.2v7A1.2 1.2 0 0 1 13 12.2H8l-3 2v-2H3a1.2 1.2 0 0 1-1.2-1.2V4A1.2 1.2 0 0 1 3 2.8Z',
  workflow: 'M3 2h2.4a1 1 0 0 1 1 1v2a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1Zm7.6 0H13a1 1 0 0 1 1 1v2a1 1 0 0 1-1 1h-2.4a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1ZM6.8 10h2.4a1 1 0 0 1 1 1v2a1 1 0 0 1-1 1H6.8a1 1 0 0 1-1-1v-2a1 1 0 0 1 1-1ZM4.2 6v1.4a1.8 1.8 0 0 0 1.8 1.8h4a1.8 1.8 0 0 0 1.8-1.8V6M8 9.2v.8',
  artifacts: 'M1.9 4.2a1.4 1.4 0 0 1 1.4-1.4h2.9l1.4 1.7h5.1a1.4 1.4 0 0 1 1.4 1.4v6a1.4 1.4 0 0 1-1.4 1.4H3.3a1.4 1.4 0 0 1-1.4-1.4Z',
  terminal: 'M3.8 2.8h8.4a2 2 0 0 1 2 2v6.4a2 2 0 0 1-2 2H3.8a2 2 0 0 1-2-2V4.8a2 2 0 0 1 2-2Zm.7 3.2 2.5 2-2.5 2M8.5 10.5H12',
  learning: 'M8 2.5 14 5.5 8 8.5 2 5.5ZM4.2 7.2v3.2c0 1 1.7 2.1 3.8 2.1s3.8-1.1 3.8-2.1V7.2',
  file: 'M4.2 1.9h4.3l3.3 3.3v8.9a1 1 0 0 1-1 1H4.2a1 1 0 0 1-1-1V2.9a1 1 0 0 1 1-1Zm4.1.2v3.4h3.4',
  plus: 'M8 3.2v9.6M3.2 8h9.6',
  close: 'm4 4 8 8M12 4l-8 8',
  panelRight: 'M3.8 2.8h8.4a2 2 0 0 1 2 2v6.4a2 2 0 0 1-2 2H3.8a2 2 0 0 1-2-2V4.8a2 2 0 0 1 2-2ZM9.8 2.8v10.4',
  width: 'M2.5 8h11M5 5.5 2.5 8 5 10.5m6-5 2.5 2.5-2.5 2.5',
} as const

export const IconPlus = () => <Icon d={PATHS.plus} />
export const IconClose = () => <Icon d={PATHS.close} size={12} />
export const IconPanelRight = () => <Icon d={PATHS.panelRight} />
export const IconWidth = () => <Icon d={PATHS.width} />

export const INSPECTOR_META: Record<InspectorKind, { label: string; description: string; Icon: ComponentType }> = {
  context: { label: '上下文', description: '查看本轮运行实际生效的上下文与约束', Icon: () => <Icon d={PATHS.context} /> },
  workflow: { label: '计划与工作流', description: '审阅任务计划、节点与工作流运行', Icon: () => <Icon d={PATHS.workflow} /> },
  artifacts: { label: '任务与产物', description: '浏览任务结果、文件与 Artifact', Icon: () => <Icon d={PATHS.artifacts} /> },
  terminal: { label: '终端输出', description: '查看命令与工具执行的输出', Icon: () => <Icon d={PATHS.terminal} /> },
  learning: { label: '学习审阅', description: '审阅本会话产生的学习候选', Icon: () => <Icon d={PATHS.learning} /> },
  file: { label: '文件', description: '查看结果与消息里打开的文件', Icon: () => <Icon d={PATHS.file} /> },
}
