import { useState } from 'react'
import type { Preference, Scope } from '../../api/management'
import { PREFERENCE_STATEMENT_MAX } from '../../state/preferences'

export interface BatchOperation {
  operation: string
  preference_id?: string
  statement?: string
}

export const BATCH_LIMIT = 8
export const BATCH_OPERATIONS = ['add', 'replace', 'enable', 'disable', 'remove'] as const
export const BATCH_LABELS: Record<string, string> = {
  add: '新增',
  replace: '替换',
  enable: '启用',
  disable: '停用',
  remove: '删除',
}

/** Ordered same-scope batch; every item is validated before anything is sent. */
export function batchPreview(rows: BatchOperation[]): string {
  return rows
    .map(row => `${BATCH_LABELS[row.operation] ?? row.operation}${row.statement ? `：${row.statement}` : ''}`)
    .join('；')
}

export function batchInvalid(rows: BatchOperation[]): boolean {
  return rows.some(row => {
    if (row.operation !== 'add' && !row.preference_id) return true
    return ['add', 'replace'].includes(row.operation) && !row.statement?.trim()
  })
}

/** Strict wire shape: add never carries a target id; lifecycle ops never carry text. */
export function batchPayload(rows: BatchOperation[]): BatchOperation[] {
  return rows.map(row => ({
    operation: row.operation,
    ...(row.operation === 'add' ? {} : { preference_id: row.preference_id ?? '' }),
    ...(['add', 'replace'].includes(row.operation) ? { statement: row.statement ?? '' } : {}),
  }))
}

export function BatchPanel({
  scope,
  revision,
  entries,
  submit,
}: {
  scope: Scope
  revision: number
  entries: Preference[]
  submit: (operations: BatchOperation[], expectedRevision: number) => Promise<boolean>
}) {
  const blank = (): BatchOperation => ({ operation: 'add', preference_id: '', statement: '' })
  const [rows, setRows] = useState<BatchOperation[]>([blank()])
  const [base, setBase] = useState<number | null>(null)
  const [message, setMessage] = useState('')
  const edit = (index: number, patch: Partial<BatchOperation>) => {
    setBase(value => value ?? revision)
    setRows(current => current.map((row, position) => (position === index ? { ...row, ...patch } : row)))
    setMessage('')
  }
  const send = async () => {
    const ok = await submit(batchPayload(rows), base ?? revision).catch(() => false)
    setMessage(ok ? '' : '批量提交失败；没有写入任何一项。')
    if (ok) { setRows([blank()]); setBase(null) }
  }
  return (
    <div className="pp-batch">
      <p className="pp-hint">
        {scope === 'global' ? '全局（影响所有工作区）' : '当前工作区'}
      </p>
      {rows.map((row, index) => (
        <div key={index} className="pp-batch-row">
          <select
            className="pp-input"
            aria-label={`第 ${index + 1} 项操作`}
            value={row.operation}
            onChange={event => edit(index, { operation: event.target.value })}
          >
            {BATCH_OPERATIONS.map(value => (
              <option key={value} value={value}>{BATCH_LABELS[value]}</option>
            ))}
          </select>
          {row.operation !== 'add' && (
            <select
              className="pp-input"
              aria-label={`第 ${index + 1} 项目标`}
              value={row.preference_id ?? ''}
              onChange={event => edit(index, { preference_id: event.target.value })}
            >
              <option value="">选择记录</option>
              {entries.filter(item => item.status !== 'deleted').map(item => (
                <option key={item.preference_id} value={item.preference_id}>{item.statement}</option>
              ))}
            </select>
          )}
          {['add', 'replace'].includes(row.operation) && (
            <input
              className="pp-input"
              aria-label={`第 ${index + 1} 项正文`}
              value={row.statement ?? ''}
              maxLength={PREFERENCE_STATEMENT_MAX}
              onChange={event => edit(index, { statement: event.target.value })}
            />
          )}
          <button
            type="button"
            className="pp-button"
            disabled={rows.length === 1}
            onClick={() => { setRows(current => current.filter((_, position) => position !== index)); setBase(value => value ?? revision) }}
          >
            移除
          </button>
        </div>
      ))}
      <div className="pp-dialog-actions">
        <button
          type="button"
          className="pp-button"
          disabled={rows.length >= BATCH_LIMIT}
          onClick={() => { setRows(current => [...current, blank()]); setBase(value => value ?? revision) }}
        >
          添加变更
        </button>
        <button type="button" className="pp-button pp-primary" disabled={batchInvalid(rows)} onClick={() => void send()}>
          确认提交全部变更
        </button>
      </div>
      <p className="pp-hint" role="status">预览：{batchPreview(rows)}</p>
      {message && <p className="pp-error" role="alert">{message}</p>}
    </div>
  )
}
