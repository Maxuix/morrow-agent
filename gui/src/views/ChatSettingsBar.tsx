import { useCallback, useEffect, useRef, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { ChatPermissionsView, ChatSettings, ChatSettingsView, ProviderSettingsView } from '../api/settings'
import type { ModelRefWire } from '../api/types'
import { approvalScopeLabel, notifyPermissionChanged } from '../state/approvalDecision'
import { commandId } from './lib/editor'
import { useDismissablePopover } from './lib/popover'

/** Everything the composer's permission and model controls need. */
export interface ChatSettingsState {
  value: ChatSettingsView | null
  catalog: ProviderSettingsView | null
  busy: boolean; error: string; ready: boolean; active: boolean
  model: ModelRefWire | null
  efforts: string[]
  effort: string
  permission: ChatSettings['permission'] | null
  permissionPresets: ChatSettingsView['permission_presets']
  save: (settings: ChatSettings, scope?: 'session' | 'workspace' | 'global') => Promise<void>
}

/**
 * Loads and saves chat settings for one session; the composer renders the
 * result through `ChatPermissionControl` and `ChatModelControl`, so the old
 * inline select row disappears into two popovers.
 */
export function useChatSettings({client, workspace, session, active, snapshot, onReady, onImages, onPermission}: {
  client: ApiClient; workspace: string; session: string; active: boolean
  snapshot?: ChatSettingsView
  onReady?: (ready: boolean) => void
  onImages?: (supported: boolean) => void
  onPermission: (permission: ChatSettings['permission'] | null) => void
}): ChatSettingsState {
  const [value, setValue] = useState<ChatSettingsView | null>(null)
  const [catalog, setCatalog] = useState<ProviderSettingsView | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const alive = useRef(true)
  const generation = useRef(0)
  const load = useCallback(async () => {
    const ticket = ++generation.current
    const [v, c] = await Promise.all([client.chatSettings(workspace, session), client.providerSettings()])
    if (alive.current && ticket === generation.current) {setValue(v); setCatalog(c)}
  }, [client, workspace, session])
  useEffect(() => {alive.current = true; void load().catch(() => setError('无法读取会话设置')); const reload = () => void load().catch(() => {}); window.addEventListener('focus', reload); return () => {alive.current = false; window.removeEventListener('focus', reload)} }, [load])
  useEffect(() => {
    if (snapshot) void load().catch(() => {})
  }, [snapshot?.documents.global.revision, snapshot?.documents.workspace.revision, snapshot?.documents.session.revision, load])
  const model = value?.effective.model ?? null
  const selected = catalog?.providers.find(p => p.provider_id === model?.provider_id)
  const selectedModel = selected?.models.find(m => m.model_id === model?.model_id)
  const efforts = selectedModel?.effective_capabilities?.reasoning_efforts ?? []
  const images = selectedModel?.effective_capabilities?.input_types.includes('image') ?? false
  useEffect(() => onImages?.(images), [images, onImages])
  const effort = value?.effective.generation?.reasoning_effort ?? ''
  const permission = value?.effective.permission ?? null
  const permissionPresets = value?.permission_presets ?? []
  const permissionAvailable = permissionPresets.some(p => p.preset === permission && p.available)
  const ready = permissionAvailable && (!effort || efforts.includes(effort)) && !!selected?.credential_configured && !!selectedModel
  useEffect(() => onReady?.(ready && !busy), [ready, busy, onReady])
  useEffect(() => onPermission(permission), [permission, onPermission])
  const save = useCallback(async (settings: ChatSettings, scope: 'session' | 'workspace' | 'global' = 'session') => {
    if (!value) return
    generation.current++; setBusy(true); setError('')
    try {
      const next = await client.saveChatSettings(workspace, session, scope, settings, value.documents[scope].revision)
      if (alive.current) setValue(next)
    } catch (e) {if (alive.current) setError(e instanceof Error ? e.message : '设置保存失败'); await load().catch(() => {})}
    finally {if (alive.current) setBusy(false)}
  }, [client, workspace, session, value, load])
  return {value, catalog, busy, error, ready, active, model, efforts, effort, permission, permissionPresets, save}
}

/** 常见思考程度的短标签；未声明支持时入口整体隐藏。 */
export function effortLabel(effort: string): string {
  return ({minimal: '最低', low: '低', medium: '中', high: '高'} as Record<string, string>)[effort] ?? effort
}

const ShieldIcon = () => <svg width="13" height="13" viewBox="0 0 14 14" fill="none" aria-hidden="true"><path d="M7 1.5 12 3.4v3.2c0 2.9-2.05 5.2-5 6-2.95-.8-5-3.1-5-6V3.4L7 1.5Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/></svg>
const CaretIcon = () => <svg width="9" height="9" viewBox="0 0 10 10" fill="none" aria-hidden="true"><path d="m2 3.5 3 3 3-3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/></svg>

/** Toolbar-left permission entry plus the current run/session authorization controls. */
export function ChatPermissionControl({
  settings,
  client,
  workspace,
  session,
  focusSignal = 0,
  hostAllowed = false,
  onHostAllowedChange,
}: {
  settings: ChatSettingsState
  client?: ApiClient
  workspace?: string
  session?: string
  focusSignal?: number
  hostAllowed?: boolean
  onHostAllowedChange?: (allowed: boolean) => void
}) {
  const menu = useDismissablePopover()
  const current = settings.permissionPresets.find(p => p.preset === settings.permission)
  const [permissions, setPermissions] = useState<ChatPermissionsView | null>(null)
  const [permissionError, setPermissionError] = useState('')
  const [permissionBusy, setPermissionBusy] = useState<string | null>(null)
  const loadTicket = useRef(0)
  const revokeCommands = useRef(new Map<string, string>())
  const canLoad = client !== undefined && !!workspace && !!session
  const loadPermissions = useCallback(async () => {
    if (!client || !workspace || !session) return
    const ticket = ++loadTicket.current
    try {
      const view = await client.chatPermissions(workspace, session)
      if (ticket === loadTicket.current) {
        setPermissions(view)
        setPermissionError('')
      }
    } catch {
      if (ticket === loadTicket.current) setPermissionError('授权状态读取失败，请重试。')
    }
  }, [client, session, workspace])
  useEffect(() => {
    if (!canLoad) return
    const reload = () => void loadPermissions()
    window.addEventListener('morrow:permission-changed', reload)
    if (focusSignal > 0) {
      if (menu.current) {
        menu.current.open = true
        menu.current.querySelector<HTMLElement>('input, button, select')?.focus()
      }
      reload()
    }
    return () => window.removeEventListener('morrow:permission-changed', reload)
  }, [canLoad, focusSignal, loadPermissions, menu])
  const revoke = async (kind: 'grant' | 'session_scope', subjectId: string, revision: number) => {
    if (!client || !workspace || !session || permissionBusy !== null) return
    const key = `${kind}:${subjectId}`
    const stableCommand = revokeCommands.current.get(key) ?? commandId(`revoke_${kind}`)
    revokeCommands.current.set(key, stableCommand)
    setPermissionBusy(key); setPermissionError('')
    try {
      await client.revokeChatPermission(workspace, session, {
        command_id: stableCommand, kind, subject_id: subjectId, expected_revision: revision,
      })
      notifyPermissionChanged()
      await loadPermissions()
    } catch (error) {
      if (typeof error === 'object' && error !== null && 'status' in error && (error as {status?: number}).status === 409) {
        await loadPermissions()
      } else {
        setPermissionError('撤销结果暂时未知；可再次点击使用同一请求核对。')
      }
    } finally {
      setPermissionBusy(null)
    }
  }
  const activeGrants = permissions?.grants.filter(grant => grant.status === 'active') ?? []
  const scopes = permissions?.session_scopes.items ?? []
  return <details ref={menu} className="composer-permission" onToggle={event => {if ((event.currentTarget as HTMLDetailsElement).open) void loadPermissions()}}>
    <summary className="editor-button" aria-label={`权限：${current?.label ?? '默认'}`} title="会话权限预设与授权">
      <ShieldIcon/><span className="permission-label">{current?.label ?? '权限'}</span><CaretIcon/>
    </summary>
    <div className="composer-popover composer-permission-popover">
      {settings.permissionPresets.map(p => <button key={p.preset} className={`composer-menu-item${p.preset === settings.permission ? ' is-active' : ''}`}
        disabled={settings.busy || !p.available} aria-pressed={p.preset === settings.permission}
        onClick={() => {if (settings.value) void settings.save({...settings.value.documents.session.settings, permission: p.preset})}}>
        <span>{p.label}{p.preset === settings.permission ? ' ✓' : ''}</span>
        {p.reason && <span className="menu-note">{p.reason}</span>}
      </button>)}
      {settings.permission === 'full-access-manual' && <div className="permission-host-control">
        <label><input type="checkbox" checked={hostAllowed} disabled={settings.busy}
          onChange={event => onHostAllowedChange?.(event.target.checked)}/> 允许本次运行使用 Host</label>
        <p className="menu-note">最长 15 分钟；Host 可访问用户文件、网络和凭据，每条命令仍需单独审批。</p>
      </div>}
      {canLoad && <div className="permission-live-state">
        <div className="permission-section-heading">当前运行授权</div>
        {activeGrants.length === 0 && <p className="menu-note">暂无活动 Host 授权。</p>}
        {activeGrants.map(grant => <div className="permission-receipt" key={grant.grant_id}>
          <span>{grant.capabilities.join('、')} · 到期 {new Date(grant.expires_at).toLocaleString()}</span>
          <button type="button" className="exec-link" disabled={permissionBusy !== null}
            onClick={() => void revoke('grant', grant.grant_id, grant.row_version)}>撤销此授权</button>
        </div>)}
        <div className="permission-section-heading">本会话范围授权</div>
        {scopes.length === 0 && <p className="menu-note">暂无会话范围授权。</p>}
        {scopes.map(scope => <div className="permission-receipt" key={`${scope.scope}:${scope.approval_id}`}>
          <span>{approvalScopeLabel(scope.scope)}</span>
          <button type="button" className="exec-link" disabled={permissionBusy !== null}
            onClick={() => void revoke('session_scope', scope.approval_id, scope.revision)}>撤销此授权</button>
        </div>)}
        {permissionError && <p role="alert" className="menu-error">{permissionError}</p>}
      </div>}
    </div>
  </details>
}

/** Toolbar-right entry: session model and thinking effort. Provider catalog
 * edits live in settings; models without declared efforts hide the selector. */
export function ChatModelControl({settings, onOpenSettings}: {settings: ChatSettingsState; onOpenSettings: () => void}) {
  const menu = useDismissablePopover()
  const {value, catalog, busy, ready} = settings
  const selected = catalog?.providers.find(p => p.provider_id === settings.model?.provider_id)
  const showEffort = settings.efforts.length > 0
  const scopeLabel = ({session: '当前会话', workspace: '工作区', global: '全局', adapter: '接口默认'} as Record<string, string>)[settings.value?.sources.model.scope ?? ''] ?? settings.value?.sources.model.scope
  return <details ref={menu} className="composer-model">
    <summary className="editor-button" aria-label="模型与思考程度" title="模型与思考程度">
      {!ready && value && <span className="model-warning-dot" aria-label="模型配置不可用"/>}
      <span className="model-name">{settings.model ? settings.model.model_id : '选择模型'}</span>
      {showEffort && <span className="model-effort">· {settings.effort ? effortLabel(settings.effort) : '默认'}</span>}
      <CaretIcon/>
    </summary>
    <div className="composer-popover composer-model-popover">
      <label className="popover-field"><span>模型</span>
        <select className="editor-input" aria-label="会话模型" disabled={busy || !value} value={settings.model ? JSON.stringify(settings.model) : ''} onChange={e => {if (value) void settings.save({...value.documents.session.settings, model: JSON.parse(e.target.value), generation: {}})}}>
          <option value="" disabled>选择模型</option>
          {settings.model && !selected?.models.some(m => m.model_id === settings.model!.model_id) && <option value={JSON.stringify(settings.model)} disabled>已不可用：{settings.model.provider_id}/{settings.model.model_id}</option>}
          {catalog?.providers.map(p => <optgroup key={p.provider_id} label={p.provider_id}>{p.models.map(m => <option key={m.model_id} value={JSON.stringify({provider_id: p.provider_id, model_id: m.model_id})} disabled={!p.credential_configured}>{m.model_id}{p.credential_configured ? '' : '（缺少凭据）'}</option>)}</optgroup>)}
        </select>
      </label>
      {showEffort && <label className="popover-field"><span>思考程度</span>
        <select className="editor-input" aria-label="思考程度" disabled={busy || !value} value={settings.effort} onChange={e => {if (value) void settings.save({...value.documents.session.settings, generation: {reasoning_effort: e.target.value || null}})}}>
          <option value="">接口默认</option>
          {settings.effort && !settings.efforts.includes(settings.effort) && <option value={settings.effort} disabled>已不可用：{settings.effort}</option>}
          {settings.efforts.map(e => <option key={e} value={e}>{effortLabel(e)}</option>)}
        </select>
      </label>}
      <div className="popover-secondary">
        <button className="editor-button" onClick={onOpenSettings}>打开 Provider 设置</button>
        {value && (
          <button className="editor-button" disabled={busy} onClick={() => void settings.save({model: null, generation: null, permission: null})}>使用继承设置</button>
        )}
      </div>
      {value && <p className="text-xs text-secondary">模型来源：{scopeLabel}{settings.active && ' · 更改下次运行生效；已提交输入保留原设置'}</p>}
      {settings.error && <p role="alert" className="text-xs text-failed">{settings.error}</p>}
    </div>
  </details>
}
