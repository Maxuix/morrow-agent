import { useCallback, useEffect, useState } from 'react'
import type { ApiClient } from '../../api/client'
import type { DirtyGuard } from '../../state/navigation'
import type {
  AgentDefinitionViewWire,
  ProviderCatalogWire,
  SkillCatalogWire,
  ToolCatalogWire,
} from '../../api/types'
import { LeaveGuardDialog, useLeaveGuard, useLeavePrompt } from '../management/LeaveGuard'
import { AgentInspector } from '../AgentInspector'
import { AgentPresetsPanel } from '../AgentPresetsPanel'

const EMPTY_CATALOGS = {
  providers: [] as ProviderCatalogWire[],
  skills: [] as SkillCatalogWire[],
  tools: [] as ToolCatalogWire[],
}

/**
 * One settings surface for preset catalog, project model preferences, copy-as-
 * custom, and AgentDefinition publish / enable / disable / revoke. Browsing
 * issues only GET catalog reads; materializing writes stay on the original
 * commands.
 */
export function AgentSettingsSection({
  client,
  workspaceId,
  workspaceName,
  connection,
  registerGuard,
  onManageTools,
}: {
  client: ApiClient
  workspaceId: string
  workspaceName: string
  connection: string
  registerGuard?: (guard: DirtyGuard) => () => void
  onManageTools?: () => void
}) {
  const [definitions, setDefinitions] = useState<AgentDefinitionViewWire[]>([])
  const [catalogs, setCatalogs] = useState(EMPTY_CATALOGS)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [inspectorDirty, setInspectorDirty] = useState(false)
  const [presetsDirty, setPresetsDirty] = useState(false)
  const [message, setMessage] = useState('')
  const dirty = inspectorDirty || presetsDirty
  const { open: guardOpen, confirmLeave, settle } = useLeavePrompt()

  const load = useCallback(async (preferredId?: string) => {
    const [nextDefinitions, nextCatalogs] = await Promise.all([
      client.listAgentDefinitions(),
      client.editorCatalogs(),
    ])
    setDefinitions(nextDefinitions)
    setCatalogs({
      providers: nextCatalogs.providers,
      skills: nextCatalogs.skills,
      tools: nextCatalogs.tools,
    })
    setSelectedId(current => preferredId ?? current ?? nextDefinitions[0]?.definition_id ?? null)
  }, [client])

  useEffect(() => {
    setDefinitions([])
    setSelectedId(null)
    setMessage('')
    void load().catch((error: unknown) => {
      setMessage(error instanceof Error ? error.message : '无法读取 Agent 目录')
    })
  }, [load, workspaceId])

  useLeaveGuard(registerGuard, dirty, () => dirty ? confirmLeave() : Promise.resolve(true))

  const selected = definitions.find(item => item.definition_id === selectedId) ?? null

  return (
    <section className="settings-page" aria-label="Agent 预设">
      <div className="settings-content">
        <header className="settings-heading">
          <span className="settings-eyebrow">设置 / Agent 预设</span>
          <h2>Agent 预设与定义</h2>
          <p role="status">
            {workspaceName}
          </p>
        </header>
        {message && <p role="alert" className="text-failed">{message}</p>}
        <AgentPresetsPanel
          client={client}
          workspace={workspaceId}
          connection={connection}
          workspaceName={workspaceName}
          onDirtyChange={setPresetsDirty}
          onOpenDefinition={id => setSelectedId(id)}
        />
        <div className="settings-section-heading">
          <h3>Agent 定义</h3>
          <span>发布、启停与撤销走原定义命令</span>
        </div>
        <label>当前定义
          <select
            className="editor-input"
            aria-label="当前 Agent 定义"
            value={selectedId ?? ''}
            onChange={event => setSelectedId(event.target.value || null)}
          >
            {definitions.length === 0 && <option value="">暂无定义</option>}
            {definitions.map(item => (
              <option key={item.definition_id} value={item.definition_id}>
                {item.source?.name ?? item.definition_id}
              </option>
            ))}
          </select>
        </label>
        <AgentInspector
          client={client}
          definitions={definitions}
          selected={selected}
          providers={catalogs.providers}
          skills={catalogs.skills}
          tools={catalogs.tools}
          onRefresh={async definitionId => { await load(definitionId) }}
          onManageTools={onManageTools}
          onDirtyChange={setInspectorDirty}
        />
      </div>
      <LeaveGuardDialog
        open={guardOpen}
        title="Agent 编辑尚未保存"
        description="离开将丢失未保存的定义或自定义表单。已发布版本不受影响。"
        onDiscard={() => settle(true)}
        onStay={() => settle(false)}
      />
    </section>
  )
}
