import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ApiClient } from '../../api/client'
import type { Scope } from '../../api/management'
import type { DirtyGuard, KnowledgeFocus } from '../../state/navigation'
import { LeaveGuardDialog, useLeaveGuard, useLeavePrompt } from '../management/LeaveGuard'
import {
  buildIntent,
  collectRows,
  filterRows,
  rowCounts,
  rowKey,
  type PreferenceIntent,
  usePreferenceDocuments,
  type PreferenceDocumentState,
  type PreferenceDocuments,
  type PreferenceRow,
  type PreferenceSourceFilter,
  type PreferenceStatusFilter,
  type RowWriteState,
} from '../../state/preferences'
import { Facts } from '../management/components'
import { PreferenceBatch } from '../PreferenceBatch'
import { PreferenceEditorDialog } from './PreferenceEditorDialog'

type Documents = PreferenceDocuments
type WriteRow = (intent: ReturnType<typeof buildIntent>, key: string) => Promise<boolean>

const SOURCE_LABELS: Record<PreferenceSourceFilter, string> = {
  all: '全部',
  workspace: '工作区',
  global: '全局',
}
const STATUS_LABELS: Record<PreferenceStatusFilter, string> = {
  all: '全部',
  active: '已启用',
  disabled: '已停用',
}
const OPERATION_LABELS: Record<string, string> = {
  add: '新增',
  replace: '替换',
  enable: '启用',
  disable: '停用',
  remove: '删除',
}

function statusText(row: PreferenceRow): string {
  const status = row.status ?? 'active'
  if (status === 'active') return '已启用'
  if (status === 'disabled') return '已停用'
  if (status === 'deleted') return '已删除'
  return `未知状态 ${status}`
}

