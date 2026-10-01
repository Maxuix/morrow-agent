import { expect, it } from 'vitest'
import { ApiClient } from './client'
import { OutboxStorage } from '../state/chat'
import type { InteractionInput } from './chat'

it('uses authenticated session POSTs and preserves the selected ID and message through outbox retry', async () => {
  const calls: {url: string; body: unknown; method: string | undefined; authorization: string | null}[] = []
  const client = new ApiClient({baseUrl: '', token: 'local-token', fetchImpl: async (url, init) => {
    calls.push({url: String(url), body: JSON.parse(String(init?.body)), method: init?.method,
      authorization: new Headers(init?.headers).get('authorization')})
    return new Response(JSON.stringify(String(url).endsWith('/interactions') ? {receipt: {status: 'accepted'}} : {}))
  }})
  await client.computerWindowCandidates('ws one', 'session/one')
  const selection = {candidate_ids: ['ccandidate_a'], allow_action: false, share_images: false, delivery: 'background' as const}
  await client.selectComputerWindows('ws one', 'session/one', selection)
  const input: InteractionInput = {client_message_id: 'cmd_original', text: '观察窗口', intent: 'send', computer_selection_id: 'cselection_a'}
  const values = new Map<string, string>()
  const outbox = new OutboxStorage({getItem: key => values.get(key) ?? null, setItem: (key, value) => {values.set(key, value)}})
  outbox.write('ws one', 'session/one', input)
  const restored = outbox.read('ws one', 'session/one')!
  expect(outbox.read('ws one', 'another')).toBeNull()
  await client.chatSend('ws one', 'session/one', restored)
  await client.chatSend('ws one', 'session/one', restored)
  expect(calls.map(call => call.url)).toEqual([
    '/v1/workspaces/ws%20one/sessions/session%2Fone/computer-use/candidates',
    '/v1/workspaces/ws%20one/sessions/session%2Fone/computer-use/selection',
    '/v1/workspaces/ws%20one/sessions/session%2Fone/interactions',
    '/v1/workspaces/ws%20one/sessions/session%2Fone/interactions',
  ])
  expect(calls.map(call => call.body)).toEqual([{}, selection, input, input])
  expect(calls.every(call => call.method === 'POST' && call.authorization === 'Bearer local-token')).toBe(true)
})
