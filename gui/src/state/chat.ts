import { ApiError, type ApiClient } from '../api/client'
import type { ChatSnapshot, ChatFrame, StageFrame, TimelineItem } from '../api/chat'

export const MAX_HISTORY_ITEMS = 500
export const MAX_HISTORY_BYTES = 4 * 1024 * 1024

export function boundedItems(items: TimelineItem[], older = false): TimelineItem[] {
  const map = new Map(items.map(item => [item.item_id, item]))
  const ordered = [...map.values()].sort((a, b) => a.order_key[0] - b.order_key[0] || a.item_id.localeCompare(b.item_id))
  const kept: TimelineItem[] = []; let bytes = 0
  for (const item of older ? ordered : ordered.reverse()) {
    const size = new TextEncoder().encode(JSON.stringify(item)).length
    if (kept.length >= MAX_HISTORY_ITEMS || bytes + size > MAX_HISTORY_BYTES) break
    kept.push(item); bytes += size
  }
  return older ? kept : kept.reverse()
}
export interface ChatState {
  snapshot: ChatSnapshot | null; items: TimelineItem[]; cursor: string | null
  connection: 'connecting' | 'live' | 'reconnecting' | 'offline'; error: string | null
  loadingHistory: boolean; trimmed: boolean; revision: number
}
export interface ChatSocket {
  onopen: (() => void) | null; onmessage: ((event: {data: string}) => void) | null
  onclose: (() => void) | null; send(text: string): void; close(): void
}
/** One scope, one live socket, bounded history. All callbacks belong to a generation. */
export class ChatStore {
  private state: ChatState = {snapshot: null, items: [], cursor: null, connection: 'connecting', error: null, loadingHistory: false, trimmed: false, revision: 0}
  private listeners = new Set<() => void>()
  private generation = 0
  private socket: ChatSocket | null = null
  private timer: ReturnType<typeof setTimeout> | null = null
  private attempts = 0
  private stopped = false
  private refreshing = false
  private refreshAgain = false
  private browsingHistory = false
  constructor(private client: ApiClient, readonly workspace: string, readonly session: string,
    private factory: (url: string) => ChatSocket = url => new WebSocket(url) as ChatSocket) {}
  getState = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private patch(value: Partial<ChatState>) { this.state = {...this.state, ...value, revision: this.state.revision + 1}; this.listeners.forEach(f => f()) }
  private close() { if (this.socket) { this.socket.onclose = null; this.socket.onmessage = null; this.socket.onopen = null; this.socket.close(); this.socket = null } }
  stop() { this.stopped = true; this.generation++; this.close(); if (this.timer) clearTimeout(this.timer) }
  async start() {
    this.stopped = false
    const generation = ++this.generation
    this.close(); if (this.timer) clearTimeout(this.timer)
    this.patch({connection: this.state.snapshot ? 'reconnecting' : 'connecting', error: null, loadingHistory: false})
    try {
      const snapshot = await this.client.chatSnapshot(this.workspace, this.session)
      if (this.stopped || generation !== this.generation) return
      // Reconnection can skip more than a history page. Start a contiguous
      // window at the new snapshot and use its cursor to recover the gap.
      this.browsingHistory = false
      this.patch({snapshot, items: boundedItems(snapshot.timeline.items), cursor: snapshot.timeline.next_cursor, connection: 'live', trimmed: false})
      const socket = this.factory(this.client.chatSocketUrl(this.workspace, this.session)); this.socket = socket
      socket.onopen = () => { if (generation === this.generation) { this.attempts = 0; socket.send(JSON.stringify({type: 'subscribe', stream_epoch: snapshot.stream_epoch, after_sequence: snapshot.sequence})) } }
      socket.onmessage = event => { if (!this.stopped && generation === this.generation) this.frame(event.data) }
      socket.onclose = () => { if (!this.stopped && generation === this.generation) this.reconnect() }
    } catch (error) { if (!this.stopped && generation === this.generation) this.reconnect(error) }
  }
  private reconnect(error?: unknown) {
    this.generation++; this.close()
    this.attempts++
    const terminal = this.attempts > 5 || error instanceof ApiError && error.status === 401
    this.patch({connection: terminal ? 'offline' : 'reconnecting', error: error instanceof ApiError ? error.message : '连接中断；正在核对已提交历史。'})
    if (!terminal) this.timer = setTimeout(() => void this.start(), Math.min(1000 * 2 ** (this.attempts - 1), 15000))
  }
  retry() { this.attempts = 0; void this.start() }
  private frame(raw: string) {
    let frame: ChatFrame
    try { frame = JSON.parse(raw) as ChatFrame } catch { this.reconnect(); return }
    const snapshot = this.state.snapshot
    if (!snapshot || frame.type === 'heartbeat') return
    if (frame.workspace_id !== this.workspace || frame.session_id !== this.session) return
    if (frame.type === 'resync_required' || frame.stream_epoch !== snapshot.stream_epoch || frame.sequence > snapshot.sequence + 1) { this.reconnect(); return }
    if (frame.sequence <= snapshot.sequence) return
    const next = {...snapshot, sequence: frame.sequence}
    if (frame.type === 'reply_started') next.draft = {message_id: frame.message_id!, text: '', revision: 0}
    if (frame.type === 'reply_delta' && !this.state.items.some(item => item.item_id === frame.message_id)) {
      const text = (next.draft?.message_id === frame.message_id ? next.draft.text : '') + (frame.payload.text ?? '')
      if (new TextEncoder().encode(text).length > 262144) { this.reconnect(); return }
      next.draft = {message_id: frame.message_id!, text, revision: (next.draft?.revision ?? 0) + 1}
    }
    if (frame.type === 'stage') {
      // Stage changes update the run card locally; a progress event never
      // justifies pulling the full timeline again.
      const stages = new Map((next.stages ?? []).map(s => [s.node_run_id, s]))
      stages.set(frame.payload.node_run_id!, frame.payload as StageFrame)
      next.stages = [...stages.values()].slice(-64)
    }
    if (frame.type === 'run_state' && frame.payload.discard_message_id === next.draft?.message_id) next.draft = null
    // Keep draft until a snapshot contains its durable replacement.
    this.patch({snapshot: next})
    if (['reply_committed', 'queue_changed', 'run_state'].includes(frame.type)) void this.refresh()
  }
  async refresh() {
    if (this.refreshing) { this.refreshAgain = true; return }
    this.refreshing = true; const generation = this.generation
    try {
      do {
        this.refreshAgain = false
        const fresh = await this.client.chatSnapshot(this.workspace, this.session)
        if (this.stopped || generation !== this.generation) return
        const current = this.state.snapshot
        // Snapshot watermark may be behind frames received while HTTP was in flight.
        // The merge protects the draft and locally received stage frames from an
        // older HTTP response overwriting newer streamed state.
        const snapshot = current && current.stream_epoch === fresh.stream_epoch && current.sequence > fresh.sequence
          ? {...fresh, sequence: current.sequence, draft: current.draft, stages: current.stages} : fresh
        if (snapshot.draft && fresh.timeline.items.some(i => i.item_id === snapshot.draft!.message_id)) snapshot.draft = null
        const incoming = this.browsingHistory ? fresh.timeline.items.filter(i => this.state.items.some(old => old.item_id === i.item_id)) : fresh.timeline.items
        const all = [...this.state.items, ...incoming]
        const merged = boundedItems(all, this.browsingHistory)
        const trimmed = merged.length < new Set(all.map(i => i.item_id)).size
        this.patch({snapshot, items: merged, trimmed: this.state.trimmed || trimmed,
          cursor: !this.state.items.length || trimmed && !this.browsingHistory
            ? fresh.timeline.next_cursor : this.state.cursor})
      } while (this.refreshAgain)
    } catch (error) { if (!this.stopped && generation === this.generation) this.patch({error: error instanceof ApiError ? error.message : '刷新失败，可重试'}) }
    finally { this.refreshing = false }
  }
  async older() {
    if (!this.state.cursor || this.state.loadingHistory) return
    this.browsingHistory = true
    const generation = this.generation; const cursor = this.state.cursor
    this.patch({loadingHistory: true})
    try {
      const page = await this.client.chatHistory(this.workspace, this.session, cursor)
      if (this.stopped || generation !== this.generation) return
      const all = [...page.items, ...this.state.items]; const items = boundedItems(all, true)
      this.patch({items, cursor: page.next_cursor, trimmed: items.length < new Set(all.map(i => i.item_id)).size})
    } catch { if (!this.stopped && generation === this.generation) this.patch({error: '历史加载失败，请重试'}) }
    finally { if (!this.stopped && generation === this.generation) this.patch({loadingHistory: false}) }
  }
  latest() { this.browsingHistory = false; this.patch({items: [], cursor: null, trimmed: false}); void this.start() }
}

