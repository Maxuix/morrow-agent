import { beforeEach, describe, expect, it } from 'vitest'
import { ApiClient } from '../api/client'
import type {
  ApprovalWire,
  EventWire,
  NodeViewWire,
  RunViewWire,
  SessionWire,
  TaskRunWire,
  WorkflowRunWire,
} from '../api/types'
import { SyncStore } from './sync'
import type { Sleep, WebSocketFactory, WebSocketLike } from './sync'

// Fakes -------------------------------------------------------------------------

interface FakeReply {
  status?: number
  body?: unknown
  headers?: Record<string, string>
}

type Handler = (url: URL) => FakeReply | Promise<FakeReply>

/** Scripted fetch: routes by pathname, records every request. */
class FakeCore {
  readonly urls: string[] = []
  readonly counts = new Map<string, number>()
  private readonly handlers = new Map<string, Handler>()

  constructor() {
    this.on('/v1/sessions', () => ({ body: { sessions: [], next_cursor: null } }))
    this.on('/v1/events', () => ({ body: { events: [], latest_cursor: 0, has_more: false } }))
    this.on('/v1/approvals', () => ({ body: { approvals: [] } }))
  }

  on(path: string, handler: Handler): this {
    this.handlers.set(path, handler)
    return this
  }

  count(path: string): number {
    return this.counts.get(path) ?? 0
  }

  readonly fetchImpl: typeof fetch = async (input) => {
    const url = new URL(String(input), 'http://morrow.test')
    this.urls.push(url.pathname + url.search)
    this.counts.set(url.pathname, this.count(url.pathname) + 1)
    const handler = this.handlers.get(url.pathname)
    if (!handler) {
      return new Response(
        JSON.stringify({ error: { code: 'not_found', message: `no route: ${url.pathname}` } }),
        { status: 404 },
      )
    }
    const reply = await handler(url)
    return new Response(JSON.stringify(reply.body ?? null), {
      status: reply.status ?? 200,
      headers: reply.headers,
    })
  }
}

class FakeWebSocket implements WebSocketLike {
  static instances: FakeWebSocket[] = []

  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  closed = false

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this)
  }

  close(): void {
    this.closed = true
  }

  serverSend(message: unknown): void {
    this.onmessage?.({ data: JSON.stringify(message) })
  }

  serverClose(): void {
    this.onclose?.()
  }
}

// Fixtures ------------------------------------------------------------------------

const T0 = '2026-09-03T00:00:00Z'

function makeRun(overrides: Partial<WorkflowRunWire> = {}): WorkflowRunWire {
  return {
    workflow_run_id: 'wrun_1',
    workflow_revision_id: 'wrev_1',
    root_task_run_id: 'task_1',
    status: 'queued',
    row_version: 1,
    started_at: null,
    completed_at: null,
    budget_snapshot: {
      max_agent_generation_requests: 100,
      default_node_max_agent_generation_requests: 10,
      admission_timeout_seconds: 60,
      max_concurrency: 2,
    },
    admission_deadline_at: '2026-09-03T00:01:00Z',
    input_artifacts: [
      { name: 'task', artifact_id: 'art_1', contract: { kind: 'TaskContract', version: 1 } },
    ],
    result_status: null,
    pending_terminal_intent: null,
    pause_requested: false,
    run_relation: 'initial',
    lineage_budget_root_run_id: null,
    parent_run_id: null,
    superseded_reason: null,
    ...overrides,
  }
}

function makeNodeView(overrides: Partial<NodeViewWire['node']> = {}): NodeViewWire {
  return {
    node: {
      node_run_id: 'nrun_1',
      workflow_run_id: 'wrun_1',
      node_id: 'plan',
      status: 'queued',
      attempt: 1,
      row_version: 1,
      started_at: null,
      completed_at: null,
      conversation_session_id: null,
      leaf_task_run_id: null,
      agent_run_id: null,
      effective_node_generation_request_cap: null,
      ...overrides,
    },
    output_bindings: [],
    artifacts: [],
    approval_pending: false,
  }
}

function makeRunView(run: WorkflowRunWire, nodes: NodeViewWire[] = []): RunViewWire {
  return {
    run,
    revision: {},
    nodes,
    input_artifacts: [],
    agent_generation_request_count: 0,
    lineage_agent_generation_request_count: 0,
    inherited_artifacts: [],
    effective_outputs: [],
    usage_availability: 'unavailable',
    terminal_outcome: null,
    actionable_status: null,
  }
}