/** Rows default to workspace order, then global; a row never re-sorts on toggle. */
export function PreferenceList({
  client,
  documents,
  rows,
  write,
  mutate,
  connected = true,
  onRefresh,
  anchor,
  registerGuard,
}: {
  client: ApiClient
  documents: Documents
  rows: Record<string, RowWriteState>
  write: WriteRow
  mutate: ReturnType<typeof usePreferenceDocuments>['mutate']
  connected?: boolean
  onRefresh: () => void
  anchor?: KnowledgeFocus
  registerGuard?: (guard: DirtyGuard) => () => void
}) {
  const [query, setQuery] = useState('')
  const [source, setSource] = useState<PreferenceSourceFilter>('all')
  const [status, setStatus] = useState<PreferenceStatusFilter>('all')
  const [editor, setEditor] = useState<{ mode: 'add' | 'edit'; row?: PreferenceRow; scope: Scope } | null>(null)
  const [confirming, setConfirming] = useState<{ row: PreferenceRow; operation: 'remove' | 'disable' | 'enable' } | null>(null)
  const [diagnostics, setDiagnostics] = useState<unknown>(null)
  const [historyScope, setHistoryScope] = useState<Scope>('workspace')
  const [batchScope, setBatchScope] = useState<Scope>('workspace')

  const opener = useRef<HTMLElement | null>(null)
  const landed = useRef<KnowledgeFocus | null>(null)
  const editorDirty = editor !== null
  const { open: guardOpen, confirmLeave, settle } = useLeavePrompt()
  const promptLeave = useCallback(() => editorDirty ? confirmLeave() : Promise.resolve(true), [confirmLeave, editorDirty])
  useLeaveGuard(registerGuard, editorDirty, promptLeave)

  const allRows = useMemo(() => collectRows(documents), [documents])
  const visible = useMemo(() => filterRows(allRows, query, source, status), [allRows, query, source, status])
  const counts = rowCounts(visible)
  const failed = (['workspace', 'global'] as const).filter(scope => documents[scope].status === 'error')
  const loading = (['workspace', 'global'] as const).every(scope => documents[scope].status === 'loading')

  const closeEditor = () => {
    setEditor(null)
    opener.current?.focus?.()
    opener.current = null
  }
  const openEditor = (mode: 'add' | 'edit', scope: Scope, row?: PreferenceRow) => {
    opener.current = document.activeElement as HTMLElement | null
    setEditor({ mode, row, scope })
  }
  useEffect(() => {
    if (!anchor || landed.current === anchor) return
    if (anchor === 'add') {
      landed.current = anchor
      openEditor('add', source === 'global' ? 'global' : 'workspace')
      return
    }
    if (['list', 'show', 'replace', 'remove', 'enable', 'disable'].includes(anchor)) {
      landed.current = anchor
      document.querySelector<HTMLElement>('[aria-label="搜索规则"]')?.focus()
    }
  }, [anchor, source])
  const submitEditor = async ({ scope: target, statement }: { scope: Scope; statement: string }) => {
    if (editor === null) return
    const document: PreferenceDocumentState = documents[target]
    const intent =
      editor.mode === 'add'
        ? buildIntent(target, 'add', document.revision, { statement })
        : buildIntent(target, 'replace', document.revision, {
            preferenceId: editor.row?.preference_id,
            statement,
          })
    const key = editor.row?.key ?? `${target}:add`
    const ok = await write(intent, key)
    if (ok) closeEditor()
  }
  const toggle = async (row: PreferenceRow) => {
    const operation = (row.status ?? 'active') === 'active' ? 'disable' : 'enable'
    if (row.scope === 'global') {
      setConfirming({ row, operation })
      return
    }
    await write(buildIntent(row.scope, operation, documents[row.scope].revision, { preferenceId: row.preference_id }), row.key)
  }
  const remove = (row: PreferenceRow) => setConfirming({ row, operation: 'remove' })
  const confirmWrite = async () => {
    if (confirming === null) return
    const { row, operation } = confirming
    setConfirming(null)
    await write(
      buildIntent(row.scope, operation, documents[row.scope].revision, { preferenceId: row.preference_id }),
      row.key,
    )
  }
  const showDiagnostics = async () => {
    try {
      setDiagnostics(await client.knowledgeQuery('preference-status'))
    } catch {
      setDiagnostics({ error: '解析诊断不可用，请稍后重试。' })
    }
  }

  return (
    <section className="pp-prefs" aria-label="行为偏好">
      <header className="pp-card-head">
        <h2 className="pp-card-title">行为偏好</h2>
        <button
          type="button"
          className="pp-button pp-primary"
          disabled={!connected}
          onClick={() => openEditor('add', source === 'global' ? 'global' : 'workspace')}
        >
          ＋ 新增偏好
        </button>
      </header>

      <div className="pp-toolbar">
        <label className="pp-field pp-grow">
          <span className="pp-field-title">搜索规则</span>
          <input
            className="pp-input"
            aria-label="搜索规则"
            placeholder="按正文或 ID 搜索"
            value={query}
            onChange={event => setQuery(event.target.value)}
          />
        </label>
        <div className="pp-filter" role="group" aria-label="来源">
          <span className="pp-field-title">来源</span>
          {(['all', 'workspace', 'global'] as const).map(value => (
            <button
              key={value}
              type="button"
              className="pp-button"
              aria-pressed={source === value}
              onClick={() => setSource(value)}
            >
              {SOURCE_LABELS[value]}
            </button>
          ))}
        </div>
        <div className="pp-filter" role="group" aria-label="状态">
          <span className="pp-field-title">状态</span>
          {(['all', 'active', 'disabled'] as const).map(value => (
            <button
              key={value}
              type="button"
              className="pp-button"
              aria-pressed={status === value}
              onClick={() => setStatus(value)}
            >
              {STATUS_LABELS[value]}
            </button>
          ))}
        </div>
      </div>

      {failed.map(scope => (
        <p key={scope} className="pp-error" role="alert">
          {documents[scope].error}
          <button type="button" className="pp-button" onClick={onRefresh}>重试</button>
        </p>
      ))}
      {loading && <p className="pp-hint" role="status">正在读取偏好…</p>}

      <ul className="pp-pref-list">
        {visible.map(row => {
          const state = rows[row.key] ?? { status: 'idle', message: '' }
          const deleted = row.status === 'deleted'
          return (
            <li key={row.key} className={`pp-pref-row${(row.status ?? 'active') === 'disabled' ? ' is-disabled' : ''}`}>
              <button
                type="button"
                role="switch"
                aria-checked={(row.status ?? 'active') === 'active'}
                aria-label={`${statusText(row)}：${row.statement}`}
                className="pp-switch"
                disabled={state.status === 'busy' || deleted || !connected}
                onClick={() => void toggle(row)}
              >
                <span aria-hidden="true" />
              </button>
              <div className="pp-pref-main">
                <p className="pp-pref-statement">{row.statement}</p>
                <p className="pp-hint">
                  {row.scope === 'global' ? '全局 · 影响所有工作区' : '当前工作区'} · {statusText(row)}
                  {row.evidence_ids?.length ? ` · 证据 ${row.evidence_ids.length} 条` : ' · 无关联证据'}
                  {row.status === 'active' || row.status === 'disabled'
                    ? ` · ${row.preference_id} · r${row.revision}`
                    : ''}
                </p>
                {state.message && (
                  <p className={state.status === 'error' ? 'pp-error' : 'pp-hint'} role={state.status === 'error' ? 'alert' : 'status'}>
                    {state.message}
                  </p>
                )}
              </div>
              <div className="pp-pref-actions">
                <button
                  type="button"
                  className="pp-button"
                  disabled={state.status === 'busy' || deleted || !connected}
                  onClick={event => { opener.current = event.currentTarget; openEditor('edit', row.scope, row) }}
                >
                  编辑
                </button>
                <button type="button" className="pp-button pp-danger" disabled={deleted || !connected} onClick={() => remove(row)}>
                  删除规则
                </button>
              </div>
            </li>
          )
        })}
      </ul>
      {!loading && visible.length === 0 && (
        <p className="pp-hint">
          没有匹配的规则。
          {(query !== '' || source !== 'all' || status !== 'all') && (
            <button type="button" className="pp-button" onClick={() => { setQuery(''); setSource('all'); setStatus('all') }}>
              清除筛选
            </button>
          )}
        </p>
      )}

      <footer className="pp-pref-footer">
        <p className="pp-hint" role="status">
          已加载 {counts.loaded} 条 · 已启用 {counts.enabled} 条（记录状态，不是本次运行使用数量）
        </p>
        <button type="button" className="pp-button" onClick={() => void showDiagnostics()}>解析详情</button>
      </footer>
      <details className="pp-more">
        <summary>高级：同作用域批量修改（最多 8 项）</summary>
        <div className="pp-filter" role="group" aria-label="批量作用域">
          {(['workspace', 'global'] as const).map(value => (
            <button key={value} type="button" className="pp-button" aria-pressed={batchScope === value} onClick={() => setBatchScope(value)}>
              {value === 'global' ? '全局' : '工作区'}
            </button>
          ))}
        </div>
        <PreferenceBatch
          key={batchScope}
          scope={batchScope}
          revision={documents[batchScope].revision}
          entries={documents[batchScope].entries}
          mutate={(kind, body, target) =>
            kind === 'preferences'
              ? mutate(body as { arguments: PreferenceIntent }, target ?? `batch:${batchScope}`)
              : Promise.resolve(false)
          }
        />
      </details>

      <details className="pp-more">
        <summary>历史记录</summary>
        <p className="pp-hint">最近的操作历史</p>
        <div className="pp-filter" role="group" aria-label="历史作用域">
          {(['workspace', 'global'] as const).map(value => (
            <button key={value} type="button" className="pp-button" aria-pressed={historyScope === value} onClick={() => setHistoryScope(value)}>
              {value === 'global' ? '全局' : '工作区'}
            </button>
          ))}
        </div>
        {documents[historyScope].history.length === 0 && <p className="pp-hint">此作用域暂无历史。</p>}
        {documents[historyScope].history.map(batch => (
          <details key={batch.batch_id}>
            <summary className="pp-hint">
              {batch.created_at} · {batch.status}
              {batch.status === 'finalized' ? ` · r${batch.expected_document_revision} → r${batch.expected_document_revision + 1}` : ' · 未提交修订'}
            </summary>
            <p className="pp-hint">
              {batch.operations.map(item => `${OPERATION_LABELS[item.operation] ?? item.operation}${item.statement ? `：${item.statement}` : ''}`).join('；') || '无操作明细'}
            </p>
            <p className="pp-hint">变更前：{batch.before.map(row => row.statement).join('；') || '无'}</p>
            <p className="pp-hint">变更后：{batch.after.map(row => row.statement).join('；') || '无'}</p>
          </details>
        ))}
      </details>
      {diagnostics !== null && (
        <details className="pp-more">
          <summary>偏好解析状态</summary>
          <Facts value={diagnostics} />
        </details>
      )}

      {editor !== null && (
        <PreferenceEditorDialog
          mode={editor.mode}
          scope={editor.scope}
          initialStatement={editor.row?.statement ?? ''}
          busy={!connected || (rows[editor.row?.key ?? `${editor.scope}:add`]?.status ?? 'idle') === 'busy'}
          error={rows[editor.row?.key ?? `${editor.scope}:add`]?.status === 'error' ? rows[editor.row?.key ?? `${editor.scope}:add`]?.message : ''}
          onSubmit={value => void submitEditor(value)}
          onCancel={closeEditor}
        />
      )}

      <LeaveGuardDialog
        open={guardOpen}
        title="偏好编辑尚未完成"
        description="新增或编辑表单还开着。没有本地草稿；可以放弃后离开，或留在此页继续。"
        onDiscard={() => { closeEditor(); settle(true) }}
        onStay={() => settle(false)}
      />
      {confirming !== null && (
        <div className="pp-modal" role="presentation">
          <dialog className="pp-dialog" open aria-labelledby="pp-confirm-title">
            <h2 id="pp-confirm-title">
              {confirming.operation === 'remove'
                ? '删除这条规则？'
                : confirming.operation === 'disable'
                  ? '停用这条全局规则？'
                  : '启用这条全局规则？'}
            </h2>
            <p className="pp-pref-statement">{confirming.row.statement}</p>
            <p className="pp-hint">
              {confirming.row.scope === 'global' ? '全局 · 影响所有工作区。' : '当前工作区。'}
              {confirming.operation === 'remove'
                ? '删除后从普通列表移出，历史会保留；如需恢复请重新新增。'
                : '这条记录在所有工作区生效。'}
            </p>
            <div className="pp-dialog-actions">
              <button type="button" className="pp-button pp-danger" onClick={() => void confirmWrite()}>
                {confirming.operation === 'remove' ? '确认删除' : '确认'}
              </button>
              <button type="button" className="pp-button" onClick={() => setConfirming(null)}>取消</button>
            </div>
          </dialog>
        </div>
      )}
    </section>
  )
}

export { rowKey }