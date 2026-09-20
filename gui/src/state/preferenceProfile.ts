import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, type ApiClient } from '../api/client'
import type { Profile } from '../api/management'

export type { Profile }

/** Server-side limits; the browser only mirrors them for immediate feedback. */
export const PROFILE_NAME_MAX = 2048
export const PROFILE_SUMMARY_MAX = 2048
export const PROFILE_ITEM_MAX = 512
export const PROFILE_LIST_FIELDS = ['tech_stack', 'goals', 'constraints', 'conventions'] as const
export type ProfileListField = (typeof PROFILE_LIST_FIELDS)[number]
export type ProfileField = keyof Profile

export const PROFILE_FIELD_LABELS: Record<ProfileField, string> = {
  name: '项目名称',
  summary: '项目概述',
  tech_stack: '技术栈',
  goals: '项目目标',
  constraints: '项目约束',
  conventions: '项目约定',
}

export function emptyProfile(): Profile {
  return { name: '', summary: null, goals: [], tech_stack: [], constraints: [], conventions: [] }
}

/** Mirrors the server's whitespace folding; Python casefold stays authoritative. */
export function normalizeItem(value: string): string {
  return value.split(/\s+/u).filter(Boolean).join(' ').toLocaleLowerCase()
}

export function profileEquals(left: Profile | null, right: Profile | null): boolean {
  if (left === null || right === null) return left === right
  return (
    left.name === right.name &&
    (left.summary ?? null) === (right.summary ?? null) &&
    PROFILE_LIST_FIELDS.every(field => left[field].join('\u0000') === right[field].join('\u0000'))
  )
}

export function isDirty(draft: Profile | null, base: Profile | null): boolean {
  return draft !== null && !profileEquals(draft, base)
}

export function addItem(items: string[], text: string): { items: string[]; error: string } {
  const value = text.trim()
  if (!value) return { items, error: '' }
  if (value.length > PROFILE_ITEM_MAX) return { items, error: `条目超出 ${PROFILE_ITEM_MAX} 字符` }
  if (items.some(item => normalizeItem(item) === normalizeItem(value))) {
    return { items, error: '已存在相同条目' }
  }
  return { items: [...items, value], error: '' }
}

export function updateItem(items: string[], index: number, text: string): { items: string[]; error: string } {
  const value = text.trim()
  if (!value) return { items, error: '条目不能为空' }
  if (value.length > PROFILE_ITEM_MAX) return { items, error: `条目超出 ${PROFILE_ITEM_MAX} 字符` }
  const others = items.filter((_, position) => position !== index)
  if (others.some(item => normalizeItem(item) === normalizeItem(value))) {
    return { items, error: '已存在相同条目' }
  }
  return { items: items.map((item, position) => (position === index ? value : item)), error: '' }
}

export function removeItem(items: string[], index: number): string[] {
  return items.filter((_, position) => position !== index)
}

export type ProfileErrors = Partial<Record<ProfileField, string>>

/** Locate every problem field so the form can focus the first one. */
export function validateProfile(profile: Profile): { errors: ProfileErrors; first: ProfileField | null } {
  const errors: ProfileErrors = {}
  if (!profile.name.trim()) errors.name = '请填写项目名称'
  else if (profile.name.length > PROFILE_NAME_MAX) errors.name = `项目名称超出 ${PROFILE_NAME_MAX} 字符`
  const summary = profile.summary ?? ''
  if (summary.length > PROFILE_SUMMARY_MAX) errors.summary = `项目概述超出 ${PROFILE_SUMMARY_MAX} 字符`
  for (const field of PROFILE_LIST_FIELDS) {
    const items = profile[field]
    if (items.some(item => !item.trim())) {
      errors[field] = `${PROFILE_FIELD_LABELS[field]}存在空条目`
      continue
    }
    if (items.some(item => item.length > PROFILE_ITEM_MAX)) {
      errors[field] = `${PROFILE_FIELD_LABELS[field]}存在超出 ${PROFILE_ITEM_MAX} 字符的条目`
      continue
    }
    const seen = new Set<string>()
    for (const item of items) {
      const key = normalizeItem(item)
      if (seen.has(key)) {
        errors[field] = `${PROFILE_FIELD_LABELS[field]}已存在相同条目`
        break
      }
      seen.add(key)
    }
  }
  const order: ProfileField[] = ['name', 'summary', 'tech_stack', 'goals', 'constraints', 'conventions']
  return { errors, first: order.find(field => errors[field] !== undefined) ?? null }
}