function makeSession(id = 'ses_1'): SessionWire {
  return {
    session_id: id,
    lifecycle: 'active',
    health: 'ok',
    created_at: T0,
    updated_at: T0,
    current_task_run_id: null,
    parent_session_id: null,
  }
}

function makeTask(overrides: Partial<TaskRunWire> = {}): TaskRunWire {
  return {
    task_run_id: 'task_1',
    session_id: 'ses_1',
    purpose: 'user',
    status: 'open',
    attempt: 1,
    row_version: 1,
    created_at: T0,
    updated_at: T0,
    accepted_at: null,
    closed_at: null,
    ...overrides,
  }
}

function makeApproval(id = 'ap_1'): ApprovalWire {
  return {
    approval_id: id,
    tool_execution_id: 'tex_1',
    tool_name: 'shell',
    session_id: 'ses_1',
    task_run_id: 'task_1',
    agent_run_id: 'arun_1',
    requested_scope: 'workspace-write',
    preview: ['$ pnpm test'],
    resolution: 'pending',
    created_at: T0,
    expires_at: '2026-09-03T01:00:00Z',
    resolved_at: null,
    row_version: 1,
  }
}

function makeEvent(
  cursor: number,
  eventType: string,
  aggregateKind: string,
  aggregateId: string,
  payload: Record<string, unknown>,
  eventId?: string,
): EventWire {
  return {
    cursor,
    event_id: eventId ?? `evt_${cursor}`,
    event_type: eventType,
    aggregate_kind: aggregateKind,
    aggregate_id: aggregateId,
    payload,
    created_at: `2026-09-03T00:00:${String(cursor).padStart(2, '0')}Z`,
  }
}

// Helpers ----------------------------------------------------------------------------

const instantSleep: Sleep = async () => {}

function makeStore(core: FakeCore, options: { sleep?: Sleep } = {}): SyncStore {
  const client = new ApiClient({ baseUrl: '', token: 'tok', fetchImpl: core.fetchImpl })
  const wsFactory: WebSocketFactory = (url) => new FakeWebSocket(url)
  return new SyncStore({
    client,
    token: 'tok',
    wsFactory,
    sleep: options.sleep ?? instantSleep,
  })
}

/** Let every queued microtask/macrotask chain settle; no wall-clock assertion. */
async function flush(rounds = 20): Promise<void> {
  for (let index = 0; index < rounds; index += 1) {
    await new Promise<void>((resolve) => setTimeout(resolve, 0))
  }
}

function lastSocket(): FakeWebSocket {
  const socket = FakeWebSocket.instances.at(-1)
  if (!socket) throw new Error('no websocket was opened')
  return socket
}

beforeEach(() => {
  FakeWebSocket.instances = []
})

// Tests -------------------------------------------------------------------------------

