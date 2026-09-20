import type { ApiClient } from '../../api/client'
import type { RunViewWire, WorkflowRunWire } from '../../api/types'
import type { ActivityStore } from '../../state/activity'
import type { AppLocation, DirtyGuard } from '../../state/navigation'
import type { FileBufferStore } from '../../state/fileBuffer'
import type { FileTarget, InspectorStore, InspectorTarget } from '../../state/inspector'
import type { SyncStore, WorkflowRunProjection } from '../../state/sync'
import type { TaskPlanStore } from '../../state/taskPlan'

/** Shared dependency boundary for mounted Inspector bodies. */
export interface InspectorBodyProps {
  target?: InspectorTarget
  active: boolean
  client?: ApiClient
  workspaceId?: string
  sessionId?: string
  currentTaskRunId?: string | null
  syncStore?: SyncStore
  planStore?: TaskPlanStore
  connection?: string
  /** The one session-scoped activity stream owned by ChatWorkspace. */
  activityStore?: ActivityStore | null
  runs?: WorkflowRunProjection[]
  showRun?: boolean
  onPreferRun?: (preferRun: boolean) => void
  onReturnToRoot?: (() => void) | null
  onWorkflowEdit?: (run: WorkflowRunWire, view: RunViewWire) => void
  focusNodeId?: string | null
  onNavigate?: (location: AppLocation) => void
  /** Shared file-open entry: result lists, Markdown links and product panels. */
  onOpenFile?: (target: FileTarget) => void
  /** 文件编辑缓冲区：草稿只活在面板存活期的内存里。 */
  fileBuffers?: FileBufferStore
  /** 外壳 store：file 面板用它注册“打开另一文件/关闭标签”前的离开确认。 */
  inspectorStore?: InspectorStore
  /** 草稿离开保护：注册到导航 store 的 guard 栈，覆盖切会话/切页面/刷新。 */
  registerGuard?: (guard: DirtyGuard) => () => void
}

export type InspectorBodyExtras = Omit<InspectorBodyProps, 'target' | 'active'>
