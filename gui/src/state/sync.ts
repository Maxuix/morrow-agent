/**
 * Snapshot + event-stream sync engine, mirroring the reference consumer in
 * `tests/fixtures/core_api_client.py`:
 *
 * - boot anchors one consistent `GET /v1/snapshot` cursor, then opens the
 *   WebSocket hint stream at `/v1/events/stream?token=`;
 * - WS `hello`/`cursor`/`ping` messages carry no facts — a `latest_cursor`
 *   ahead of ours only schedules a durable pull;
 * - pulls page `GET /v1/events?after=<cursor>&limit=100` until `has_more` is
 *   false, advance the cursor per event, dedupe by `event_id`, and skip
 *   events at or below the anchor (a page whose first event is ahead of
 *   `cursor + 1` is fine — pages are authoritative);
 * - any failure or WS close moves to `reconnecting` with capped exponential
 *   backoff, and a successful reconnect runs a full resync (fresh snapshot +
 *   remainder pull) rather than trusting the old maps; after
 *   `maxReconnectAttempts` failures the store goes `offline` until
 *   `retry()` is called. A typed `401/unauthorized` is permanent for the
 *   current page token, so it enters `unauthorized` immediately without
 *   backoff.
 *
 * The store is framework-agnostic: plain class, `subscribe`/`getState`
 * shaped for a later `useSyncExternalStore` binding. Timers and the WebSocket
 * factory are injected, so tests never touch wall-clock or real sockets.
 *
 * Created-event payloads are deliberately partial on the wire
 * (`session.created` carries only `{lifecycle, health}`, `task.created` only
 * `{status, row_version}`, `workflow_run.created` only lineage facts), so the
 * store fetches the detail endpoint for each. Status events patch in place.
 */
import { ApiError, type ApiClient } from '../api/client'
import type {
  ApprovalWire,
  EventWire,
  RunViewWire,
  SessionWire,
  StreamHintWire,
  TaskRunWire,
  WorkflowRunWire,
  WorkflowStatus,
} from '../api/types'

export type ConnectionState = 'connecting' | 'live' | 'reconnecting' | 'offline' | 'unauthorized'

export interface WorkflowRunProjection {
  run: WorkflowRunWire
  /** Full run view, loaded on `workflow_run.created` or on-demand refetch. */
  view: RunViewWire | null
}

export interface SyncState {
  connection: ConnectionState
  cursor: number
  sessions: Map<string, SessionWire>
  tasks: Map<string, TaskRunWire>
  workflowRuns: Map<string, WorkflowRunProjection>
  pendingApprovals: Map<string, ApprovalWire>
  lastEventAt: string | null
}

/** Minimal structural slice of the DOM WebSocket the store relies on. */
export interface WebSocketLike {
  onmessage: ((event: { data: string }) => void) | null
  onclose: (() => void) | null
  close(): void
}

export type WebSocketFactory = (url: string) => WebSocketLike
export type Sleep = (ms: number) => Promise<void>

export interface SyncStoreOptions {
  client: ApiClient
  token: string
  wsFactory?: WebSocketFactory
  sleep?: Sleep
  maxReconnectAttempts?: number
  backoffMs?: (attempt: number) => number
}

const DEFAULT_MAX_RECONNECT_ATTEMPTS = 5
const MAX_BACKOFF_MS = 15_000

export function defaultBackoffMs(attempt: number): number {
  return Math.min(1000 * 2 ** (attempt - 1), MAX_BACKOFF_MS)
}

const defaultSleep: Sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

// The DOM WebSocket's `onmessage` is contravariantly incompatible with the
// store's minimal handler type; the structural slice is all the store needs.
const defaultWsFactory: WebSocketFactory = (url) => new WebSocket(url) as WebSocketLike

export class SyncStore {
  private state: SyncState = {
    connection: 'connecting',
    cursor: 0,
    sessions: new Map(),
    tasks: new Map(),
    workflowRuns: new Map(),
    pendingApprovals: new Map(),
    lastEventAt: null,
  }

  private readonly client: ApiClient
  private readonly token: string
  private readonly wsFactory: WebSocketFactory
  private readonly sleep: Sleep
  private readonly maxReconnectAttempts: number
  private readonly backoffMs: (attempt: number) => number

  private readonly listeners = new Set<() => void>()
  private readonly seenEventIds = new Set<string>()
  private pullChain: Promise<void> = Promise.resolve()
  private ws: WebSocketLike | null = null
  private attempts = 0
  private started = false
  private stopped = false
  private recovering = false

  constructor(options: SyncStoreOptions) {
    this.client = options.client
    this.token = options.token
    this.wsFactory = options.wsFactory ?? defaultWsFactory
    this.sleep = options.sleep ?? defaultSleep
    this.maxReconnectAttempts = options.maxReconnectAttempts ?? DEFAULT_MAX_RECONNECT_ATTEMPTS
    this.backoffMs = options.backoffMs ?? defaultBackoffMs
  }

