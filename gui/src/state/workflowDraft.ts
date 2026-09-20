import { ApiError, type ApiClient } from '../api/client'
import type {
  WorkflowDefinitionSourceWire,
  WorkflowDraftDiagnosticWire,
  WorkflowDraftViewWire,
} from '../api/types'
import { commandId } from '../views/lib/editor'

export interface WorkflowDraftScope {
  workspaceId: string
  draftId: string
}

export type DraftSaveState =
  | 'idle'
  | 'scheduled'
  | 'saving'
  | 'saved'
  | 'failed'
  | 'conflict'
  | 'unknown'

export type DraftCheckState = 'idle' | 'checking' | 'passed' | 'warning' | 'blocked'

export interface PendingDraftCommand {
  scope: WorkflowDraftScope
  commandId: string
  generation: number
  expectedRowVersion: number
  source: WorkflowDefinitionSourceWire
}

export interface WorkflowDraftControllerState {
  scope: WorkflowDraftScope | null
  serverSnapshot: WorkflowDraftViewWire | null
  localSource: WorkflowDefinitionSourceWire | null
  editGeneration: number
  acknowledgedGeneration: number
  pendingCommand: PendingDraftCommand | null
  /** A request whose outcome was not safely known, or which hit OCC. */
  unresolvedCommand: PendingDraftCommand | null
  saveState: DraftSaveState
  checkState: DraftCheckState
  diagnostics: WorkflowDraftDiagnosticWire[]
  error: string | null
  message: string | null
}

export interface WorkflowDraftControllerClient {
  updateWorkflowDraft(
    draftId: string,
    source: WorkflowDefinitionSourceWire,
    expectedRowVersion: number,
    commandId: string,
  ): Promise<WorkflowDraftViewWire>
  getWorkflowDraft(draftId: string): Promise<WorkflowDraftViewWire>
}

export interface DraftSaveOutcome {
  ok: boolean
  reason?: 'clean' | 'saved' | 'failed' | 'conflict' | 'unknown' | 'terminal'
}

type Waiter = (outcome: DraftSaveOutcome) => void

const DEFAULT_DEBOUNCE_MS = 400

function clone<T>(value: T): T {
  return structuredClone(value)
}

function sameSource(
  left: WorkflowDefinitionSourceWire | null,
  right: WorkflowDefinitionSourceWire | null,
): boolean {
  if (left === null || right === null) return left === right
  return JSON.stringify(left) === JSON.stringify(right)
}

function checkStateFor(view: WorkflowDraftViewWire | null, dirty: boolean): DraftCheckState {
  if (view === null || dirty) return 'idle'
  if (view.draft.status === 'invalid' || view.draft.status === 'rejected') return 'blocked'
  if (view.draft.status === 'valid') {
    return view.draft.diagnostics.some(item => item.severity === 'warning') ? 'warning' : 'passed'
  }
  return 'idle'
}

function safeError(error: unknown): { message: string; state: DraftSaveState } {
  if (error instanceof ApiError) {
    if (error.status === 409) return { message: error.message, state: 'conflict' }
    if (error.status === 0) return { message: error.message, state: 'unknown' }
    return { message: error.message, state: 'failed' }
  }
  return {
    message: error instanceof Error ? error.message : 'Draft 保存失败',
    state: 'failed',
  }
}

const EMPTY_STATE: WorkflowDraftControllerState = {
  scope: null,
  serverSnapshot: null,
  localSource: null,
  editGeneration: 0,
  acknowledgedGeneration: 0,
  pendingCommand: null,
  unresolvedCommand: null,
  saveState: 'idle',
  checkState: 'idle',
  diagnostics: [],
  error: null,
  message: null,
}

/**
 * Durable draft UI state machine. It owns no server truth: every successful
 * response becomes the server snapshot, while local edits remain separate
 * until the matching generation is acknowledged.
 */
export class WorkflowDraftController {
  private readonly listeners = new Set<() => void>()
  private readonly debounceMs: number
  private readonly makeCommandId: (scope: string) => string
  private state: WorkflowDraftControllerState = EMPTY_STATE
  private timer: ReturnType<typeof setTimeout> | null = null
  private scopeEpoch = 0
  private disposed = false
  private waiters: Waiter[] = []