/** Wire snapshot: blank summary is null, list order and text are preserved. */
export function toPayload(profile: Profile): Profile {
  const summary = (profile.summary ?? '').trim()
  return {
    name: profile.name,
    summary: summary === '' ? null : profile.summary,
    tech_stack: [...profile.tech_stack],
    goals: [...profile.goals],
    constraints: [...profile.constraints],
    conventions: [...profile.conventions],
  }
}

/** Stable request identity: identical intent reuses the command id on retry. */
export function intentKey(workspaceId: string, kind: string, body: unknown): string {
  return JSON.stringify([workspaceId, kind, body])
}

export function newCommandId(): string {
  return `cmd_${crypto.randomUUID().replaceAll('-', '')}`
}

export type ProfileWriteStatus =
  | 'idle'
  | 'saving'
  | 'saved'
  | 'unchanged'
  | 'refresh-failed'
  | 'conflict'
  | 'error'

export interface ProfileDocument {
  profile: Profile | null
  revision: number
}

/**
 * One Profile document with its own revision, draft baseline and write status.
 * A background refresh never replaces a dirty draft; it only reports that the
 * server moved so the user can compare or re-read deliberately.
 */
export function useProfileDocument(client: ApiClient, workspaceId: string) {
  const [snapshot, setSnapshot] = useState<ProfileDocument | null>(null)
  const [queryError, setQueryError] = useState('')
  const [draft, setDraft] = useState<Profile | null>(null)
  const [base, setBase] = useState<Profile | null>(null)
  const [baseRevision, setBaseRevision] = useState(0)
  const [status, setStatus] = useState<ProfileWriteStatus>('idle')
  const [message, setMessage] = useState('')
  const [serverAhead, setServerAhead] = useState(false)
  const [refreshToken, setRefreshToken] = useState(0)
  const generation = useRef(0)
  const intent = useRef<{ key: string; commandId: string } | null>(null)
  const dirtyRef = useRef(false)

  const dirty = isDirty(draft, base)
  dirtyRef.current = dirty

  const load = useCallback(async () => {
    const current = ++generation.current
    try {
      const value = await client.managementQuery('profile')
      if (current !== generation.current) return
      setSnapshot({ profile: value.profile, revision: value.revision })
      setQueryError('')
      setServerAhead(
        dirtyRef.current &&
          (!profileEquals(value.profile, base) || value.revision !== baseRevision),
      )
    } catch {
      if (current !== generation.current) return
      setQueryError('项目画像加载失败，请重试。')
    }
  }, [client, base, baseRevision])

  useEffect(() => {
    setSnapshot(null)
    setQueryError('')
    setDraft(null)
    setBase(null)
    setBaseRevision(0)
    setStatus('idle')
    setMessage('')
    setServerAhead(false)
    intent.current = null
    void load()
  }, [client, workspaceId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (refreshToken === 0) return
    void load()
  }, [refreshToken, load])

  // A background refresh keeps the draft intact; polling stops with the page.
  useEffect(() => {
    const tick = () => { void load() }
    window.addEventListener('focus', tick)
    const timer = window.setInterval(tick, 10000)
    return () => { window.clearInterval(timer); window.removeEventListener('focus', tick) }
  }, [load])

  // The first edit establishes the baseline this draft will be saved against.
  const edit = useCallback(
    (next: Profile) => {
      if (draft === null && snapshot !== null) {
        setBase(snapshot.profile)
        setBaseRevision(snapshot.revision)
      }
      setDraft(next)
      setStatus('idle')
      setMessage('')
    },
    [draft, snapshot],
  )

  const discard = useCallback(() => {
    setDraft(base)
    setStatus('idle')
    setMessage('')
    setServerAhead(false)
  }, [base])

  const readServer = useCallback(async () => {
    await load()
    const value = await client.managementQuery('profile')
    setBase(value.profile)
    setBaseRevision(value.revision)
    setDraft(current => (current === null ? current : value.profile))
    setServerAhead(false)
    return value
  }, [client, load])

  const save = useCallback(async (): Promise<boolean> => {
    if (draft === null || status === 'saving') return false
    const { errors, first } = validateProfile(draft)
    if (first !== null) {
      setStatus('error')
      setMessage(errors[first] ?? '请检查填写内容')
      return false
    }
    const payload = toPayload(draft)
    const body = { expected_revision: baseRevision, profile: payload }
    const key = intentKey(workspaceId, 'profile-save', body)
    const commandId = intent.current?.key === key ? intent.current.commandId : newCommandId()
    intent.current = { key, commandId }
    setStatus('saving')
    setMessage('正在保存…')
    try {
      const result = (await client.managementCommand('profile-save', {
        ...body,
        command_id: commandId,
      })) as { value?: { status?: string; revision?: number } }
      intent.current = null
      const unchanged = result?.value?.status === 'unchanged'
      try {
        const value = await client.managementQuery('profile')
        setSnapshot({ profile: value.profile, revision: value.revision })
        setQueryError('')
        setBase(value.profile)
        setBaseRevision(value.revision)
        setDraft(value.profile)
        setServerAhead(false)
        setStatus(unchanged ? 'unchanged' : 'saved')
        setMessage(unchanged ? '内容与服务端一致，未产生新版本。' : '已保存；之后的上下文解析会使用新资料。')
      } catch {
        setStatus('refresh-failed')
        setMessage('已保存，但刷新权威数据失败；请重新读取确认。')
      }
      return true
    } catch (error) {
      if (error instanceof ApiError && error.status < 500) intent.current = null
      if (error instanceof ApiError && error.status === 409) {
        setStatus('conflict')
        setMessage('服务端内容已变化：草稿已保留，请比较最新值后重新整理。')
        setServerAhead(true)
        void load()
      } else {
        setStatus('error')
        setMessage('保存结果未确认；可重试同一意图，或先重新读取。')
      }
      return false
    }
  }, [client, draft, baseRevision, status, workspaceId, load])

  const clear = useCallback(async (): Promise<boolean> => {
    if (status === 'saving') return false
    const body = {
      expected_revision: baseRevision,
      command: { scope: 'workspace', target: 'profile', operation: 'reset' },
    }
    const key = intentKey(workspaceId, 'profile', body)
    const commandId = intent.current?.key === key ? intent.current.commandId : newCommandId()
    intent.current = { key, commandId }
    setStatus('saving')
    setMessage('正在清空…')
    try {
      await client.managementCommand('profile', { ...body, command_id: commandId })
      intent.current = null
      const value = await client.managementQuery('profile')
      setSnapshot({ profile: value.profile, revision: value.revision })
      setQueryError('')
      setBase(value.profile)
      setBaseRevision(value.revision)
      setDraft(value.profile)
      setStatus('saved')
      setMessage('项目画像已清空；偏好规则和历史保留。')
      return true
    } catch (error) {
      if (error instanceof ApiError && error.status < 500) intent.current = null
      setStatus('error')
      setMessage('清空失败；草稿已保留。')
      return false
    }
  }, [client, baseRevision, status, workspaceId])

  return {
    snapshot,
    queryError,
    draft,
    base,
    baseRevision,
    status,
    message,
    serverAhead,
    dirty,
    edit,
    discard,
    save,
    clear,
    reload: readServer,
    refresh: () => setRefreshToken(value => value + 1),
  }
}
