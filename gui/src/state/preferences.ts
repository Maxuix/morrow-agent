import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, type ApiClient } from '../api/client'
import type { Preference, PreferencePage, Scope } from '../api/management'
import { newCommandId } from './preferenceProfile'

export const PREFERENCE_STATEMENT_MAX = 512
export const PREFERENCE_SCOPES: readonly Scope[] = ['workspace', 'global']

export type PreferenceSourceFilter = 'all' | Scope
export type PreferenceStatusFilter = 'all' | 'active' | 'disabled'

export interface PreferenceRow extends Preference {
  scope: Scope
  key: string
}

export interface PreferenceDocumentState {
  status: 'loading' | 'ready' | 'error'
  revision: number
  entries: Preference[]
  history: PreferencePage['history']
  error: string
}

export type PreferenceDocuments = Record<Scope, PreferenceDocumentState>

export function emptyDocuments(): PreferenceDocuments {
  const state = (): PreferenceDocumentState => ({
    status: 'loading',
    revision: 0,
    entries: [],
    history: [],
    error: '',
  })
  return { workspace: state(), global: state() }
}

/** Server counts Python string length: one code point per emoji, not UTF-16 units. */
export function charCount(value: string): number {
  return [...value].length
}

export function rowKey(scope: Scope, preferenceId: string): string {
  return `${scope}:${preferenceId}`
}

/** Workspace rows first, then global; each document keeps its own order. */
export function collectRows(documents: PreferenceDocuments): PreferenceRow[] {
  const rows: PreferenceRow[] = []
  for (const scope of PREFERENCE_SCOPES) {
    for (const entry of documents[scope].entries) {
      rows.push({ ...entry, scope, key: rowKey(scope, entry.preference_id) })
    }
  }
  return rows
}

/** Search text and id only; latin text ignores case, Chinese matches directly. */
export function filterRows(
  rows: PreferenceRow[],
  query: string,
  source: PreferenceSourceFilter,
  status: PreferenceStatusFilter,
): PreferenceRow[] {
  const needle = query.trim().toLocaleLowerCase()
  return rows.filter(row => {
    if (source !== 'all' && row.scope !== source) return false
    const state = row.status ?? 'active'
    // Deleted records are history-only; the list never offers enable or restore.
    if (state === 'deleted') return false
    if (status === 'active' && state !== 'active') return false
    if (status === 'disabled' && state !== 'disabled') return false
    if (needle === '') return true
    return (
      row.statement.toLocaleLowerCase().includes(needle) ||
      row.preference_id.toLocaleLowerCase().includes(needle)
    )
  })
}

/** Enabled is a record state, never the count injected into one run. */
export function rowCounts(rows: PreferenceRow[]): { loaded: number; enabled: number } {
  return {
    loaded: rows.length,
    enabled: rows.filter(row => (row.status ?? 'active') === 'active').length,
  }
}

export function statementError(statement: string): string {
  const value = statement.trim()
  if (!value) return '规则正文不能为空'
  if (charCount(statement) > PREFERENCE_STATEMENT_MAX) {
    return `规则正文超出 ${PREFERENCE_STATEMENT_MAX} 字符`
  }
  return ''
}

export type PreferenceOperation = 'add' | 'replace' | 'enable' | 'disable' | 'remove'

export interface PreferenceIntent {
  scope: Scope
  expected_revision: number
  operations: Record<string, unknown>[]
}

export function buildIntent(
  scope: Scope,
  operation: PreferenceOperation,
  expectedRevision: number,
  options: { preferenceId?: string; statement?: string } = {},
): PreferenceIntent {
  const item: Record<string, unknown> = { operation }
  if (options.preferenceId !== undefined) item.preference_id = options.preferenceId
  if (options.statement !== undefined) item.statement = options.statement
  return { scope, expected_revision: expectedRevision, operations: [item] }
}

/** The document revision of the row's own scope is the only valid baseline. */
export function revisionFor(documents: PreferenceDocuments, scope: Scope): number {
  return documents[scope].revision
}

export function rowIsGlobal(row: PreferenceRow): boolean {
  return row.scope === 'global'
}

export function globalImpactText(row: PreferenceRow): string {
  return `全局规则“${row.statement}”影响所有工作区。`
}

export type RowWriteState = { status: 'idle' | 'busy' | 'error'; message: string }

