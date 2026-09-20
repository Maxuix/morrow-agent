import { Suspense, lazy } from 'react'
import type { ApiClient } from '../../api/client'
import type { DirtyGuard } from '../../state/navigation'
import { PageScaffold } from '../pages/PageScaffold'

const EditorShell = lazy(() =>
  import('../EditorShell').then((module) => ({ default: module.EditorShell })),
)

/**
 * 「工作流定义」主页面容器：仅展示工作流编辑器（EditorShell）。
 * 项目文件查看能力已完全收敛至右侧 Inspector 面板（仅查看当前会话文件）。
 */
export function WorkspaceEditorPage({
  client,
  onManageTools,
  onManageAgents,
  onBack,
  registerGuard,
}: {
  client: ApiClient
  onManageTools?: () => void
  onManageAgents?: () => void
  onBack: () => void
  registerGuard?: (guard: DirtyGuard) => () => void
}) {
  return (
    <PageScaffold
      title="工作流定义"
      onBack={onBack}
    >
      <Suspense fallback={<p className="p-6 text-sm text-secondary">正在加载工作流编辑器…</p>}>
        <EditorShell client={client} onManageTools={onManageTools} onManageAgents={onManageAgents} registerGuard={registerGuard} />
      </Suspense>
    </PageScaffold>
  )
}
