import { useEffect, useRef, useState } from 'react'
import type { ApiClient } from '../../api/client'
import { ApiError } from '../../api/client'
import type { ManagementCommand, ManagementQueries, Scope } from '../../api/management'
import type { Mutate } from './types'

/** Workspace identity is the display name, never Profile.name. */
export function useWorkspaceName(client: ApiClient, workspaceId: string | null): string {
  const [name, setName] = useState('')
  const [scope, setScope] = useState<string | null>(null)
  useEffect(() => {
    if (workspaceId === null) {
      setName('')
      setScope(null)
      return
    }
    setScope(workspaceId)
    setName('')
    let active = true
    void client
      .workspaces()
      .then(list => {
        if (!active) return
        const entry = list.items.find(item => item.workspace_id === workspaceId)
        setName(entry?.display_name || workspaceId.slice(0, 12))
      })
      .catch(() => { if (active) setName(workspaceId.slice(0, 12)) })
    return () => { active = false }
  }, [client, workspaceId])
  if (scope !== workspaceId) return (workspaceId ?? '').slice(0, 12)
  return name || (workspaceId ?? '').slice(0, 12)
}

export function useManagement<K extends keyof ManagementQueries>(client: ApiClient, kind: K, scope: Scope, refresh: number, page = 0) {
  const [data, setData] = useState<ManagementQueries[K] | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    setData(null)
    let request = 0
    const load = () => {
      const current = ++request
      void client.managementQuery(kind, { scope, page }).then(value => { if (active && current === request) { setData(value); setError('') } })
        .catch(() => { if (active && current === request) setError('加载失败，请刷新重试。') })
    }
    load()
    window.addEventListener('focus', load)
    const timer = window.setInterval(load, 10000)
    return () => { active = false; window.clearInterval(timer); window.removeEventListener('focus', load) }
  }, [client, kind, scope, refresh, page])
  return { data, error }
}

/** Keep uncertain commands intact while read-back updates the visible rows. */
export function useManagementMutate(client: ApiClient, connected: boolean, onChanged?: () => void) {
  const [refresh, setRefresh] = useState(0)
  const [busy, setBusy] = useState(false)
  const [awaitingRetry, setAwaitingRetry] = useState(false)
  const [message, setMessage] = useState('')
  const inFlight = useRef(false)
  const pending = useRef<{
    key: string
    kind: ManagementCommand
    body: Record<string, unknown>
    target?: string
  } | null>(null)
  const send = async (command: NonNullable<typeof pending.current>) => {
    if (inFlight.current || !connected) return false
    inFlight.current = true
    setBusy(true)
    setMessage('')
    try {
      await client.managementCommand(command.kind, command.body, command.target)
      pending.current = null
      setAwaitingRetry(false)
      setRefresh(n => n + 1)
      onChanged?.()
      setMessage('已保存。新设置将在之后的上下文解析中生效。')
      return true
    } catch (error) {
      const rejected = error instanceof ApiError && error.status >= 400 && error.status < 500
      if (rejected) {
        pending.current = null
        setAwaitingRetry(false)
      } else {
        pending.current = command
        setAwaitingRetry(true)
        setRefresh(n => n + 1)
        onChanged?.()
      }
      setMessage(error instanceof ApiError && error.status === 409
        ? '内容已变化或状态不允许此操作。你的输入已保留，请刷新事实后重试。'
        : rejected ? `${error.message}；输入已保留。`
        : '保存结果未确认。正在重新读取；请点击“重试原操作”确认原提交结果。')
      return false
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }
  const mutate: Mutate = async (kind, body, target) => {
    if (inFlight.current || !connected) return false
    const key = JSON.stringify([kind, target, body])
    if (pending.current) {
      // A refreshed enable switch may now mean disable, and a draft may have
      // a new id/version. Neither is a retry of the original submission.
      if (pending.current.key !== key) {
        setMessage('请先点击“重试原操作”确认上次提交结果，再提交新的变更。')
        return false
      }
      return send(pending.current)
    }
    const commandId = typeof body.command_id === 'string'
      ? body.command_id
      : `cmd_${crypto.randomUUID().replaceAll('-', '')}`
    const command = {
      key, kind, target,
      body: JSON.parse(JSON.stringify({ ...body, command_id: commandId })) as Record<string, unknown>,
    }
    return send(command)
  }
  const retryOriginal = () => pending.current ? send(pending.current) : Promise.resolve(false)
  const bump = () => setRefresh(n => n + 1)
  const reload = () => { bump(); onChanged?.() }
  return { mutate, retryOriginal, awaitingRetry, busy, message, refresh, reload, bump, setMessage }
}
