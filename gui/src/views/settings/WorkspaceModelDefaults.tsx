import { useChatSettings } from '../ChatSettingsBar'

/**
 * Project-level chat model default, written through the existing chat-settings
 * workspace document. A session is only the API vehicle; frozen runs keep their
 * original settings. Without an open session the card explains how to proceed.
 */
export function WorkspaceModelDefaults({
  client,
  workspaceId,
  sessionId,
  workspaceName,
}: {
  client: import('../../api/client').ApiClient
  workspaceId: string
  sessionId: string | null
  workspaceName: string
}) {
  if (sessionId === null) {
    return (
      <section className="workspace-model-defaults" aria-label="项目默认模型">
        <p className="default-model-banner">
          <span>项目默认</span>
          请先打开对话，再设置项目默认模型。
        </p>
      </section>
    )
  }
  return (
    <WorkspaceModelDefaultsForm
      client={client}
      workspaceId={workspaceId}
      sessionId={sessionId}
      workspaceName={workspaceName}
    />
  )
}

function WorkspaceModelDefaultsForm({
  client,
  workspaceId,
  sessionId,
  workspaceName,
}: {
  client: import('../../api/client').ApiClient
  workspaceId: string
  sessionId: string
  workspaceName: string
}) {
  const settings = useChatSettings({
    client,
    workspace: workspaceId,
    session: sessionId,
    active: false,
    onPermission: () => {},
  })
  const workspaceModel = settings.value?.documents.workspace.settings.model
  const globalModel = settings.value?.documents.global.settings.model
  return (
    <section className="workspace-model-defaults" aria-label="项目默认模型">
      <p className="default-model-banner">
        <span>项目 {workspaceName}</span>
        对话默认模型：{workspaceModel ? `${workspaceModel.provider_id} / ${workspaceModel.model_id}` : '继承全局'}
        {globalModel ? ` · 全局对话默认 ${globalModel.provider_id} / ${globalModel.model_id}` : ''}
      </p>
      {settings.value && (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="editor-button"
            disabled={settings.busy || !settings.ready}
            onClick={() => void settings.save(settings.value!.effective, 'workspace')}
          >
            将当前生效设置设为项目默认
          </button>
          <button
            type="button"
            className="editor-button"
            disabled={settings.busy || !settings.ready}
            onClick={() => void settings.save(settings.value!.effective, 'global')}
          >
            将当前生效设置设为全局对话默认
          </button>
        </div>
      )}
      <p className="text-xs text-secondary">用于新对话。</p>
      {settings.error && <p role="alert" className="text-xs text-failed">{settings.error}</p>}
    </section>
  )
}
