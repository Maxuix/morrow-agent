import { useEffect, useRef, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { ComputerUseSettings, ComputerUseSettingsView, ComputerWindowSelection } from '../api/settings'

import { ComputerWindowPicker } from './ComputerWindowPicker'

const hostReasons: Record<string, string> = {
  disabled: '已关闭', sdk_missing: '未安装桌面组件', native_unverified: '原生验收未通过',
  no_interactive_session: '没有交互桌面', tcc_missing: '缺少系统权限',
  image_missing: '无法读取窗口图像', unsupported_os: '系统暂不支持',
}
const modelReasons: Record<string, string> = {
  model_unavailable: '当前模型不可用', function_tools_required: '当前模型不支持所需工具协议',
  images_not_supported: '当前模型不支持图像，请选择语义模式或更换模型',
}

/** Configuration never grants device access; a run needs its own local selection. */
export function ComputerUseControl({client, workspace, session, modelKey, permission, disabled, selection, onSelectionChange}: {
  client: ApiClient; workspace: string; session: string; modelKey?: string
  permission?: string | null; disabled?: boolean; selection?: ComputerWindowSelection | null
  onSelectionChange?: (selection: ComputerWindowSelection | null) => void
}) {
  const [view, setView] = useState<ComputerUseSettingsView | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const ticket = useRef(0)
  const saving = useRef(false)
  useEffect(() => {
    const current = ++ticket.current
    saving.current = false; setBusy(false); setError('')
    const load = () => {
      if (saving.current) return
      const request = ++ticket.current
      void client.computerUseSettings(workspace, session).then(next => {
        if (request === ticket.current) {setView(next); setError('')}
      }).catch(() => {if (request === ticket.current) setError('桌面设置读取失败')})
    }
    // Compatibility with clients used by older embedded surfaces.
    if (typeof client.computerUseSettings !== 'function') return
    setView(null); load()
    window.addEventListener('focus', load)
    return () => {ticket.current = Math.max(ticket.current, current) + 1; window.removeEventListener('focus', load)}
  }, [client, workspace, session, modelKey])
  const save = async (settings: ComputerUseSettings) => {
    if (!view || busy) return
    const request = ++ticket.current
    saving.current = true; setBusy(true); setError('')
    try {
      const next = await client.saveComputerUseSettings(workspace, session, settings, view.revision)
      if (request === ticket.current) setView(next)
    } catch (failure) {
      if (request === ticket.current) {
        setError(failure instanceof Error ? failure.message : '桌面设置保存失败')
        try {
          const next = await client.computerUseSettings(workspace, session)
          if (request === ticket.current) setView(next)
        } catch { /* Keep the error and allow a focus refresh. */ }
      }
    } finally {if (request === ticket.current) {saving.current = false; setBusy(false)}}
  }
  if (!view && !error) return null
  return <div className="permission-live-state">
    <div className="permission-section-heading">桌面操作</div>
    {view && <>
      <label><input type="checkbox" checked={view.settings.enabled} disabled={busy}
        onChange={event => void save({...view.settings, enabled: event.target.checked})}/> 启用桌面功能（全局）</label>
      <label className="popover-field"><span>观察方式</span>
        <select className="editor-input" aria-label="桌面观察方式" value={view.settings.mode} disabled={busy}
          onChange={event => void save({...view.settings, mode: event.target.value as ComputerUseSettings['mode']})}>
          <option value="semantic">语义树</option><option value="hybrid">语义树与窗口图像</option>
        </select>
      </label>
      <p className="menu-note" role="status">宿主：{hostReasons[view.host.reason] ?? '桌面暂不可用'}</p>
      {view.model_error && <p className="menu-note">{modelReasons[view.model_error]}</p>}
      <p className="menu-note">更改对后续运行生效。启用后仍需完整访问（逐次确认）及本次运行的独立桌面授权；现有运行请使用停止或撤销。</p>
    </>}
    {view && onSelectionChange && <ComputerWindowPicker client={client} workspace={workspace} session={session}
      view={view} permission={permission} modelKey={modelKey} disabled={disabled || busy}
      selection={selection ?? null} onChange={onSelectionChange}/>}
    {error && <p role="alert" className="menu-error">{error}</p>}
  </div>
}
