import type { ApiClient } from '../../api/client'
import type { Scope } from '../../api/management'
import type { DirtyGuard, ToolsSection } from '../../state/navigation'
import { useState } from 'react'
import { SkillManager } from '../SkillManager'
import { McpManager } from '../McpManager'
import { useManagementMutate, useWorkspaceName } from '../management/hooks'
import { buttonClass } from '../management/styles'
import { PageScaffold } from '../pages/PageScaffold'

const SECTION_LABELS: Record<ToolsSection, string> = {
  skills: 'Skills',
  mcp: 'MCP 服务',
}

export function WorkspaceToolsPage({
  client,
  workspaceId,
  section,
  item,
  connected,
  onChanged,
  onNavigate,
  onBack,
  registerGuard,
}: {
  client: ApiClient
  workspaceId?: string | null
  section: ToolsSection
  item?: string
  connected: boolean
  onChanged?: () => void
  onNavigate: (section: ToolsSection) => void
  onBack: () => void
  registerGuard?: (guard: DirtyGuard) => () => void
}) {
  const [scope, setScope] = useState<Scope>('workspace')
  const { mutate, busy, message, refresh, reload } = useManagementMutate(client, connected, onChanged)
  const workspaceName = useWorkspaceName(client, workspaceId ?? null)
  return (
    <PageScaffold
      title="技能与 MCP 工具"
      onBack={onBack}
      tabs={(Object.keys(SECTION_LABELS) as ToolsSection[]).map(key => ({
        id: key,
        label: SECTION_LABELS[key],
        active: key === section,
        onSelect: () => onNavigate(key),
      }))}
    >
      <div className="asset-page">
<p className="pp-hint">{workspaceName}</p>
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <div className="pp-filter" role="group" aria-label="查看范围">
            <button type="button" className={buttonClass} aria-pressed={scope === 'workspace'} onClick={() => setScope('workspace')}>
              本项目
            </button>
            <button type="button" className={buttonClass} aria-pressed={scope === 'global'} onClick={() => setScope('global')}>
              全局安装
            </button>
          </div>
          <button type="button" className={buttonClass} disabled={busy} onClick={reload}>刷新</button>
          {!connected && <span role="status">连接中断，恢复后可编辑。</span>}
          <span role="status" className="text-secondary">{message}</span>
        </div>
        <div className="min-h-0 flex-1">
          {section === 'skills'
            ? <SkillManager key={`${workspaceId ?? ''}:${scope}`} client={client} scope={scope} refresh={refresh} mutate={mutate} item={item} registerGuard={registerGuard} />
            : <McpManager key={`${workspaceId ?? ''}:${scope}`} client={client} scope={scope} refresh={refresh} item={item} registerGuard={registerGuard} />}
        </div>
      </div>
    </PageScaffold>
  )
}