export const DRAFT_KEY = 'morrow.chatDrafts.v1'
export const MAX_DRAFTS = 20
export const MAX_DRAFT_CHARS = 4096
export class DraftStorage {
  constructor(private storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>) {}
  entries(): Record<string, string> {
    try { const value: unknown = JSON.parse(this.storage.getItem(DRAFT_KEY) ?? '{}')
      if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
      return Object.fromEntries(Object.entries(value).filter(([k,v]) => k.length < 300 && typeof v === 'string' && v.length <= MAX_DRAFT_CHARS).slice(0, MAX_DRAFTS))
    } catch { return {} }
  }
  key(workspace: string, session: string) { return JSON.stringify([workspace, session]) }
  read(workspace: string, session: string) { return this.entries()[this.key(workspace, session)] ?? '' }
  write(workspace: string, session: string, text: string) {
    const entries = this.entries(); const key = this.key(workspace, session)
    if (text.length > MAX_DRAFT_CHARS) throw new Error('草稿最多 4096 字符，请拆分输入。')
    if (text && !entries[key] && Object.keys(entries).length >= MAX_DRAFTS) throw new Error('已保留 20 个会话草稿。请清理一个草稿后再切换会话；当前输入仍留在此页。')
    if (text) entries[key] = text; else delete entries[key]
    try { this.storage.setItem(DRAFT_KEY, JSON.stringify(entries)) } catch { throw new Error('浏览器无法保存草稿，请复制当前输入或清理草稿后再离开。') }
  }
  remove(key: string) { const entries = this.entries(); delete entries[key]; this.storage.setItem(DRAFT_KEY, JSON.stringify(entries)) }
}

export const OUTBOX_KEY = 'morrow.chatOutbox.v1'
export class OutboxStorage {
  constructor(private storage: Pick<Storage, 'getItem'|'setItem'>) {}
  private entries(): Record<string, import('../api/chat').InteractionInput> {
    try { return JSON.parse(this.storage.getItem(OUTBOX_KEY) ?? '{}') } catch { return {} }
  }
  read(workspace: string, session: string) { return this.entries()[JSON.stringify([workspace,session])] ?? null }
  write(workspace: string, session: string, input: import('../api/chat').InteractionInput | null) {
    const key = JSON.stringify([workspace,session]); const entries=this.entries()
    if (input && !entries[key] && Object.keys(entries).length >= 20) throw new Error('已有 20 个未核对的发送，请先返回原会话核对回执。')
    if (input) entries[key]=input; else delete entries[key]
    this.storage.setItem(OUTBOX_KEY, JSON.stringify(entries))
  }
}
