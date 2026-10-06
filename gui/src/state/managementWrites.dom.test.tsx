// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../api/client'
import { useManagementMutate } from '../views/management/hooks'
import { useKnowledgeAction } from '../views/LearningOperations'
import { emptyProfile, useProfileDocument } from './preferenceProfile'
import { buildIntent, usePreferenceDocuments } from './preferences'

afterEach(cleanup)

function losingReplyClient(kind: 'network' | 503 | 409 = 'network') {
  const commands: Record<string, unknown>[] = []
  const queries: string[] = []
  let profile = emptyProfile()
  let revision = 0
  const client = new ApiClient({ baseUrl: '', token: '', fetchImpl: async (input, init) => {
    const path = String(input)
    if (init?.method === 'POST') {
      commands.push(JSON.parse(String(init.body)))
      if (commands[0].profile) profile = commands[0].profile as typeof profile
      if (path.endsWith('/profile')) profile = emptyProfile()
      // Simulate a command committed exactly once, followed by a lost reply.
      revision = 1
      if (commands.length === 1) {
        if (kind === 'network') throw new TypeError('lost response')
        return Response.json({ error: { code: 'test', message: 'rejected' } }, { status: kind })
      }
      return Response.json(path.includes('/knowledge-commands/') ? { status: 'saved' } : { result: { value: { status: 'saved' } } })
    }
    queries.push(path)
    if (path.includes('/preferences')) return Response.json({ document: { revision, entries: [] }, history: [] })
    return Response.json({ profile, revision })
  } })
  return { client, commands, queries }
}

describe('uncertain command identity and read-back', () => {
  it.each(['network', 503, 409] as const)('management retries %s with the appropriate identity', async kind => {
    const { client, commands } = losingReplyClient(kind)
    const changed = vi.fn()
    const { result } = renderHook(() => useManagementMutate(client, true, changed))
    await act(async () => { expect(await result.current.mutate('profile-save', { profile: emptyProfile() })).toBe(false) })
    expect(changed).toHaveBeenCalledTimes(kind === 409 ? 0 : 1)
    if (kind !== 409) expect(result.current.message).toContain('结果未确认')
    await act(async () => { expect(await result.current.mutate('profile-save', { profile: emptyProfile() })).toBe(true) })
    if (kind === 409) expect(commands[1].command_id).not.toBe(commands[0].command_id)
    else expect(commands[1].command_id).toBe(commands[0].command_id)
  })

  it('preference read-back keeps the original revision-bound intent retryable', async () => {
    const { client, commands, queries } = losingReplyClient()
    const { result } = renderHook(() => usePreferenceDocuments(client, 'ws_a'))
    await waitFor(() => expect(result.current.documents.workspace.status).toBe('ready'))
    const intent = buildIntent('workspace', 'add', 0, { statement: 'Keep this rule' })
    const before = queries.length
    await act(async () => { expect(await result.current.write(intent, 'new')).toBe(false) })
    await waitFor(() => expect(result.current.documents.workspace.revision).toBe(1))
    expect(queries.length).toBeGreaterThan(before)
    expect(result.current.rows.new.message).toContain('结果未确认')
    // The actual form rebuilds its intent from the refreshed document revision.
    const retry = buildIntent('workspace', 'add', result.current.documents.workspace.revision, { statement: 'Keep this rule' })
    await act(async () => { expect(await result.current.write(retry, 'new')).toBe(true) })
    expect(commands[1]).toEqual(commands[0])
  })

  it.each(['save', 'clear'] as const)('profile %s reads back without replacing the local draft or retry identity', async operation => {
    const { client, commands } = losingReplyClient()
    const { result } = renderHook(() => useProfileDocument(client, 'ws_a'))
    await waitFor(() => expect(result.current.snapshot).not.toBeNull())
    act(() => result.current.edit({ ...emptyProfile(), name: 'Local draft' }))
    await act(async () => { expect(await result.current[operation]()).toBe(false) })
    await waitFor(() => expect(result.current.snapshot?.revision).toBe(1))
    expect(result.current.draft?.name).toBe('Local draft')
    expect(result.current.baseRevision).toBe(0)
    expect(result.current.message).toContain('结果未确认')
    await act(async () => { expect(await result.current[operation]()).toBe(true) })
    expect(commands[1]).toEqual(commands[0])
  })

  it('knowledge writes preserve their command after a lost reply and trigger read-back', async () => {
    const { client, commands } = losingReplyClient()
    const changed = vi.fn()
    const { result } = renderHook(() => useKnowledgeAction(client, changed))
    const body = { action: 'mode', mode: 'off', expected_row_version: 1 }
    await act(async () => { expect(await result.current.act('learning', body)).toBe(false) })
    expect(changed).toHaveBeenCalledOnce()
    expect(result.current.message).toContain('结果未确认')
    await act(async () => { expect(await result.current.act('learning', body)).toBe(true) })
    expect(commands[1]).toEqual(commands[0])
  })
})
