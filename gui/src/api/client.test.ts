import { describe, expect, it } from 'vitest'
import { ApiClient, ApiError } from './client'

interface RecordedRequest {
  url: string
  authorization: string | null
}

function scriptedFetch(
  reply: { status?: number; body?: unknown; headers?: Record<string, string> },
  recorded: RecordedRequest[] = [],
): { fetchImpl: typeof fetch; recorded: RecordedRequest[] } {
  const fetchImpl: typeof fetch = async (input, init) => {
    const headers = new Headers(init?.headers)
    recorded.push({ url: String(input), authorization: headers.get('authorization') })
    return new Response(JSON.stringify(reply.body ?? null), {
      status: reply.status ?? 200,
      headers: reply.headers,
    })
  }
  return { fetchImpl, recorded }
}

describe('ApiClient', () => {
  it('sends the bearer token on every call and builds relative URLs', async () => {
    const { fetchImpl, recorded } = scriptedFetch({
      body: { protocol_version: 1, workspace_id: 'ws_1', latest_cursor: 7 },
    })
    const client = new ApiClient({ baseUrl: '', token: 'secret-token', fetchImpl })

    const meta = await client.meta()

    expect(meta.workspace_id).toBe('ws_1')
    expect(recorded).toEqual([{ url: '/v1/meta', authorization: 'Bearer secret-token' }])
  })

  it('builds the events paging query with after/limit', async () => {
    const { fetchImpl, recorded } = scriptedFetch({
      body: { events: [], latest_cursor: 0, has_more: false },
    })
    const client = new ApiClient({ baseUrl: '', token: 't', fetchImpl })

    await client.events(41)
    await client.events(9, 25)

    expect(recorded.map((r) => r.url)).toEqual([
      '/v1/events?after=41&limit=100',
      '/v1/events?after=9&limit=25',
    ])
  })

  it('surfaces error bodies as ApiError with status and code', async () => {
    const { fetchImpl } = scriptedFetch({
      status: 401,
      body: { error: { code: 'unauthorized', message: 'invalid session token' } },
    })
    const client = new ApiClient({ baseUrl: '', token: 'bad', fetchImpl })

    const failure = await client.snapshot().catch((error: unknown) => error)

    expect(failure).toBeInstanceOf(ApiError)
    const apiError = failure as ApiError
    expect(apiError.status).toBe(401)
    expect(apiError.code).toBe('unauthorized')
    expect(apiError.message).toBe('invalid session token')
  })

  it('carries Retry-After on 503 busy responses', async () => {
    const { fetchImpl } = scriptedFetch({
      status: 503,
      body: { error: { code: 'busy', message: 'command queue is full' } },
      headers: { 'retry-after': '1' },
    })
    const client = new ApiClient({ baseUrl: '', token: 't', fetchImpl })

    const failure = (await client.listApprovals().catch((error: unknown) => error)) as ApiError

    expect(failure).toBeInstanceOf(ApiError)
    expect(failure.status).toBe(503)
    expect(failure.code).toBe('busy')
    expect(failure.retryAfterSeconds).toBe(1)
  })

  it('falls back to a generic ApiError for non-JSON error bodies', async () => {
    const fetchImpl: typeof fetch = async () =>
      new Response('gateway exploded', { status: 502, headers: { 'content-type': 'text/plain' } })
    const client = new ApiClient({ baseUrl: '', token: 't', fetchImpl })

    const failure = (await client.meta().catch((error: unknown) => error)) as ApiError

    expect(failure).toBeInstanceOf(ApiError)
    expect(failure.status).toBe(502)
    expect(failure.code).toBe('unknown')
  })

  it('unwraps detail envelopes for sessions, tasks, runs and nodes', async () => {
    const bodies: Record<string, unknown> = {
      '/v1/sessions/ses_1': { session: { session_id: 'ses_1' } },
      '/v1/tasks/task_1': { task: { task_run_id: 'task_1' } },
      '/v1/workflow-runs/wrun_1': { view: { run: { workflow_run_id: 'wrun_1' } } },
      '/v1/workflow-runs/wrun_1/nodes/nrun_1': { view: { node: { node_run_id: 'nrun_1' } } },
    }
    const fetchImpl: typeof fetch = async (input) => {
      const body = bodies[String(input)]
      return new Response(JSON.stringify(body), { status: body === undefined ? 404 : 200 })
    }
    const client = new ApiClient({ baseUrl: '', token: 't', fetchImpl })

    await expect(client.getSession('ses_1')).resolves.toEqual({ session_id: 'ses_1' })
    await expect(client.getTask('task_1')).resolves.toEqual({ task_run_id: 'task_1' })
    await expect(client.getRunView('wrun_1')).resolves.toEqual({
      run: { workflow_run_id: 'wrun_1' },
    })
    await expect(client.getNodeView('wrun_1', 'nrun_1')).resolves.toEqual({
      node: { node_run_id: 'nrun_1' },
    })
  })

  it('sends authenticated JSON mutations through POST and PUT', async () => {
    const requests: Array<{ method: string; contentType: string | null; body: unknown }> = []
    const fetchImpl: typeof fetch = async (_input, init) => {
      const headers = new Headers(init?.headers)
      requests.push({
        method: init?.method ?? 'GET',
        contentType: headers.get('content-type'),
        body: JSON.parse(String(init?.body)),
      })
      return new Response(
        JSON.stringify({ result: { workflow_draft: { draft: { row_version: 2 } } } }),
      )
    }
    const client = new ApiClient({ baseUrl: '', token: 'editor-token', fetchImpl })
    const source = { workflow_definition_id: 'flow' } as never

    await client.createWorkflowDraft(source, 1, 'cmd_create', 'draft_1')
    await client.updateWorkflowDraft('draft_1', source, 1, 'cmd_update')

    expect(requests).toEqual([
      {
        method: 'POST',
        contentType: 'application/json',
        body: {
          command_id: 'cmd_create',
          draft_id: 'draft_1',
          source,
          expected_source_revision: 1,
        },
      },
      {
        method: 'PUT',
        contentType: 'application/json',
        body: {
          command_id: 'cmd_update',
          source,
          expected_row_version: 1,
        },
      },
    ])
  })
})