  getState(): SyncState {
    return this.state
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  /** Boot: snapshot → replace maps, anchor the cursor, open the hint stream. */
  async start(): Promise<void> {
    if (this.started) return
    this.started = true
    this.stopped = false
    this.patch({ connection: 'connecting' })
    try {
      await this.loadSnapshot()
      this.openSocket()
    } catch (error) {
      this.handleSyncFailure(error)
    }
  }

  /** Manual recovery from `offline`; resets the backoff budget. */
  retry(): void {
    if (this.state.connection !== 'offline') return
    this.attempts = 0
    this.handleSyncFailure()
  }

  stop(): void {
    this.stopped = true
    this.closeSocket()
  }

  // Snapshot / resync ---------------------------------------------------------

  private async loadSnapshot(): Promise<void> {
    const snapshot = await this.client.snapshot()
    const workflowRuns = new Map<string, WorkflowRunProjection>()
    for (const run of snapshot.workflow_runs) {
      workflowRuns.set(run.workflow_run_id, { run, view: null })
    }
    const pendingApprovals = new Map<string, ApprovalWire>()
    for (const approval of snapshot.pending_approvals) {
      pendingApprovals.set(approval.approval_id, approval)
    }
    // The snapshot carries no sessions/tasks; repopulate the session index
    // from the list endpoint (first page) and let live events fill the rest.
    const sessions = new Map<string, SessionWire>()
    const sessionsPage = await this.client.listSessions({ limit: 100 })
    for (const session of sessionsPage.sessions) {
      sessions.set(session.session_id, session)
    }
    this.state = {
      ...this.state,
      cursor: snapshot.cursor,
      workflowRuns,
      pendingApprovals,
      sessions,
      tasks: new Map(),
    }
    this.emit()
  }

  /** Gap recovery: fresh snapshot re-anchor, then drain the remainder. */
  private async resync(): Promise<void> {
    await this.loadSnapshot()
    await this.drainEvents()
  }

  // Event stream ---------------------------------------------------------------

  private wsUrl(): string {
    const path = `/v1/events/stream?token=${encodeURIComponent(this.token)}`
    if (typeof window !== 'undefined' && window.location) {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      return `${protocol}//${window.location.host}${path}`
    }
    return path
  }

  private openSocket(): void {
    this.closeSocket()
    const ws = this.wsFactory(this.wsUrl())
    this.ws = ws
    ws.onmessage = (event) => this.handleHint(event.data)
    ws.onclose = () => {
      this.ws = null
      this.handleSyncFailure()
    }
  }

  private closeSocket(): void {
    const ws = this.ws
    this.ws = null
    if (ws) {
      ws.onmessage = null
      ws.onclose = null
      try {
        ws.close()
      } catch {
        // A half-open socket may throw on close; the projection is unaffected.
      }
    }
  }

  private handleHint(data: string): void {
    let hint: StreamHintWire
    try {
      hint = JSON.parse(data) as StreamHintWire
    } catch {
      return
    }
    if (hint.type !== 'hello' && hint.type !== 'cursor' && hint.type !== 'ping') return
    if (typeof hint.latest_cursor !== 'number') return
    if (hint.latest_cursor > this.state.cursor) {
      this.schedulePull()
    } else if (this.state.connection === 'connecting') {
      // Caught up at the anchor and the stream is open: we are live.
      this.patch({ connection: 'live' })
    }
  }

  /** Serialize pulls so events always apply in cursor order. */
  private schedulePull(): void {
    this.pullChain = this.pullChain
      .then(async () => {
        if (this.stopped || this.recovering) return
        await this.drainEvents()
        if (!this.stopped && !this.recovering && this.ws !== null) {
          this.patch({ connection: 'live' })
        }
      })
      .catch((error: unknown) => this.handleSyncFailure(error))
  }

  private async drainEvents(): Promise<void> {
    while (true) {
      const page = await this.client.events(this.state.cursor)
      for (const event of page.events) {
        if (event.cursor <= this.state.cursor) continue
        if (!this.seenEventIds.has(event.event_id)) {
          this.seenEventIds.add(event.event_id)
          await this.applyEvent(event)
        }
        this.commit(event)
      }
      if (!page.has_more) break
    }
  }

  private async applyEvent(event: EventWire): Promise<void> {
    switch (event.event_type) {
      case 'session.created': {
        // Payload is only {lifecycle, health}; fetch the full row.
        const session = await this.client.getSession(event.aggregate_id)
        this.state.sessions.set(session.session_id, session)
        break
      }
      case 'task.created': {
        // Payload is only {status, row_version}; fetch the full row.
        const task = await this.client.getTask(event.aggregate_id)
        this.state.tasks.set(task.task_run_id, task)
        break
      }
      case 'workflow_run.created': {
        // Payload is only lineage facts; fetch the full run view.
        await this.refreshRun(event.aggregate_id)
        break
      }
      case 'workflow_run.status_changed': {
        const projection = this.state.workflowRuns.get(event.aggregate_id)
        if (projection === undefined) {
          await this.refreshRun(event.aggregate_id)
        } else {
          patchRun(projection.run, event.payload)
        }
        // No approval.resolved event exists; a resolved approval only shows up
        // as run/node progress, so lifecycle events refresh the pending list.
        await this.refreshApprovals()
        break
      }
      case 'workflow_node.status_changed': {
        const runId = event.payload.workflow_run_id
        const projection =
          typeof runId === 'string' ? this.state.workflowRuns.get(runId) : undefined
        if (typeof runId !== 'string') break
        if (projection === undefined) {
          // Node event for an unknown run: refetch the whole view.
          await this.refreshRun(runId)
          break
        }
        const nodeView = projection.view?.nodes.find(
          (item) => item.node.node_run_id === event.aggregate_id,
        )
        if (projection.view !== null && nodeView === undefined) {
          // Unknown node (e.g. a patch extended the graph): refetch the view.
          await this.refreshRun(runId)
          break
        }
        if (nodeView !== undefined) {
          if (typeof event.payload.status === 'string') {
            nodeView.node.status = event.payload.status as WorkflowStatus
          }
          if (typeof event.payload.row_version === 'number') {
            nodeView.node.row_version = event.payload.row_version
          }
        }
        // See workflow_run.status_changed: node progress is the signal that a
        // resolved approval left the pending set.
        await this.refreshApprovals()
        break
      }
      case 'approval.requested': {
        // The event is only a pull hint ({effect, reason_codes,
        // preview_line_count}); the approvals query owns the bounded preview.
        await this.refreshApprovals()
        break
      }
      default:
        // The stream is additive; unknown event types are ignored.
        break
    }
  }

  private async refreshRun(runId: string): Promise<void> {
    const view = await this.client.getRunView(runId)
    this.state.workflowRuns.set(runId, { run: view.run, view })
  }

  private async refreshApprovals(): Promise<void> {
    const approvals = await this.client.listApprovals(true)
    this.state.pendingApprovals = new Map(
      approvals.map((approval) => [approval.approval_id, approval]),
    )
  }

  // Failure / reconnect ---------------------------------------------------------

  private handleSyncFailure(error?: unknown): void {
    if (this.stopped || this.recovering) return
    if (isUnauthorized(error)) {
      this.closeSocket()
      this.patch({ connection: 'unauthorized' })
      return
    }
    this.recovering = true
    this.closeSocket()
    this.patch({ connection: 'reconnecting' })
    void this.reconnectLoop()
  }

  private async reconnectLoop(): Promise<void> {
    try {
      while (!this.stopped) {
        if (this.attempts >= this.maxReconnectAttempts) {
          this.patch({ connection: 'offline' })
          return
        }
        this.attempts += 1
        await this.sleep(this.backoffMs(this.attempts))
        if (this.stopped) return
        try {
          await this.resync()
        } catch (error) {
          if (isUnauthorized(error)) {
            this.patch({ connection: 'unauthorized' })
            return
          }
          continue
        }
        this.attempts = 0
        this.recovering = false
        this.openSocket()
        this.patch({ connection: 'live' })
        return
      }
    } finally {
      // `recovering` is cleared on success above; on stop/offline the store no
      // longer accepts hint-driven pulls until `retry()` runs.
      if (
        this.state.connection === 'live' ||
        this.state.connection === 'offline' ||
        this.state.connection === 'unauthorized'
      ) {
        this.recovering = false
      }
    }
  }

  // State plumbing ----------------------------------------------------------------

  /** Advance the cursor anchor per event, including deduplicated replays. */
  private commit(event: EventWire): void {
    this.state = { ...this.state, cursor: event.cursor, lastEventAt: event.created_at }
    this.emit()
  }

  private patch(partial: Partial<SyncState>): void {
    this.state = { ...this.state, ...partial }
    this.emit()
  }

  private emit(): void {
    for (const listener of this.listeners) listener()
  }
}

function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401 && error.code === 'unauthorized'
}

function patchRun(run: WorkflowRunWire, payload: Record<string, unknown>): void {
  if (typeof payload.status === 'string') run.status = payload.status as WorkflowStatus
  if (typeof payload.row_version === 'number') run.row_version = payload.row_version
  if (typeof payload.pause_requested === 'boolean') run.pause_requested = payload.pause_requested
  if ('result_status' in payload) {
    run.result_status = payload.result_status as WorkflowRunWire['result_status']
  }
  if ('pending_terminal_intent' in payload) {
    run.pending_terminal_intent =
      payload.pending_terminal_intent as WorkflowRunWire['pending_terminal_intent']
  }
  if (typeof payload.superseded_reason === 'string') {
    run.superseded_reason = payload.superseded_reason as WorkflowRunWire['superseded_reason']
  }
}
