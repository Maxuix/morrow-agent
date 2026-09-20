import type { ApiClient } from '../../api/client'
import type { DirtyGuard, SettingsSection } from '../../state/navigation'
import type { Theme } from '../../state/theme'
import { useWorkspaceName } from '../management/hooks'
import { ProviderSettings } from '../ProviderSettings'
import { PageScaffold } from '../pages/PageScaffold'
import { AppearanceSection } from './AppearanceSection'
import { AgentSettingsSection } from './AgentSettingsSection'
import { DiagnosticsSection } from './DiagnosticsSection'
import { WorkspaceModelDefaults } from './WorkspaceModelDefaults'

const SECTION_LABELS: Record<SettingsSection, string> = {
  providers: 'Provider 与模型',
  agents: 'Agent 预设',
  appearance: '外观',
  diagnostics: '诊断与维护',
}

/**
 * Settings main page: four sections live here. Provider/appearance work without
 * a workspace (N01). Agent presets bind to the current project.
 */
export function SettingsPage({
  client,
  section,
  onNavigate,
  onBack,
  workspaceId = null,
  sessionId = null,
  connection = 'offline',
  theme,
  onThemeChange,
  registerGuard,
  onManageTools,
}: {
  client: ApiClient
  section: SettingsSection
  onNavigate: (section: SettingsSection) => void
  onBack: () => void
  workspaceId?: string | null
  sessionId?: string | null
  connection?: string
  theme: Theme
  onThemeChange: (theme: Theme) => void
  registerGuard?: (guard: DirtyGuard) => () => void
  onManageTools?: () => void
}) {
  const workspaceName = useWorkspaceName(client, workspaceId)
  return (
    <PageScaffold
      title="设置"
      onBack={onBack}
      tabs={(Object.keys(SECTION_LABELS) as SettingsSection[]).map(key => ({
        id: key,
        label: SECTION_LABELS[key],
        active: key === section,
        onSelect: () => onNavigate(key),
      }))}
    >
      {section === 'providers' && (
        <ProviderSettings
          client={client}
          registerGuard={registerGuard}
          workspaceDefaults={
            workspaceId ? (
              <WorkspaceModelDefaults
                client={client}
                workspaceId={workspaceId}
                sessionId={sessionId}
                workspaceName={workspaceName}
              />
            ) : undefined
          }
        />
      )}
      {section === 'agents' && (
        workspaceId
          ? (
            <AgentSettingsSection
              key={workspaceId}
              client={client}
              workspaceId={workspaceId}
              workspaceName={workspaceName}
              connection={connection}
              registerGuard={registerGuard}
              onManageTools={onManageTools}
            />
          )
          : (
            <section className="settings-page" aria-label="Agent 预设">
              <div className="settings-content">
                <header className="settings-heading">
                  <span className="settings-eyebrow">设置 / Agent 预设</span>
                  <h2>Agent 预设与定义</h2>
                  <p>Agent 预设绑定当前项目。请先打开一个工作区后再编辑目录或定义。</p>
                </header>
              </div>
            </section>
          )
      )}
      {section === 'appearance' && (
        <AppearanceSection theme={theme} onThemeChange={onThemeChange} />
      )}
      {section === 'diagnostics' && (
        <DiagnosticsSection client={client} connection={connection} />
      )}
    </PageScaffold>
  )
}