/**
 * Two independent preference documents with their own revisions, plus a
 * per-scope serial write queue. A failed scope keeps the other scope usable.
 */
export function usePreferenceDocuments(client: ApiClient, workspaceId: string) {
  const [documents, setDocuments] = useState<PreferenceDocuments>(emptyDocuments)
  const [rows, setRows] = useState<Record<string, RowWriteState>>({})
  const [message, setMessage] = useState('')
  const [refreshToken, setRefreshToken] = useState(0)
  const generation = useRef(0)
  const queue = useRef<Record<Scope, Promise<unknown>>>({
    workspace: Promise.resolve(),
    global: Promise.resolve(),
  })
  const intents = useRef<Map<string, string>>(new Map())

  const load = useCallback(async () => {
    const current = ++generation.current
    setDocuments(previous => ({
      workspace: { ...previous.workspace, status: 'loading' },
      global: { ...previous.global, status: 'loading' },
    }))
    const results = await Promise.allSettled(
      PREFERENCE_SCOPES.map(scope => client.managementQuery('preferences', { scope })),
    )
    if (current !== generation.current) return
    setDocuments(previous => {
      const next = { ...previous }
      PREFERENCE_SCOPES.forEach((scope, index) => {
        const result = results[index]
        if (result.status === 'fulfilled') {
          next[scope] = {
            status: 'ready',
            revision: result.value.document.revision,
            entries: result.value.document.entries,
            history: result.value.history,
            error: '',
          }
        } else {
          next[scope] = {
            ...previous[scope],
            status: 'error',
            error:
              scope === 'global' ? '全局偏好加载失败，请重试。' : '当前工作区偏好加载失败，请重试。',
          }
        }
      })
      return next
    })
  }, [client])

  useEffect(() => {
    setDocuments(emptyDocuments())
    setRows({})
    setMessage('')
    intents.current.clear()
    void load()
  }, [client, workspaceId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (refreshToken === 0) return
    void load()
  }, [refreshToken, load])

  useEffect(() => {
    const tick = () => { void load() }
    window.addEventListener('focus', tick)
    const timer = window.setInterval(tick, 10000)
    return () => { window.clearInterval(timer); window.removeEventListener('focus', tick) }
  }, [load])

  const setRow = (key: string, state: RowWriteState) =>
    setRows(previous => ({ ...previous, [key]: state }))

  const applyDocument = useCallback(
    (scope: Scope, page: PreferencePage) =>
      setDocuments(previous => ({
        ...previous,
        [scope]: {
          status: 'ready',
          revision: page.document.revision,
          entries: page.document.entries,
          history: page.history,
          error: '',
        },
      })),
    [],
  )

  /**
   * Serialize writes per scope; an identical intent reuses its command id so an
   * uncertain retry cannot create a second rule.
   */
  const mutate = useCallback(
    (body: { arguments: PreferenceIntent }, rowKeyForState: string): Promise<boolean> => {
      const key = JSON.stringify([workspaceId, 'preferences', body])
      const scope = body.arguments.scope
      const commandId = intents.current.get(key) ?? newCommandId()
      intents.current.set(key, commandId)
      setRow(rowKeyForState, { status: 'busy', message: '正在提交…' })
      const run = queue.current[scope].then(async () => {
        try {
          await client.managementCommand('preferences', { ...body, command_id: commandId })
          intents.current.delete(key)
          setRow(rowKeyForState, { status: 'idle', message: '' })
          setMessage('已保存。新设置将在之后的上下文解析中生效。')
          applyDocument(scope, await client.managementQuery('preferences', { scope }))
          return true
        } catch (error) {
          const conflict = error instanceof ApiError && error.status === 409
          if (error instanceof ApiError && error.status < 500) intents.current.delete(key)
          setRow(rowKeyForState, {
            status: 'error',
            message: conflict
              ? '该作用域内容已变化，请刷新后重试；开关保持最后确认状态。'
              : '保存结果未确认，可重试相同操作。',
          })
          return false
        }
      })
      queue.current[scope] = run.catch(() => undefined)
      return run
    },
    [applyDocument, client, workspaceId],
  )

  const write = useCallback(
    (intent: PreferenceIntent, rowKeyForState: string) =>
      mutate({ arguments: intent }, rowKeyForState),
    [mutate],
  )

  return {
    documents,
    rows,
    message,
    setMessage,
    load,
    write,
    mutate,
    refresh: () => setRefreshToken(value => value + 1),
  }
}