  constructor(
    private readonly client: WorkflowDraftControllerClient | ApiClient,
    options: { debounceMs?: number; makeCommandId?: (scope: string) => string } = {},
  ) {
    this.debounceMs = options.debounceMs ?? DEFAULT_DEBOUNCE_MS
    this.makeCommandId = options.makeCommandId ?? commandId
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  getState = (): WorkflowDraftControllerState => this.state

  get dirty(): boolean {
    return (
      this.state.pendingCommand !== null ||
      this.state.unresolvedCommand !== null ||
      !sameSource(this.state.localSource, this.state.serverSnapshot?.draft.source ?? null)
    )
  }

  get scope(): WorkflowDraftScope | null {
    return this.state.scope
  }

  /** Load a new scope; late promises from the previous scope become inert. */
  load(scope: WorkflowDraftScope, view: WorkflowDraftViewWire): void {
    this.clearTimer()
    this.scopeEpoch += 1
    this.state = {
      scope: { ...scope },
      serverSnapshot: clone(view),
      localSource: clone(view.draft.source),
      editGeneration: 0,
      acknowledgedGeneration: 0,
      pendingCommand: null,
      unresolvedCommand: null,
      saveState: 'saved',
      checkState: checkStateFor(view, false),
      diagnostics: clone(view.draft.diagnostics),
      error: null,
      message: '已读取 Draft 服务器快照。',
    }
    this.notify()
  }

  /** Mark the editor empty without persisting a source or task body. */
  clear(): void {
    this.clearTimer()
    this.scopeEpoch += 1
    this.state = { ...EMPTY_STATE }
    this.notify()
  }

  edit(source: WorkflowDefinitionSourceWire): boolean {
    const snapshot = this.state.serverSnapshot
    if (
      snapshot === null ||
      snapshot.draft.status === 'frozen' ||
      snapshot.draft.status === 'rejected' ||
      this.state.scope === null
    ) {
      return false
    }
    const next = clone(source)
    const generation = this.state.editGeneration + 1
    const dirty = !sameSource(next, snapshot.draft.source)
    this.state = {
      ...this.state,
      localSource: next,
      editGeneration: generation,
      saveState: this.state.pendingCommand !== null ? 'saving' : dirty ? 'scheduled' : 'idle',
      checkState: checkStateFor(snapshot, dirty),
      error: null,
      message: dirty ? '有未保存修改。' : '修改已恢复为已保存内容。',
      unresolvedCommand: dirty ? this.state.unresolvedCommand : null,
    }
    this.notify()
    if (dirty && this.state.unresolvedCommand === null) this.schedule(this.debounceMs)
    else if (!dirty) this.clearTimer()
    return true
  }

  /** Force the latest local payload into the serial update queue. */
  flush(): void {
    this.clearTimer()
    void this.startSave()
  }

  /** Wait until the latest local generation has been confirmed by Core. */
  saveNow(): Promise<DraftSaveOutcome> {
    if (this.state.serverSnapshot === null || this.state.localSource === null) {
      return Promise.resolve({ ok: false, reason: 'failed' })
    }
    if (this.state.serverSnapshot.draft.status === 'frozen' || this.state.serverSnapshot.draft.status === 'rejected') {
      return Promise.resolve({ ok: false, reason: 'terminal' })
    }
    if (this.state.unresolvedCommand !== null) {
      return Promise.resolve({
        ok: false,
        reason: this.state.saveState === 'conflict' ? 'conflict' : 'unknown',
      })
    }
    if (!this.dirty && this.state.pendingCommand === null) {
      return Promise.resolve({ ok: true, reason: 'clean' })
    }
    return new Promise(resolve => {
      this.waiters.push(resolve)
      this.clearTimer()
      if (this.state.pendingCommand === null && this.state.unresolvedCommand === null) {
        void this.startSave()
      }
    })
  }

  /** Re-read after a 409/network-unknown result without changing local input. */
  async reconcile(): Promise<'matched' | 'conflict' | 'failed'> {
    const scope = this.state.scope
    if (scope === null) return 'failed'
    const epoch = this.scopeEpoch
    try {
      const view = await this.client.getWorkflowDraft(scope.draftId)
      if (this.disposed || epoch !== this.scopeEpoch || this.state.scope?.draftId !== scope.draftId) {
        return 'failed'
      }
      const unresolved = this.state.unresolvedCommand
      const unresolvedHash = unresolved === null ? null : await sourceHash(unresolved.source)
      const matched = unresolved !== null && (
        (unresolvedHash !== null && view.draft.source_hash === unresolvedHash) ||
        sameSource(view.draft.source, unresolved.source)
      )
      this.state = {
        ...this.state,
        serverSnapshot: clone(view),
        diagnostics: clone(view.draft.diagnostics),
        unresolvedCommand: matched ? null : unresolved,
        saveState: matched ? 'saved' : unresolved === null ? this.state.saveState : 'conflict',
        checkState: checkStateFor(view, !sameSource(this.state.localSource, view.draft.source)),
        error: matched ? null : unresolved === null ? this.state.error : '服务器版本与本地修改不同。',
        message: matched ? '已核对服务器结果。' : '服务器版本已读取，请选择保留或放弃本地修改。',
      }
      if (matched) {
        const acknowledged = Math.max(
          this.state.acknowledgedGeneration,
          unresolved?.generation ?? this.state.acknowledgedGeneration,
        )
        this.state = { ...this.state, acknowledgedGeneration: acknowledged }
        if (!this.dirty) this.resolveWaiters({ ok: true, reason: 'saved' })
        else if (this.state.pendingCommand === null) this.schedule(0)
      } else {
        this.resolveWaiters({ ok: false, reason: 'conflict' })
      }
      this.notify()
      return matched ? 'matched' : 'conflict'
    } catch (error) {
      if (!this.disposed && epoch === this.scopeEpoch) {
        const safe = safeError(error)
        this.state = { ...this.state, saveState: 'failed', error: safe.message, message: '无法核对服务器结果。' }
        this.resolveWaiters({ ok: false, reason: 'failed' })
        this.notify()
      }
      return 'failed'
    }
  }

  /** Drop only local, unacknowledged input; it never deletes the durable Draft. */
  discardLocal(): void {
    const snapshot = this.state.serverSnapshot
    if (snapshot === null) return
    const pending = this.state.pendingCommand
    const unresolved = this.state.unresolvedCommand
    this.clearTimer()
    this.state = {
      ...this.state,
      localSource: clone(snapshot.draft.source),
      editGeneration: pending?.generation ?? this.state.acknowledgedGeneration,
      // A request already sent to Core cannot be cancelled. Keep its record so
      // a late response is still classified and the guard cannot claim clean
      // state before the durable outcome is known.
      pendingCommand: pending,
      unresolvedCommand: unresolved,
      saveState: pending === null ? this.state.saveState : 'saving',
      checkState: checkStateFor(snapshot, pending !== null || unresolved !== null),
      error: unresolved === null ? null : this.state.error,
      message: pending === null && unresolved === null
        ? '已放弃未保存修改；持久化 Draft 未删除。'
        : '已停止继续编辑；已发出的保存仍可能完成，持久化 Draft 未删除。',
    }
    if (pending === null && unresolved === null) this.resolveWaiters({ ok: true, reason: 'clean' })
    else if (unresolved !== null && pending === null) {
      this.resolveWaiters({ ok: false, reason: this.state.saveState === 'conflict' ? 'conflict' : 'unknown' })
    }
    this.notify()
  }

  dispose(): void {
    this.disposed = true
    this.clearTimer()
    this.scopeEpoch += 1
    this.resolveWaiters({ ok: false, reason: 'failed' })
    this.listeners.clear()
  }

  private schedule(delay: number): void {
    this.clearTimer()
    if (this.disposed) return
    this.timer = setTimeout(() => {
      this.timer = null
      void this.startSave()
    }, delay)
  }

  private clearTimer(): void {
    if (this.timer !== null) clearTimeout(this.timer)
    this.timer = null
  }

  private async startSave(): Promise<void> {
    const { scope, serverSnapshot, localSource, pendingCommand, unresolvedCommand } = this.state
    if (
      this.disposed ||
      scope === null ||
      serverSnapshot === null ||
      localSource === null ||
      pendingCommand !== null ||
      unresolvedCommand !== null ||
      !this.dirty
    ) {
      if (!this.dirty && pendingCommand === null && unresolvedCommand === null) {
        this.resolveWaiters({ ok: true, reason: 'clean' })
      }
      return
    }
    const command: PendingDraftCommand = {
      scope: { ...scope },
      commandId: this.makeCommandId('draft_update'),
      generation: this.state.editGeneration,
      expectedRowVersion: serverSnapshot.draft.row_version,
      source: clone(localSource),
    }
    const epoch = this.scopeEpoch
    this.state = {
      ...this.state,
      pendingCommand: command,
      saveState: 'saving',
      message: '正在保存并通过 Core Compiler 检查…',
      error: null,
    }
    this.notify()
    try {
      const view = await this.client.updateWorkflowDraft(
        command.scope.draftId,
        clone(command.source),
        command.expectedRowVersion,
        command.commandId,
      )
      this.handleSuccess(epoch, command, view)
    } catch (error) {
      this.handleFailure(epoch, command, error)
    }
  }

  private handleSuccess(epoch: number, command: PendingDraftCommand, view: WorkflowDraftViewWire): void {
    if (
      this.disposed ||
      epoch !== this.scopeEpoch ||
      this.state.pendingCommand?.commandId !== command.commandId ||
      this.state.scope?.draftId !== command.scope.draftId
    ) return
    const stillDirty = !sameSource(this.state.localSource, view.draft.source)
    const newerEdit = this.state.editGeneration > command.generation
    this.state = {
      ...this.state,
      serverSnapshot: clone(view),
      pendingCommand: null,
      acknowledgedGeneration: Math.max(this.state.acknowledgedGeneration, command.generation),
      diagnostics: clone(view.draft.diagnostics),
      saveState: stillDirty || newerEdit ? 'scheduled' : 'saved',
      checkState: checkStateFor(view, stillDirty || newerEdit),
      error: null,
      message: stillDirty || newerEdit ? '上一笔修改已保存，正在保存最新修改…' : view.draft.status === 'valid' ? 'Draft 已校验并保存。' : 'Draft 已保存；请处理检查问题。',
      unresolvedCommand: null,
    }
    this.notify()
    if (stillDirty || newerEdit) this.schedule(0)
    else this.resolveWaiters({ ok: true, reason: 'saved' })
  }

  private handleFailure(epoch: number, command: PendingDraftCommand, error: unknown): void {
    if (
      this.disposed ||
      epoch !== this.scopeEpoch ||
      this.state.pendingCommand?.commandId !== command.commandId ||
      this.state.scope?.draftId !== command.scope.draftId
    ) return
    const safe = safeError(error)
    this.state = {
      ...this.state,
      pendingCommand: null,
      unresolvedCommand: command,
      saveState: safe.state,
      checkState: 'idle',
      error: safe.message,
      message: safe.state === 'conflict' ? '保存冲突已暂停自动重试，请核对服务器版本。' : safe.state === 'unknown' ? '保存结果未知，请先核对服务器状态。' : 'Draft 保存失败；本地修改仍保留。',
    }
    this.resolveWaiters({ ok: false, reason: safe.state === 'unknown' ? 'unknown' : safe.state === 'conflict' ? 'conflict' : 'failed' })
    this.notify()
  }

  private resolveWaiters(outcome: DraftSaveOutcome): void {
    if (this.waiters.length === 0) return
    const waiters = this.waiters
    this.waiters = []
    for (const waiter of waiters) waiter(outcome)
  }

  private notify(): void {
    for (const listener of this.listeners) listener()
  }
}

/** Stable client-side hash for reconciling a lost response without exposing data. */
async function sourceHash(source: WorkflowDefinitionSourceWire): Promise<string | null> {
  const subtle = globalThis.crypto?.subtle
  if (subtle === undefined) return null
  const canonical = JSON.stringify(sortKeys(source))
  const digest = await subtle.digest('SHA-256', new TextEncoder().encode(canonical))
  return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('')
}

function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys)
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, sortKeys(item)]),
    )
  }
  return value
}
