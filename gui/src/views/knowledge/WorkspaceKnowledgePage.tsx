import { Suspense, lazy } from 'react'
import type { ApiClient } from '../../api/client'
import type { DirtyGuard, KnowledgeFocus, KnowledgeSection } from '../../state/navigation'
import { useManagementMutate } from '../management/hooks'
import { PageScaffold } from '../pages/PageScaffold'
import { KnowledgeLibraryPage } from './KnowledgeLibraryPage'

const PreferenceProfilePage = lazy(() =>
  import('../PreferenceProfilePage').then((module) => ({ default: module.PreferenceProfilePage })),
)

const SECTION_LABELS: Record<KnowledgeSection, string> = {
  profile: '项目画像',
  preferences: '行为偏好',
  knowledge: '知识库',
}

export function WorkspaceKnowledgePage({
  client,
  workspaceId,
  section,
  focus,
  connected,
  registerGuard,
  onNavigate,
  onBack,
}: {
  client: ApiClient
  workspaceId: string
  section: KnowledgeSection
  focus?: KnowledgeFocus
  connected: boolean
  registerGuard?: (guard: DirtyGuard) => () => void
  onNavigate: (section: KnowledgeSection) => void
  onBack: () => void
}) {
  const { mutate, busy, message, refresh, reload } = useManagementMutate(client, connected)
  return (
    <PageScaffold
      title="项目知识与偏好"
      onBack={onBack}
      tabs={(Object.keys(SECTION_LABELS) as KnowledgeSection[]).map(key => ({
        id: key,
        label: SECTION_LABELS[key],
        active: key === section,
        onSelect: () => onNavigate(key),
      }))}
    >
      {section === 'knowledge' ? (
        <div className="min-h-0 flex-1">
          <div className="flex flex-wrap items-center gap-3 px-6 pt-3 text-xs">
            <button type="button" className="pp-button" disabled={busy} onClick={reload}>刷新</button>
            {!connected && <span role="status">连接中断，恢复后可编辑。</span>}
            <span role="status" className="text-secondary">{message}</span>
          </div>
          <KnowledgeLibraryPage key={workspaceId} client={client} workspaceId={workspaceId} connected={connected && !busy} mutate={mutate} refresh={refresh} onChanged={reload} focus={focus} registerGuard={registerGuard} />
        </div>
      ) : (
        <Suspense fallback={<p className="p-6 text-sm text-secondary">正在加载偏好与项目画像…</p>}>
          <PreferenceProfilePage
            key={workspaceId}
            client={client}
            workspaceId={workspaceId}
            focus={section}
            anchor={focus}
            embedded
            connected={connected}
            registerGuard={registerGuard}
          />
        </Suspense>
      )}
    </PageScaffold>
  )
}
