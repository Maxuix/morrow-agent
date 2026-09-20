import { useState } from 'react'
import type { ApiClient } from '../api/client'
import { commandId } from './lib/editor'
import { safeUiText } from './editor/diagnostics'

export function DefinitionLifecycle({
  client,
  kind,
  id,
  revision,
  enabled,
  version,
  readOnly = false,
  onRefresh,
}: {
  client: ApiClient
  kind: 'agent' | 'workflow'
  id: string
  revision: number
  enabled: boolean
  version: string | null
  readOnly?: boolean
  onRefresh: () => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [validation, setValidation] = useState<string[]>([])
  const [revoking, setRevoking] = useState(false)
  const [reason, setReason] = useState('')
  const [exact, setExact] = useState(version ?? '')

  async function act(action: string) {
    setBusy(true)
    setMessage('')
    setValidation([])
    try {
      const value = await client.definitionAction(kind, id, {
        command_id: commandId(`definition_${action}`),
        action,
        expected_head_revision: revision,
        version_id: action === 'revoke' ? exact : null,
        reason: reason || 'User requested definition revocation',
      })
      if (action === 'validate') {
        const diagnostics = Array.isArray(value.diagnostics)
          ? value.diagnostics.map(item => typeof item === 'string'
            ? safeUiText(item, 'Core 未提供进一步说明。')
            : typeof item === 'object' && item !== null && 'message' in item
              ? safeUiText((item as { message: unknown }).message, 'Core 未提供进一步说明。')
              : safeUiText(item, 'Core 未提供进一步说明。'))
          : []
        setValidation(diagnostics)
        setMessage(diagnostics.length === 0 ? '校验通过。' : '校验完成，请处理下方问题。')
      } else {
        setMessage(action === 'publish' ? '定义版本已发布。' : '操作已完成。')
        await onRefresh()
      }
      setRevoking(false)
    } catch (error: unknown) {
      setMessage(safeUiText(error instanceof Error ? error.message : error, '定义操作失败。'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="flex flex-wrap items-center gap-2" aria-label="定义生命周期">
      <button type="button" className="editor-button" disabled={busy} onClick={() => void act('validate')}>校验定义</button>
      <button type="button" className="editor-button" disabled={busy || readOnly} onClick={() => void act('publish')}>发布定义</button>
      <button type="button" className="editor-button" disabled={busy || readOnly || revision === 0} onClick={() => void act(enabled ? 'disable' : 'enable')}>
        {enabled ? '停用定义' : '启用定义'}
      </button>
      <button type="button" className="editor-button" disabled={busy || readOnly || !version} onClick={() => { setExact(version ?? ''); setRevoking(true) }}>撤销版本</button>
      {readOnly && <p className="w-full text-xs text-secondary">只读</p>}
      {revoking && !readOnly && <div className="w-full rounded-lg border border-subtle p-3">
        <p className="text-xs text-secondary">新任务将无法使用此版本，历史保留。</p>
        <label className="mt-2 block text-xs text-secondary">版本 ID<input aria-label="撤销版本 ID" className="editor-input mt-1" value={exact} onChange={event => setExact(event.target.value)} /></label>
        <label className="mt-2 block text-xs text-secondary">撤销原因<input aria-label="撤销原因" className="editor-input mt-1" value={reason} onChange={event => setReason(event.target.value)} maxLength={512} /></label>
        <div className="mt-2 flex flex-wrap gap-2"><button type="button" className="editor-button" disabled={busy || !reason.trim() || !exact} onClick={() => void act('revoke')}>确认撤销版本</button><button type="button" className="editor-button" onClick={() => setRevoking(false)}>返回</button></div>
      </div>}
      {message && <p role="status" className="w-full text-xs text-secondary">{message}</p>}
      {validation.length > 0 && <ul className="w-full list-disc pl-5 text-xs text-failed">{validation.map((item, index) => <li key={`${item}:${index}`}>{item}</li>)}</ul>}
    </section>
  )
}
