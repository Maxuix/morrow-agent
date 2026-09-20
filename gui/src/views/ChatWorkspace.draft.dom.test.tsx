// @vitest-environment jsdom
import { createRoot, type Root } from 'react-dom/client'
import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../api/client'
import type { ChatCapabilities } from '../api/chat'
import type { SessionWire } from '../api/types'
import { DraftStorage } from '../state/chat'
import { commandId } from './lib/editor'
import { DraftSession } from './ChatWorkspace'

const capabilities: ChatCapabilities = {
  interaction_protocol_version: 1,
  workspace_id: 'ws_1',
  features: {},
  limits: {text_chars: 8000},
  execution_ready: true,
}

const created: SessionWire = {
  session_id: 'ses_first_prompt',
  lifecycle: 'active',
  health: 'ok',
  created_at: '2026-09-16T00:00:00Z',
  updated_at: '2026-09-16T00:00:00Z',
  current_task_run_id: null,
  parent_session_id: null,
}

function storage() {
  const values = new Map<string, string>()
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value) },
    removeItem: (key: string) => { values.delete(key) },
  }
}

describe('local conversation drafts', () => {
  let root: Root
  let container: HTMLDivElement

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => root.unmount())
    container.remove()
  })

  it('does not create a Core Session until the first Prompt is sent', async () => {
    const calls: Array<{url: string; method: string}> = []
    const fetchImpl: typeof fetch = vi.fn(async (input, init) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      calls.push({url, method})
      if (method === 'GET' && url.includes('/interactions/')) {
        return new Response(JSON.stringify({error: {code: 'not_found', message: 'missing'}}), {status: 404})
      }
      if (method === 'POST' && url.endsWith('/sessions')) {
        return new Response(JSON.stringify({result: {session: created}}), {status: 200})
      }
      if (method === 'POST' && url.endsWith('/interactions')) {
        return new Response(JSON.stringify({receipt: {client_message_id: 'cmd', interaction_id: 'int', status: 'accepted', revision: 1, queue_position: 1, agent_run_id: null, turn_id: null}}), {status: 200})
      }
      return new Response(JSON.stringify({}), {status: 200})
    })
    const client = new ApiClient({baseUrl: '', token: '', fetchImpl})
    const memory = storage()
    const onSelectSession = vi.fn()
    await act(async () => {
      root.render(<DraftSession
        client={client}
        workspace="ws_1"
        draftId="draft_test"
        capabilities={capabilities}
        drafts={new DraftStorage(memory)}
        onPersistSession={() => client.createChatSession('ws_1', commandId('chat_session'))}
        onSelectSession={onSelectSession}
        onSessionUpserted={() => {}}
      />)
    })

    expect(calls.some(call => call.method === 'POST' && call.url.endsWith('/sessions'))).toBe(false)
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('消息输入'), '第一条 Prompt')
    await user.click(screen.getByRole('button', {name: '发送'}))

    await waitFor(() => expect(onSelectSession).toHaveBeenCalledWith(created.session_id))
    const postUrls = calls.filter(call => call.method === 'POST').map(call => call.url)
    expect(postUrls.findIndex(url => url.endsWith('/sessions'))).toBeGreaterThanOrEqual(0)
    expect(postUrls.findIndex(url => url.endsWith('/interactions'))).toBeGreaterThan(
      postUrls.findIndex(url => url.endsWith('/sessions')),
    )
  })
})
