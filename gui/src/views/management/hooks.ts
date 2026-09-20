import { useEffect, useRef, useState } from 'react'
import type { ApiClient } from '../../api/client'
import { ApiError } from '../../api/client'
import type { ManagementQueries, Scope } from '../../api/management'
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

/**
 * Shared management mutation path (asset pages and the standalone tools
 * page): one in-flight command at a time, the same uncertain delivery reuses
 * the original command id, `refresh` bumps re-run the query hooks.
 */
export function useManagementMutate(client: ApiClient, connected: boolean, onChanged?: () => void) {
  const [refresh, setRefresh] = useState(0)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const retry = useRef<{ key: string; commandId: string } | null>(null)
  const mutate: Mutate = async (kind, body, target) => {
    if (busy || !connected) return false
    setBusy(true); setMessage('')
    const key = JSON.stringify([kind, target, body])
    const commandId = typeof body.command_id === "string" ? body.command_id : retry.current?.key === key ? retry.current.commandId : `cmd_${crypto.randomUUID().replaceAll('-', '')}`
    retry.current = { key, commandId }
    try {
      await client.managementCommand(kind, { ...body, command_id: commandId }, target)
      retry.current = null
      setRefresh(n => n + 1); onChanged?.(); setMessage('已保存。新设置将在之后的上下文解析中生效。')
      return true
    } catch (error) {
      if (error instanceof ApiError && error.status < 500) retry.current = null
      setMessage(error instanceof ApiError && error.status === 409
        ? '内容已变化或状态不允许此操作。你的输入已保留，请刷新事实后重试。'
        : '保存失败。可重试相同操作；输入会保留。')
      return false
    } finally { setBusy(false) }
  }
  const bump = () => setRefresh(n => n + 1)
  const reload = () => { bump(); onChanged?.() }
  return { mutate, busy, message, refresh, reload, bump, setMessage }
}
