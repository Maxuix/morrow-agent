import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import type { ApiClient } from '../api/client'
import type { ProviderSettingsView } from '../api/settings'
import type { DirtyGuard } from '../state/navigation'
import { LeaveGuardDialog, useLeaveGuard, useLeavePrompt } from './management/LeaveGuard'

const field = 'editor-input'
const button = 'editor-button'

export function ProviderSettings({
  client,
  registerGuard,
  workspaceDefaults,
}: {
  client: ApiClient
  registerGuard?: (guard: DirtyGuard) => () => void
  workspaceDefaults?: ReactNode
}) {
  const [value, setValue] = useState<ProviderSettingsView | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [dirty, setDirty] = useState(false)
  const { open: guardOpen, confirmLeave, settle } = useLeavePrompt()
  useLeaveGuard(registerGuard, dirty, () => dirty ? confirmLeave() : Promise.resolve(true))
  const refresh = useCallback(() => client.providerSettings().then(setValue), [client])
  useEffect(() => { let current = true; client.providerSettings().then(v => { if (current) setValue(v) }).catch(() => { if (current) setError('无法读取模型设置') }); return () => {current = false} }, [client])
  async function mutate(action: string, body: Record<string, unknown>, provider?: string) {
    if (!value) return false
    setBusy(true); setError(''); setNotice('')
    try {
      const next = await client.providerControl(action, {...body, expected_revision: value.revision}, provider)
      setValue(next); setDirty(false); setNotice(action === 'test' ? '连接测试已完成。' : '设置已保存。'); return true
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败'); await refresh().catch(() => {}); return false
    } finally { setBusy(false) }
  }
  async function credential(provider: string, secret: string) {
    if (!value) return
    setBusy(true); setError(''); setNotice('')
    try { await client.providerCredential(provider, secret, value.revision); await refresh(); setDirty(false); setNotice('凭据已保存。') }
    catch (e) { setError(e instanceof Error ? e.message : '凭据保存失败'); await refresh().catch(() => {}) }
    finally { setBusy(false) }
  }
  return <section className="settings-page" aria-label="模型设置">
    <div className="settings-content">
      <header className="settings-heading"><span className="settings-eyebrow">设置 / 模型</span><h2>Provider 与模型</h2></header>
      {error && <p role="alert" className="text-failed">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {workspaceDefaults}
      {!value ? <button className={button} onClick={() => void refresh().catch(() => setError('无法读取模型设置'))}>重新读取设置</button> : <>
        <p className="default-model-banner"><span>全局默认</span> {value.active_model ? `${value.active_model.provider_id} / ${value.active_model.model_id}` : '尚未选择'}</p>
        <details className="provider-add" open={value.providers.length === 0 ? true : undefined}><summary>添加 Provider<span>预设服务或自定义接口</span></summary><fieldset disabled={busy}>
          <div className="flex flex-wrap gap-2">{value.presets.map(p => <button key={p.preset_id} className={button} onClick={() => void mutate('presets', {preset_id: p.preset_id})}>添加预设 {p.preset_id}</button>)}</div>
          <form className="provider-create-form" onSubmit={e => {
            e.preventDefault(); const form = e.currentTarget; const data = new FormData(form)
            void mutate('', {provider_id: data.get('provider'), adapter_id: data.get('adapter'), base_url: data.get('url')}).then(ok => {if (ok) { form.reset(); setDirty(false) }})
          }}>
            <label>Provider ID<input className={field} name="provider" aria-label="Provider ID" placeholder="例如 my-provider" required maxLength={64} onChange={() => setDirty(true)} /></label>
            <label>接口类型<select className={field} name="adapter" aria-label="接口类型">{value.adapters.map(a => <option key={a.adapter_id}>{a.adapter_id}</option>)}</select></label>
            <label className="provider-url-field">服务地址<input className={field} name="url" type="url" aria-label="服务地址" placeholder="https://…/v1" required onChange={() => setDirty(true)} /></label>
            <button className={button} type="submit">添加自定义 Provider</button>
          </form>
        </fieldset></details>
        <div className="settings-section-heading"><h3>已连接的服务</h3><span>{value.providers.length} 个 Provider</span></div>
        {value.providers.length === 0 && <p>暂无模型服务</p>}
        {value.providers.map(p => <ProviderCard key={p.provider_id} provider={p} value={value} busy={busy} mutate={mutate} credential={credential} onDirty={() => setDirty(true)} />)}
      </>}
    </div>
    <LeaveGuardDialog
      open={guardOpen}
      title="Provider 表单有未保存内容"
      description="离开将丢失未保存的配置和凭据。"
      onDiscard={() => { setDirty(false); settle(true) }}
      onStay={() => settle(false)}
    />
  </section>
}

export function ProviderCard({provider: p, value, busy, mutate, credential, onDirty}: {
  provider: ProviderSettingsView['providers'][number]; value: ProviderSettingsView; busy: boolean
  mutate: (action: string, body: Record<string, unknown>, provider?: string) => Promise<boolean>
  credential: (provider: string, secret: string) => Promise<void>
  onDirty?: () => void
}) {
  const [secret, setSecret] = useState('')
  const [remove, setRemove] = useState(false)
  const efforts = value.adapters.find(a => a.adapter_id === p.adapter)?.reasoning_efforts ?? []
  const active = value.active_model?.provider_id === p.provider_id
  function saveCredential(e: FormEvent) {
    e.preventDefault(); const input = secret; setSecret(''); void credential(p.provider_id, input)
  }
  return <fieldset disabled={busy} className="provider-card" aria-label={p.provider_id}>
    <div className="provider-card-heading"><div><h3>{p.provider_id}</h3><p>{p.adapter}</p></div><span className={`provider-badge ${p.credential_configured ? 'is-ready' : ''}`}>{p.credential_configured ? '凭据已配置' : '待配置凭据'}</span></div>
    <section className="provider-connection" aria-label={`${p.provider_id} 连接配置`}>
    <form className="provider-field-row" key={p.base_url} onSubmit={e => {e.preventDefault(); void mutate('configure', {base_url: new FormData(e.currentTarget).get('url')}, p.provider_id)}}>
      <label>服务地址<input className={field} name="url" aria-label={`${p.provider_id} 服务地址`} defaultValue={p.base_url} type="url" required onChange={() => onDirty?.()} /></label>
      <button className={button}>保存地址</button>
    </form>
    <form className="provider-field-row" onSubmit={saveCredential}>
      <label>API 凭据<input className={field} aria-label={`${p.provider_id} 凭据`} placeholder={p.credential_configured ? '输入新凭据以替换已保存的凭据' : '输入 API Key'} type="password" autoComplete="new-password" value={secret} onChange={e => { setSecret(e.target.value); if (e.target.value) onDirty?.() }} required maxLength={16384} /></label>
      <button className={button}>保存凭据</button>
    </form>
    <div className="provider-actions">
      <button className={button} disabled={!p.credential_configured || !p.models.length} onClick={() => void mutate('test', {}, p.provider_id)}>测试连接</button>
      <button className={button} disabled={!p.credential_configured || !value.adapters.find(a => a.adapter_id === p.adapter)?.discovery} onClick={() => void mutate('discover', {}, p.provider_id)}>发现模型</button>
      <button className={button} disabled={active} onClick={() => setRemove(true)}>移除 Provider</button>
      {remove && <span>移除 {p.provider_id}？ <button className={button} onClick={() => void mutate('remove', {}, p.provider_id)}>确认移除</button> <button className={button} onClick={() => setRemove(false)}>取消</button></span>}
    </div>
    <p className="provider-test-note">{p.last_test ? `最近测试：${p.last_test.ok ? '连接成功' : '失败，请检查服务和凭据'}` : '尚未测试连接'}</p>
    </section>
    <div className="settings-section-heading provider-model-heading"><h4>模型</h4><span>{p.models.length} 个可用配置</span></div>
    <ul className="provider-model-list">{p.models.map(m => {
      const selected = active && value.active_model?.model_id === m.model_id
      return <li key={m.model_id} className="provider-model">
        <div className="provider-model-identity"><strong>{m.model_id}</strong><span>{m.model_id} → {m.api_model_id}</span>{selected && <small className="provider-badge">全局默认</small>}</div><div className="provider-model-actions">
        <button className={button} disabled={selected} onClick={() => void mutate('model', {action: 'use', model_id: m.model_id}, p.provider_id)}>设为全局默认</button>
        <button className={button} disabled={selected} onClick={() => void mutate('model', {action: 'remove', model_id: m.model_id}, p.provider_id)} aria-label={`移除模型 ${m.model_id}`}>移除</button>
        </div><details className="model-capabilities"><summary>配置模型映射与能力</summary><form className="model-capability-form" onSubmit={event => {
          event.preventDefault(); const data = new FormData(event.currentTarget)
          void mutate('configure-model', {model_id:m.model_id, api_model_id:data.get('api'), capabilities:{...m.capabilities,reasoning_efforts:data.getAll('reasoning'),input_types:data.get('images')?['text','image']:['text']}}, p.provider_id)
        }}><label>API 模型 ID<input className={field} name="api" aria-label={`${m.model_id} API 映射`} defaultValue={m.api_model_id} required /></label><div className="capability-choices">
          {efforts.map(e => <label key={e}><input type="checkbox" name="reasoning" value={e} defaultChecked={((m.capabilities?.reasoning_efforts ?? []) as string[]).includes(e)} /> {e}</label>)}
          </div><label className="capability-checkbox"><input type="checkbox" name="images" defaultChecked={((m.capabilities?.input_types??[]) as string[]).includes("image")}/> 此模型支持图像输入（按服务文档确认）</label>
          <button className={button}>保存模型配置</button>
        </form></details>
      </li>
    })}</ul>
    <details className="provider-add-model"><summary>添加模型</summary><form className="provider-model-form" onSubmit={e => {
      e.preventDefault(); const form = e.currentTarget; const data = new FormData(form)
      void mutate('models', {model_id: data.get('model'), api_model_id: data.get('api') || null, capabilities: {reasoning_efforts: data.getAll('reasoning'),input_types:data.get('images')?['text','image']:['text']}}, p.provider_id).then(ok => {if (ok) form.reset()})
    }}>
      <label>模型名称<input className={field} name="model" aria-label={`${p.provider_id} 模型名称`} placeholder="例如 deepseek-v4-flash" required maxLength={128} /></label>
      <label>API 模型 ID<input className={field} name="api" aria-label={`${p.provider_id} API 模型 ID`} placeholder="可选，默认使用模型名称" maxLength={256} /></label>
      {efforts.length > 0 && <fieldset className="capability-choices"><legend>支持的思考选项 · 按服务文档确认</legend>{efforts.map(e => <label key={e} className="text-xs"><input type="checkbox" name="reasoning" value={e} /> {e}</label>)}</fieldset>}
      <label className="capability-checkbox"><input type="checkbox" name="images"/> 此模型支持图像输入（按服务文档确认）</label>
      <button className={button}>添加模型</button>
    </form></details>
  </fieldset>
}
