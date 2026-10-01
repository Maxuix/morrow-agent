import { useEffect, useRef, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { ComputerUseSettingsView, ComputerWindowCandidates, ComputerWindowSelection } from '../api/settings'

/** Candidate reads are explicit local actions; selection alone does not create a grant. */
export function ComputerWindowPicker({client, workspace, session, view, permission, modelKey,
  disabled = false, selection, onChange}: {
  client: ApiClient; workspace: string; session: string; view: ComputerUseSettingsView
  permission?: string | null; modelKey?: string; disabled?: boolean
  selection: ComputerWindowSelection | null; onChange: (value: ComputerWindowSelection | null) => void
}) {
  const [catalog, setCatalog] = useState<ComputerWindowCandidates | null>(null)
  const [ids, setIds] = useState<string[]>([])
  const [action, setAction] = useState(false)
  const [images, setImages] = useState(false)
  const [delivery, setDelivery] = useState<'' | 'foreground' | 'background'>('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [expired, setExpired] = useState(false)
  const ticket = useRef(0)
  const change = useRef(onChange); change.current = onChange
  useEffect(() => {
    ++ticket.current; setCatalog(null); setIds([]); setAction(false); setImages(false)
    setDelivery(''); setError(''); setBusy(false); change.current(null)
    return () => {++ticket.current}
  }, [client, workspace, session, view.revision, permission, modelKey])
  const deadline = selection?.expires_at ?? catalog?.expires_at
  useEffect(() => {
    const expires = deadline ? Date.parse(deadline) : Infinity
    const check = () => setExpired(expires <= Date.now())
    check()
    const timer = Number.isFinite(expires) ? window.setTimeout(check, Math.max(0, expires - Date.now())) : undefined
    window.addEventListener('focus', check)
    return () => {window.clearTimeout(timer); window.removeEventListener('focus', check)}
  }, [deadline])
  const allowed = view.settings.enabled && permission === 'full-access-manual' && view.model_capabilities.function_tools
  const locked = disabled || busy || !allowed
  const read = async () => {
    const request = ++ticket.current
    change.current(null); setCatalog(null); setIds([]); setError(''); setBusy(true)
    try {
      const next = await client.computerWindowCandidates(workspace, session)
      if (request === ticket.current) setCatalog(next)
    } catch (failure) {
      if (request === ticket.current) setError(failure instanceof Error ? failure.message : '窗口读取失败')
    } finally {if (request === ticket.current) setBusy(false)}
  }
  const select = async () => {
    if (!delivery || !ids.length || locked || expired) return
    const request = ++ticket.current; setBusy(true); setError('')
    try {
      const next = await client.selectComputerWindows(workspace, session, {
        candidate_ids: ids, allow_action: action, share_images: images, delivery,
      })
      if (request === ticket.current) change.current(next)
    } catch (failure) {
      if (request === ticket.current) setError(failure instanceof Error ? failure.message : '窗口选择失败')
    } finally {if (request === ticket.current) setBusy(false)}
  }
  return <div className="permission-live-state">
    <button className="editor-button" disabled={locked} onClick={() => void read()}>读取本地窗口</button>
    {!allowed && <p className="menu-note">选择窗口需要启用桌面功能、支持工具的模型及完整访问（逐次确认）。</p>}
    {selection ? <>
      <p className="menu-note">下次普通对话运行使用：{selection.windows.map(item => item.display_label ?? item.app.bundle_id).join('、')}</p>
      <p className="menu-note">{selection.operations.includes('action') ? '观察与操作' : '仅观察'} · {selection.delivery === 'foreground' ? '前台投递' : '后台投递'} · {selection.image_share === 'none' ? '不分享图像' : '分享受控窗口图像'}</p>
      <button className="editor-button" disabled={disabled || busy} onClick={() => change.current(null)}>清除窗口选择</button>
    </> : catalog && <>
      {!catalog.candidates.length && <p className="menu-note">没有可选择的窗口</p>}
      {catalog.candidates.map(item => <label key={item.candidate_id}>
        <input type="checkbox" disabled={locked || expired} checked={ids.includes(item.candidate_id)}
          onChange={event => setIds(current => event.target.checked ? [...current, item.candidate_id] : current.filter(id => id !== item.candidate_id))}/>
        {item.display_label ?? item.app.bundle_id}
      </label>)}
      <label><input type="checkbox" disabled={locked || expired} checked={action} onChange={event => setAction(event.target.checked)}/> 允许操作选中窗口</label>
      <label><input type="checkbox" disabled={locked || expired || view.settings.mode !== 'hybrid' || !view.model_capabilities.images}
        checked={images} onChange={event => setImages(event.target.checked)}/> 分享受控窗口图像</label>
      <label className="popover-field"><span>投递方式</span>
        <select className="editor-input" aria-label="桌面投递方式" disabled={locked || expired} value={delivery}
          onChange={event => setDelivery(event.target.value as typeof delivery)}>
          <option value="">请选择</option><option value="foreground">前台</option><option value="background">后台</option>
        </select>
      </label>
      <button className="editor-button" disabled={locked || expired || !ids.length || !delivery} onClick={() => void select()}>用于下次运行</button>
    </>}
    {expired && <p role="alert" className="menu-error">窗口选择已过期，请重新读取或清除选择。</p>}
    {error && <p role="alert" className="menu-error">{error}</p>}
    <p className="menu-note">选择有效期约 30 秒，仅用于一次新运行；发送时创建独立桌面授权。</p>
  </div>
}