describe('SyncStore', () => {
  it('boots from a snapshot: anchors the cursor, populates runs/approvals, goes live on hello', async () => {
    const core = new FakeCore()
    core
      .on('/v1/snapshot', () => ({
        body: { cursor: 5, workflow_runs: [makeRun()], pending_approvals: [makeApproval()] },
      }))
      .on('/v1/sessions', () => ({ body: { sessions: [makeSession()], next_cursor: null } }))
    const store = makeStore(core)

    await store.start()

    const state = store.getState()
    expect(state.connection).toBe('connecting')
    expect(state.cursor).toBe(5)
    expect(state.workflowRuns.get('wrun_1')?.run.status).toBe('queued')
    expect(state.workflowRuns.get('wrun_1')?.view).toBeNull()
    expect(state.pendingApprovals.get('ap_1')?.tool_name).toBe('shell')
    expect(state.sessions.get('ses_1')?.lifecycle).toBe('active')
    expect(core.urls).toContain('/v1/sessions?limit=100')

    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(lastSocket().url).toBe('/v1/events/stream?token=tok')

    lastSocket().serverSend({ type: 'hello', latest_cursor: 5 })
    await flush()
    expect(store.getState().connection).toBe('live')
  })

  it('pulls on a cursor hint and applies events in order, tolerating cursor gaps', async () => {
    const core = new FakeCore()
    core.on('/v1/snapshot', () => ({
      body: { cursor: 0, workflow_runs: [makeRun()], pending_approvals: [] },
    }))
    // First event jumps to cursor 5: pages are authoritative, gaps are fine.
    core.on('/v1/events', () => ({
      body: {
        events: [
          makeEvent(5, 'session.created', 'session', 'ses_9', {
            lifecycle: 'active',
            health: 'ok',
          }),
          makeEvent(6, 'workflow_run.status_changed', 'workflow_run', 'wrun_1', {
            status: 'running',
            pause_requested: false,
            result_status: null,
            pending_terminal_intent: null,
            row_version: 2,
          }),
        ],
        latest_cursor: 6,
        has_more: false,
      },
    }))
    let statusWhenSessionFetched: string | undefined
    core.on('/v1/sessions/ses_9', () => {
      statusWhenSessionFetched = store.getState().workflowRuns.get('wrun_1')?.run.status
      return { body: { session: makeSession('ses_9') } }
    })
    const store = makeStore(core)
    await store.start()

    lastSocket().serverSend({ type: 'hello', latest_cursor: 6 })
    await flush()

    const state = store.getState()
    // Ordering proof: the session fetch ran before the status patch landed.
    expect(statusWhenSessionFetched).toBe('queued')
    expect(state.sessions.get('ses_9')?.health).toBe('ok')
    expect(state.workflowRuns.get('wrun_1')?.run.status).toBe('running')
    expect(state.workflowRuns.get('wrun_1')?.run.row_version).toBe(2)
    expect(state.cursor).toBe(6)
    expect(state.lastEventAt).toBe('2026-09-03T00:00:06Z')
    expect(state.connection).toBe('live')
  })

  it('drains every page of a has_more sequence before going live', async () => {
    const core = new FakeCore()
    core.on('/v1/snapshot', () => ({
      body: { cursor: 0, workflow_runs: [makeRun()], pending_approvals: [] },
    }))
    let connectionDuringSecondPage: string | undefined
    core.on('/v1/events', (url) => {
      const after = Number(url.searchParams.get('after'))
      if (after === 0) {
        return {
          body: {
            events: [
              makeEvent(1, 'workflow_run.status_changed', 'workflow_run', 'wrun_1', {
                status: 'running',
                row_version: 2,
              }),
            ],
            latest_cursor: 2,
            has_more: true,
          },
        }
      }
      connectionDuringSecondPage = store.getState().connection
      return {
        body: {
          events: [
            makeEvent(2, 'workflow_run.status_changed', 'workflow_run', 'wrun_1', {
              status: 'completed',
              result_status: 'succeeded',
              row_version: 3,
            }),
          ],
          latest_cursor: 2,
          has_more: false,
        },
      }
    })
    const store = makeStore(core)
    await store.start()

    lastSocket().serverSend({ type: 'cursor', latest_cursor: 2 })
    await flush()

    const state = store.getState()
    expect(core.count('/v1/events')).toBe(2)
    expect(core.urls).toContain('/v1/events?after=0&limit=100')
    expect(core.urls).toContain('/v1/events?after=1&limit=100')
    expect(connectionDuringSecondPage).toBe('connecting')
    expect(state.workflowRuns.get('wrun_1')?.run.status).toBe('completed')
    expect(state.workflowRuns.get('wrun_1')?.run.result_status).toBe('succeeded')
    expect(state.cursor).toBe(2)
    expect(state.connection).toBe('live')
  })

  it('deduplicates repeated delivery by event_id while still advancing the cursor', async () => {
    const core = new FakeCore()
    core.on('/v1/snapshot', () => ({ body: { cursor: 0, workflow_runs: [], pending_approvals: [] } }))
    core.on('/v1/events', () => ({
      body: {
        events: [
          makeEvent(1, 'task.created', 'task', 'task_1', { status: 'open', row_version: 1 }, 'evt_dup'),
          makeEvent(2, 'task.created', 'task', 'task_1', { status: 'open', row_version: 1 }, 'evt_dup'),
        ],
        latest_cursor: 2,
        has_more: false,
      },
    }))
    core.on('/v1/tasks/task_1', () => ({ body: { task: makeTask() } }))
    const store = makeStore(core)
    await store.start()

    lastSocket().serverSend({ type: 'hello', latest_cursor: 2 })
    await flush()

    expect(core.count('/v1/tasks/task_1')).toBe(1)
    expect(store.getState().tasks.size).toBe(1)
    expect(store.getState().tasks.get('task_1')?.status).toBe('open')
    expect(store.getState().cursor).toBe(2)
  })

  it('fetches run views on workflow_run.created, patches node statuses, refetches unknown runs', async () => {
    const core = new FakeCore()
    core.on('/v1/snapshot', () => ({ body: { cursor: 0, workflow_runs: [], pending_approvals: [] } }))
    core.on('/v1/events', () => ({
      body: {
        events: [
          makeEvent(1, 'workflow_run.created', 'workflow_run', 'wrun_1', {
            workflow_revision_id: 'wrev_1',
            run_relation: 'initial',
            parent_run_id: null,
            relation: 'start',
          }),
          makeEvent(2, 'workflow_node.status_changed', 'workflow_node', 'nrun_1', {
            workflow_run_id: 'wrun_1',
            node_id: 'plan',
            status: 'running',
            row_version: 2,
          }),
          makeEvent(3, 'workflow_node.status_changed', 'workflow_node', 'nrun_9', {
            workflow_run_id: 'wrun_9',
            node_id: 'build',
            status: 'running',
            row_version: 1,
          }),
        ],
        latest_cursor: 3,
        has_more: false,
      },
    }))
    core.on('/v1/workflow-runs/wrun_1', () => ({
      body: { view: makeRunView(makeRun({ status: 'running', row_version: 2 }), [makeNodeView()]) },
    }))
    core.on('/v1/workflow-runs/wrun_9', () => ({
      body: {
        view: makeRunView(makeRun({ workflow_run_id: 'wrun_9', status: 'running' }), [
          makeNodeView({ node_run_id: 'nrun_9', workflow_run_id: 'wrun_9', node_id: 'build' }),
        ]),
      },
    }))
    const store = makeStore(core)
    await store.start()

    lastSocket().serverSend({ type: 'hello', latest_cursor: 3 })
    await flush()

    const state = store.getState()
    // workflow_run.created loaded the full view (payload carries lineage facts only).
    const projection = state.workflowRuns.get('wrun_1')
    expect(core.count('/v1/workflow-runs/wrun_1')).toBe(1)
    expect(projection?.view?.run.status).toBe('running')
    // The node event patched the node inside the loaded view in place.
    expect(projection?.view?.nodes[0]?.node.status).toBe('running')
    expect(projection?.view?.nodes[0]?.node.row_version).toBe(2)
    expect(projection?.run).toBe(projection?.view?.run)
    // A node event for an unknown run triggered a full run-view refetch.
    expect(core.count('/v1/workflow-runs/wrun_9')).toBe(1)
    expect(state.workflowRuns.get('wrun_9')?.view?.nodes[0]?.node.node_id).toBe('build')
  })

  it('refreshes pending approvals when approval.requested arrives', async () => {
    const core = new FakeCore()
    core.on('/v1/snapshot', () => ({ body: { cursor: 0, workflow_runs: [], pending_approvals: [] } }))
    core.on('/v1/events', () => ({
      body: {
        events: [
          makeEvent(1, 'approval.requested', 'approval', 'ap_1', {
            effect: 'workspace_write',
            reason_codes: ['write'],
            preview_line_count: 1,
          }),
        ],
        latest_cursor: 1,
        has_more: false,
      },
    }))
    core.on('/v1/approvals', () => ({ body: { approvals: [makeApproval()] } }))
    const store = makeStore(core)
    await store.start()

    lastSocket().serverSend({ type: 'hello', latest_cursor: 1 })
    await flush()

    expect(core.urls).toContain('/v1/approvals?pending=true')
    expect(store.getState().pendingApprovals.get('ap_1')?.resolution).toBe('pending')
  })

  it('clears resolved approvals on run/node lifecycle events (no approval.resolved event exists)', async () => {
    const core = new FakeCore()
    core.on('/v1/snapshot', () => ({
      body: { cursor: 0, workflow_runs: [makeRun()], pending_approvals: [makeApproval()] },
    }))
    // The approval was resolved server-side; the next lifecycle event must
    // refresh the pending list, since no approval.resolved event exists.
    core.on('/v1/approvals', () => ({ body: { approvals: [] } }))
    core.on('/v1/events', () => ({
      body: {
        events: [
          makeEvent(1, 'workflow_run.status_changed', 'workflow_run', 'wrun_1', {
            status: 'running',
            row_version: 2,
          }),
        ],
        latest_cursor: 1,
        has_more: false,
      },
    }))
    const store = makeStore(core)
    await store.start()
    expect(store.getState().pendingApprovals.size).toBe(1)

    lastSocket().serverSend({ type: 'cursor', latest_cursor: 1 })
    await flush()

    expect(core.urls).toContain('/v1/approvals?pending=true')
    expect(store.getState().pendingApprovals.size).toBe(0)
    expect(store.getState().connection).toBe('live')
  })

  it('resyncs from scratch after a forced close: no lost and no duplicated state', async () => {
    const core = new FakeCore()
    let serverEpoch = 1
    core.on('/v1/snapshot', () =>
      serverEpoch === 1
        ? {
            body: {
              cursor: 2,
              workflow_runs: [makeRun({ status: 'running', row_version: 1 })],
              pending_approvals: [makeApproval()],
            },
          }
        : {
            body: {
              cursor: 3,
              workflow_runs: [
                makeRun({ status: 'completed', row_version: 2, result_status: 'succeeded' }),
              ],
              pending_approvals: [],
            },
          },
    )
    core.on('/v1/sessions', () => ({ body: { sessions: [makeSession()], next_cursor: null } }))
    core.on('/v1/events', (url) => {
      const after = Number(url.searchParams.get('after'))
      if (after === 3) {
        return {
          body: {
            events: [
              makeEvent(4, 'session.created', 'session', 'ses_2', {
                lifecycle: 'active',
                health: 'ok',
              }),
            ],
            latest_cursor: 4,
            has_more: false,
          },
        }
      }
      return { body: { events: [], latest_cursor: 3, has_more: false } }
    })
    core.on('/v1/sessions/ses_2', () => ({ body: { session: makeSession('ses_2') } }))
    const store = makeStore(core)
    const connections: string[] = []
    store.subscribe(() => connections.push(store.getState().connection))
    await store.start()
    lastSocket().serverSend({ type: 'hello', latest_cursor: 2 })
    await flush()
    expect(store.getState().connection).toBe('live')

    // The socket dies while cursor 3 lands unseen; epoch 2 is the new truth.
    serverEpoch = 2
    lastSocket().serverClose()
    await flush()

    const state = store.getState()
    expect(connections).toContain('reconnecting')
    expect(connections.at(-1)).toBe('live')
    expect(FakeWebSocket.instances).toHaveLength(2)
    expect(core.count('/v1/snapshot')).toBe(2)
    // Final projection equals a from-scratch snapshot at epoch 2, plus the
    // durable remainder pulled after the new anchor.
    expect(state.workflowRuns.size).toBe(1)
    expect(state.workflowRuns.get('wrun_1')?.run).toEqual(
      makeRun({ status: 'completed', row_version: 2, result_status: 'succeeded' }),
    )
    expect(state.pendingApprovals.size).toBe(0)
    expect(state.sessions.get('ses_1')?.lifecycle).toBe('active')
    expect(state.sessions.get('ses_2')?.lifecycle).toBe('active')
    expect(state.cursor).toBe(4)
  })

  it('backs off exponentially, goes offline after repeated failures, and recovers on manual retry', async () => {
    const core = new FakeCore()
    let up = false
    core.on('/v1/snapshot', () => {
      if (!up) throw new TypeError('network down')
      return { body: { cursor: 0, workflow_runs: [], pending_approvals: [] } }
    })
    const sleeps: number[] = []
    const sleep: Sleep = async (ms) => {
      sleeps.push(ms)
    }
    const store = makeStore(core, { sleep })

    await store.start()
    await flush()

    expect(store.getState().connection).toBe('offline')
    expect(sleeps).toEqual([1000, 2000, 4000, 8000, 15000])
    expect(core.count('/v1/snapshot')).toBe(6) // boot + 5 reconnect attempts
    expect(FakeWebSocket.instances).toHaveLength(0)

    up = true
    store.retry()
    await flush()

    expect(store.getState().connection).toBe('live')
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(store.getState().cursor).toBe(0)
  })

  it('classifies an invalid session token without retrying or opening a websocket', async () => {
    const core = new FakeCore()
    core.on('/v1/snapshot', () => ({
      status: 401,
      body: { error: { code: 'unauthorized', message: 'invalid session token' } },
    }))
    const sleeps: number[] = []
    const store = makeStore(core, {
      sleep: async (ms) => {
        sleeps.push(ms)
      },
    })

    await store.start()
    await flush()

    expect(store.getState().connection).toBe('unauthorized')
    expect(core.count('/v1/snapshot')).toBe(1)
    expect(sleeps).toEqual([])
    expect(FakeWebSocket.instances).toHaveLength(0)
  })
})
