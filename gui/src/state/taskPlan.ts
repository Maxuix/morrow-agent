import { ApiError, type ApiClient } from '../api/client'
import type { AttachmentRef } from '../api/attachments'
import type {
  ControlReceiptWire,
  PlanNodeEditWire,
  PlanWorkflowRequestWire,
  TaskPlanViewWire,
} from '../api/types'
import { latestOperation, planningRequestKey, taskPlanPhase, type TaskPlanPhase } from '../views/lib/taskPlan'

export interface TaskPlanState {
  view: TaskPlanViewWire | null
  phase: TaskPlanPhase
  /** Load failure of the view itself; submission errors land on `error`. */
  loadError: string | null
  error: string | null
  notice: string | null
  busy: boolean
  /** A view fetch is in flight; the explicit start waits for it to settle. */
  refreshing: boolean
  /** Durable control receipts for this session, oldest first (D07). */
  receipts: ControlReceiptWire[]
  revision: number
}

const POLL_INTERVAL_MS = 1500

/**
 * Generation-scoped pause/resume transport (B-line coordination, A09). The
 * planning-operation endpoints differ from the run-scoped ones and live on
 * coordinator-owned routes; the caller injects the transport until the
 * coordinator's client methods land. Absent transport → honest error, never
 * a routed call to the wrong endpoint.
 */
export interface PlanningGenerationTransport {
  pauseGeneration(workspace: string, session: string, operationId: string,
    body: {command_id: string; session_id: string; expected_row_version?: number | null}): Promise<unknown>
  resumeGeneration(workspace: string, session: string, operationId: string,
    body: {command_id: string; session_id: string; expected_row_version?: number | null}): Promise<unknown>
}

/** B's additive `view.generation` projection (P05), read structurally. */
export interface PlanningGenerationView {
  planning_operation_id: string
  status: string
  pause_lifecycle: string
}

export function planningGenerationOf(view: TaskPlanViewWire | null): PlanningGenerationView | null {
  if (view === null) return null
  const generation = (view as {generation?: PlanningGenerationView | null}).generation
  return generation !== null && generation !== undefined && generation.planning_operation_id
    ? generation
    : null
}

/**
 * Session-scoped task-plan controller over the server-owned PlanningBinding.
 * The store holds no local DAG truth: `view` is the GET projection, edits go
 * through typed commands, and events only schedule refetches.
 */
export class TaskPlanStore {
  private state: TaskPlanState = {
    view: null, phase: 'idle', loadError: null, error: null, notice: null, busy: false, refreshing: false, receipts: [], revision: 0,
  }
  private listeners = new Set<() => void>()
  private generation = 0
  private timer: ReturnType<typeof setTimeout> | null = null
  private eventCursor = 0
  private refreshChain: Promise<void> = Promise.resolve()
  private pollRefreshQueued = false
  /** Node ids with unsaved editor buffers, reported by the review workspace.
   *  Plain state (not `TaskPlanState`) so typing never re-renders the panel. */
  private unsavedNodes: ReadonlySet<string> = new Set()
  /** Last submission identity: same key replays the same command on retry. */
  private pending: { key: string; request: PlanWorkflowRequestWire } | null = null
  constructor(private client: ApiClient, readonly workspace: string, readonly session: string,
    private generationTransport?: PlanningGenerationTransport) {}
  getState = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private patch(value: Partial<TaskPlanState>) {
    this.state = { ...this.state, ...value, revision: this.state.revision + 1 }
    this.listeners.forEach((listener) => listener())
  }

  /** Plan-level unsaved-edit registry shared with the chat start guard. */
  get hasUnsavedEdits(): boolean {
    return this.unsavedNodes.size > 0
  }

  setUnsavedNodes(nodeIds: Iterable<string>) {
    this.unsavedNodes = new Set(nodeIds)
  }

  async start() {
    const generation = ++this.generation
    if (this.timer) clearTimeout(this.timer)
    this.eventCursor = 0
    await this.refresh(generation)
    if (!this.alive(generation)) return
    this.poll(generation)
  }

  stop() {
    this.generation++
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
  }

  private alive(generation: number) { return generation === this.generation }

  /** One self-re-arming poll loop per generation; submissions only refresh. */
  private poll(generation: number) {
    if (this.timer) clearTimeout(this.timer)
    this.timer = setTimeout(async () => {
      if (!this.alive(generation)) return
      try {
        const events = await this.client.taskPlanEvents(this.workspace, this.session, this.eventCursor)
        if (!this.alive(generation)) return
        if (events.length > 0) this.eventCursor = events[events.length - 1]!.sequence
        // Refresh every tick: pause/cancel from another panel or tab produces
        // no planning event, so event-only refreshes leave planPhase stale.
        await this.refresh(generation)
        if (!this.alive(generation)) return
      } catch {
        // Polling failures are transient; the loop continues and surfaces via refresh errors.
      }
      this.poll(generation)
    }, POLL_INTERVAL_MS)
  }
  /**
   * Fetch the authoritative view. Poll/event-triggered refreshes collapse
   * into one in-flight fetch; `force` (after a command) always runs its own
   * fetch chained after the current one, so the caller observes the
   * post-command version and "start" never reads a pre-save projection.
   */
  refresh(generation = this.generation, force = false): Promise<void> {
    if (!force) {
      if (this.pollRefreshQueued) return this.refreshChain
      this.pollRefreshQueued = true
    }
    const fetch = this.refreshChain.then(() => this.fetchView(generation))
    this.refreshChain = fetch.catch(() => {})
    return fetch
  }

  private async fetchView(generation: number) {
    this.pollRefreshQueued = false
    this.patch({ refreshing: true })
    try {
      const [view, receipts] = await Promise.all([
        this.client.taskPlanView(this.workspace, this.session),
        this.client.taskPlanControlReceipts(this.workspace, this.session, 0, 50).catch(() => ({ items: [] })),
      ])
      if (!this.alive(generation)) return
      this.patch({ view, phase: taskPlanPhase(view), receipts: receipts.items, loadError: null })
    } catch (error) {
      if (!this.alive(generation)) return
      this.patch({ loadError: error instanceof Error ? error.message : '规划状态读取失败' })
    } finally {
      if (this.alive(generation)) this.patch({ refreshing: false })
    }
  }

  /**
   * Reconcile from an external fact change (another panel/tab, reconnect):
   * the session store calls this when the execution snapshot moves.
   */
  syncExternal(): Promise<void> {
    return this.refresh(this.generation)
  }

  /** True when a planning operation is waiting on the Provider right now. */
  get generating(): boolean {
    const operation = this.state.view === null ? null : latestOperation(this.state.view)
    return operation !== null && (operation.status === 'queued' || operation.status === 'running')
  }

  /**
   * Generate from scratch or revise the current draft. The same
   * objective/base version reuses one command ID; a lost response replays.
   */
  async submit(objective: string, attachments: AttachmentRef[] = []) {
    const view = this.state.view
    if (this.generating) { this.patch({ error: '已有生成正在进行；可先取消再重试。' }); return }
    const bindingId = view?.binding?.planning_binding_id ?? null
    const operation = bindingId && view?.binding?.current_draft_id ? 'revise' : 'generate'
    const baseVersion = operation === 'revise' ? view?.draft?.draft.row_version ?? 0 : 0
    const key = planningRequestKey(operation, objective, baseVersion)
    const command = this.pending?.key === key && !this.failed(this.state.view)
      ? this.pending.request.command_id
      : commandIdFor(operation)
    const request: PlanWorkflowRequestWire = {
      command_id: command,
      session_id: this.session,
      origin_interaction_id: `planning_${command}`,
      task: { objective, scope: [], constraints: [], source_refs: [] },
      ...(attachments.length > 0 ? { attachments } : {}),
      ...(bindingId !== null ? { planning_binding_id: bindingId, base_draft_version: baseVersion } : {}),
      operation,
    }
    this.pending = { key, request }
    this.patch({ busy: true, error: null, notice: operation === 'generate' ? '已提交生成请求。' : '已提交修改生成请求。' })
    try {
      await this.client.taskPlanGenerate(this.workspace, this.session, request)
      this.pending = null
      if (!this.alive(this.generation)) return
      this.patch({ notice: operation === 'generate' ? '正在生成任务计划…' : '正在按修改重新生成…' })
      await this.refresh(this.generation, true)
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        // Same ID with a different payload: drop the retained identity.
        this.pending = null
      }
      this.patch({ error: error instanceof Error ? error.message : '生成请求失败', notice: null })
    } finally {
      this.patch({ busy: false })
    }
  }

  private failed(view: TaskPlanViewWire | null): boolean {
    const operation = view === null ? null : latestOperation(view)
    return operation !== null && operation.status === 'failed'
  }

  /** Cancel the in-flight planning operation only; execution is never started. */
  async cancelGeneration() {
    const view = this.state.view
    const operation = view === null ? null : latestOperation(view)
    if (operation === null || (operation.status !== 'running' && operation.status !== 'queued')) {
      this.patch({ error: '当前没有可取消的生成。' })
      return
    }
    this.patch({ busy: true, error: null })
    try {
      await this.client.taskPlanCancel(this.workspace, this.session, operation.planning_operation_id)
      if (!this.alive(this.generation)) return
      this.patch({ notice: '已取消本次生成；计划保持当前状态。' })
      await this.refresh(this.generation, true)
    } catch (error) {
      this.patch({ error: error instanceof Error ? error.message : '取消失败' })
    } finally {
      this.patch({ busy: false })
    }
  }

  /** Undo stack: draft versions recorded before each successful edit. */
  private editHistory: number[] = []
  get undoVersion(): number | null {
    return this.editHistory.at(-1) ?? null
  }

  /** Retry identity for the explicit start: same exact plan → same command. */
  private startKey: string | null = null
  private startCommand: string | null = null
  private pauseKey: string | null = null
  private pauseCommand: string | null = null
  private changeKey: string | null = null
  private changeCommand: string | null = null

  /**
   * Explicit start (button). The request carries the exact draft version and
   * execution digest the user saw; a lost response replays the same command.
   */
  async startExecution() {
    if (this.state.busy) return // double-click guard: one start per user intent
    const view = this.state.view
    const execution = view?.execution
    if (!execution?.allowed || !execution.digest || execution.draft_id === null || execution.draft_version === null) {
      this.patch({ error: '当前计划还不能开始；请先修复或等待校验完成。' })
      return
    }
    const key = JSON.stringify({ d: execution.draft_id, v: execution.draft_version, g: execution.digest })
    const command = this.startKey === key && this.startCommand !== null ? this.startCommand : `cmd_plan_start_${crypto.randomUUID().replace(/-/g, '').slice(0, 12)}`
    this.startKey = key
    this.startCommand = command
    this.patch({ busy: true, error: null })
    try {
      await this.client.taskPlanStart(this.workspace, this.session, {
        command_id: command,
        session_id: this.session,
        draft_id: execution.draft_id,
        draft_version: execution.draft_version,
        execution_digest: execution.digest,
        action_source: 'button',
        interaction_id: command,
      })
      this.startKey = null
      this.startCommand = null
      if (!this.alive(this.generation)) return
      this.patch({ notice: '已开始执行。' })
      await this.refresh(this.generation, true)
    } catch (error) {
      const stale = error instanceof ApiError && (error.status === 409 || error.status === 400)
      if (stale) {
        this.startKey = null
        this.startCommand = null
      }
      this.patch({
        error: stale
          ? `${error instanceof Error ? error.message : '开始失败'}；计划已变化，请基于当前版本重试。`
          : error instanceof Error ? error.message : '开始失败',
      })
      if (stale) await this.refresh(this.generation, true)
    } finally {
      this.patch({ busy: false })
    }
  }

  /** Durable pause: closes later admission; Active nodes keep draining. */
  async pause() {
    const run = this.state.view?.run
    if (run === null || run === undefined) {
      this.patch({ error: '当前没有可暂停的运行。' })
      return
    }
    const key = JSON.stringify({ run: run.workflow_run_id, version: run.row_version ?? null })
    const command = this.pauseKey === key && this.pauseCommand !== null
      ? this.pauseCommand
      : `cmd_plan_pause_${crypto.randomUUID().replace(/-/g, '').slice(0, 12)}`
    this.pauseKey = key
    this.pauseCommand = command
    this.patch({ busy: true, error: null })
    try {
      await this.client.taskPlanPause(this.workspace, this.session, {
        command_id: command,
        session_id: this.session,
        expected_run_row_version: run.row_version ?? null,
      })
      this.pauseKey = null
      this.pauseCommand = null
      if (!this.alive(this.generation)) return
      this.patch({ notice: '正在暂停…' })
      await this.refresh(this.generation, true)
    } catch (error) {
      const stale = error instanceof ApiError && (error.status === 409 || error.status === 400)
      if (stale) {
        this.pauseKey = null
        this.pauseCommand = null
      }
      this.patch({
        error: stale
          ? `${error instanceof Error ? error.message : '暂停失败'}；运行状态已变化，请基于当前版本重试。`
          : error instanceof Error ? error.message : '暂停失败',
      })
      if (stale) await this.refresh(this.generation, true)
    } finally {
      this.patch({ busy: false })
    }
  }

  /**
   * Generation-scoped pause (A09, B coordination): pauses the planning
   * operation itself while it waits on the model — there is no run yet, so
   * this must never route to the run endpoints. The suspension lands
   * asynchronously; the poll loop settles `view.generation.status`.
   */
  async pauseGeneration() {
    const generation = planningGenerationOf(this.state.view)
    if (generation === null || (generation.status !== 'running' && generation.status !== 'queued')) {
      this.patch({ error: '当前没有可暂停的生成。' })
      return
    }
    if (this.generationTransport === undefined) {
      this.patch({ error: '当前无法暂停生成。' })
      return
    }
    const command = `cmd_plan_gen_pause_${crypto.randomUUID().replace(/-/g, '').slice(0, 12)}`
    this.patch({ busy: true, error: null })
    try {
      await this.generationTransport.pauseGeneration(this.workspace, this.session, generation.planning_operation_id, {
        command_id: command,
        session_id: this.session,
      })
      if (!this.alive(this.generation)) return
      this.patch({ notice: '正在暂停生成…' })
      await this.refresh(this.generation, true)
    } catch (error) {
      this.patch({ error: error instanceof Error ? error.message : '暂停生成失败' })
      await this.refresh(this.generation, true)
    } finally {
      this.patch({ busy: false })
    }
  }

  /**
   * Generation-scoped resume: continues the paused planning operation with a
   * new request sequence. The resume route dispatches internally and returns
   * the terminal operation (e.g. a saved candidate was applied).
   */
  async resumeGeneration() {
    const generation = planningGenerationOf(this.state.view)
    if (generation === null || generation.status !== 'paused') {
      this.patch({ error: '当前没有可继续的生成。' })
      return
    }
    if (this.generationTransport === undefined) {
      this.patch({ error: '当前无法继续生成。' })
      return
    }
    const command = `cmd_plan_gen_resume_${crypto.randomUUID().replace(/-/g, '').slice(0, 12)}`
    this.patch({ busy: true, error: null })
    try {
      await this.generationTransport.resumeGeneration(this.workspace, this.session, generation.planning_operation_id, {
        command_id: command,
        session_id: this.session,
      })
      if (!this.alive(this.generation)) return
      this.patch({ notice: '正在继续生成…' })
      await this.refresh(this.generation, true)
    } catch (error) {
      this.patch({ error: error instanceof Error ? error.message : '继续生成失败' })
      await this.refresh(this.generation, true)
    } finally {
      this.patch({ busy: false })
    }
  }

  /** Pause remaining admission and open a change-mode planning binding. */
  async prepareChange() {
    const run = this.state.view?.run
    if (run === null || run === undefined) {
      this.patch({ error: '当前没有可修改的运行。' })
      return
    }
    const key = JSON.stringify({ run: run.workflow_run_id, revision: run.workflow_revision_id ?? null, version: run.row_version ?? null })
    const command = this.changeKey === key && this.changeCommand !== null
      ? this.changeCommand
      : `cmd_plan_change_${crypto.randomUUID().replace(/-/g, '').slice(0, 12)}`
    this.changeKey = key
    this.changeCommand = command
    this.patch({ busy: true, error: null })
    try {
      await this.client.taskPlanChange(this.workspace, this.session, {
        command_id: command,
        session_id: this.session,
        origin_interaction_id: command,
        action_source: 'button',
        expected_run_row_version: run.row_version ?? null,
      })
      this.changeKey = null
      this.changeCommand = null
      if (!this.alive(this.generation)) return
      this.patch({ notice: '正在暂停，可修改后续计划。' })
      await this.refresh(this.generation, true)
    } catch (error) {
      const stale = error instanceof ApiError && (error.status === 409 || error.status === 400)
      if (stale) {
        this.changeKey = null
        this.changeCommand = null
      }
      this.patch({
        error: stale
          ? `${error instanceof Error ? error.message : '准备修改失败'}；运行状态已变化，请基于当前版本重试。`
          : error instanceof Error ? error.message : '准备修改失败',
      })
      if (stale) await this.refresh(this.generation, true)
    } finally {
      this.patch({ busy: false })
    }
  }

  async applyChange(decision: 'accept_change' | 'save_candidate' | 'reject_change') {
    const view = this.state.view
    const candidate = view?.candidate
    const run = view?.run
    if (candidate === null || candidate === undefined || candidate.digest === null || run === null || run === undefined) {
      this.patch({ error: '当前没有可决定的修改候选。' })
      return
    }
    if (decision === 'accept_change' && !candidate.stable) {
      this.patch({ error: '暂停完成后可应用修改。' })
      return
    }
    const command = `cmd_plan_${decision}_${crypto.randomUUID().replace(/-/g, '').slice(0, 12)}`
    this.patch({ busy: true, error: null })
    try {
      await this.client.taskPlanApplyChange(this.workspace, this.session, {
        command_id: command,
        session_id: this.session,
        decision,
        action_source: 'button',
        interaction_id: command,
        candidate_digest: candidate.digest,
        expected_parent_row_version: candidate.expected_parent_row_version,
      })
      if (!this.alive(this.generation)) return
      this.patch({
        notice: decision === 'accept_change'
          ? '已采纳修改并继续执行后续步骤。'
          : decision === 'save_candidate'
            ? '修改已保存，任务仍暂停。'
            : '修改已放弃。',
      })
      await this.refresh(this.generation, true)
    } catch (error) {
      const stale = error instanceof ApiError && (error.status === 409 || error.status === 400)
      this.patch({
        error: stale
          ? `${error instanceof Error ? error.message : '决定失败'}；请基于当前版本重试。`
          : error instanceof Error ? error.message : '决定失败',
      })
      if (stale) await this.refresh(this.generation, true)
    } finally {
      this.patch({ busy: false })
    }
  }

  async resumeRun() {
    const run = this.state.view?.run
    if (run === null || run === undefined) {
      this.patch({ error: '当前没有可继续的运行。' })
      return
    }
    const command = `cmd_plan_resume_${crypto.randomUUID().replace(/-/g, '').slice(0, 12)}`
    this.patch({ busy: true, error: null })
    try {
      await this.client.taskPlanResume(this.workspace, this.session, {
        command_id: command,
        session_id: this.session,
        expected_run_row_version: run.row_version ?? null,
      })
      if (!this.alive(this.generation)) return
      this.patch({ notice: '已继续原计划。' })
      await this.refresh(this.generation, true)
    } catch (error) {
      this.patch({ error: error instanceof Error ? error.message : '继续失败' })
      await this.refresh(this.generation, true)
    } finally {
      this.patch({ busy: false })
    }
  }

  async prepareRepair() {
    const run = this.state.view?.run
    if (run === null || run === undefined) {
      this.patch({ error: '当前没有可修复的运行。' })
      return
    }
    const command = `cmd_plan_repair_${crypto.randomUUID().replace(/-/g, '').slice(0, 12)}`
    this.patch({ busy: true, error: null })
    try {
      await this.client.taskPlanRepair(this.workspace, this.session, {
        command_id: command,
        session_id: this.session,
        origin_interaction_id: command,
        action_source: 'button',
      })
      if (!this.alive(this.generation)) return
      this.patch({ notice: '修复计划已生成。' })
      await this.refresh(this.generation, true)
    } catch (error) {
      this.patch({ error: error instanceof Error ? error.message : '无法生成修复计划' })
      await this.refresh(this.generation, true)
    } finally {
      this.patch({ busy: false })
    }
  }

  /**
   * State-aware control resolution for a composer message. Returns `steer`
   * when the caller must submit an ordinary steered interaction instead.
   */
  /** Retry identity for one control input: same text + same target run. */
  private controlIdentity: { key: string; command: string; clientMessageId: string } | null = null
  private controlInFlight = false

  async control(text: string): Promise<{kind: 'handled'; intent: string} | {kind: 'steer'; targetAgentRunId: string | null} | {kind: 'unhandled'}> {
    const view = this.state.view
    if (view === null) return { kind: 'unhandled' }
    // A missing binding only blocks control outside the live-run family:
    // direct runs (explicit_workflow) have no planning binding but still own
    // a run-scoped control channel (BUG-GUI-001).
    const controlState = view.control?.state ?? null
    if (
      view.binding === null &&
      (controlState === null ||
        (controlState !== 'running' && controlState !== 'draining' && controlState !== 'paused'))
    ) {
      return { kind: 'unhandled' }
    }
    // Single-flight per session: a second click or Enter while the first
    // classification is in flight must not start a parallel command (D08/S4.7).
    if (this.controlInFlight) {
      this.patch({ error: '上一条控制指令仍在处理中；请等待回执后再发送。' })
      return { kind: 'handled', intent: 'none' }
    }
    const target = view.control?.target ?? null
    // Retry identity is text + run only: a lost response whose run then moved
    // running → pausing still replays the same command id.
    const key = JSON.stringify({
      text: text.trim(),
      run: target?.workflow_run_id ?? null,
    })
    const identity = this.controlIdentity?.key === key
      ? this.controlIdentity
      : {
          key,
          command: `cmd_plan_ctrl_${crypto.randomUUID().replace(/-/g, '').slice(0, 12)}`,
          clientMessageId: `client.plan.${crypto.randomUUID().replace(/-/g, '').slice(0, 12)}`,
        }
    this.controlIdentity = identity
    this.controlInFlight = true
    this.patch({ busy: true, error: null, notice: null })
    try {
      const result = (await this.client.taskPlanControl(this.workspace, this.session, {
        command_id: identity.command,
        client_message_id: identity.clientMessageId,
        session_id: this.session,
        text,
        expected_workflow_run_id: target?.workflow_run_id ?? undefined,
      })) as {
        disposition: 'executed' | 'answered' | 'unresolved' | 'steer' | 'acknowledged' | 'needs_choice'
        intent: string
        message?: string | null
        next_action?: string | null
        target_agent_run_id?: string | null
      }
      if (!this.alive(this.generation)) return { kind: 'handled', intent: result.intent }
      if (result.disposition === 'steer') {
        this.controlIdentity = null
        this.patch({ busy: false })
        return { kind: 'steer', targetAgentRunId: result.target_agent_run_id ?? null }
      }
      if (result.disposition === 'unresolved' || result.disposition === 'needs_choice') {
        this.patch({ error: result.message ?? '未识别为控制指令。', busy: false })
        await this.refresh(this.generation, true)
        return { kind: 'handled', intent: result.intent }
      }
      this.controlIdentity = null
      this.patch({
        notice: result.disposition === 'answered' || result.disposition === 'acknowledged'
          ? result.message ?? ''
          : result.intent === 'start'
            ? '已开始执行。'
            : result.intent === 'pause_run'
              ? '已请求暂停；进行中的步骤结束后才会完全停下。'
              : result.intent === 'resume_run'
                ? '已继续原计划。'
              : result.intent === 'revise'
                ? (this.state.view?.run !== null ? '已请求暂停并准备修改后续计划。' : '已按你的修改重新生成计划。')
                : result.intent === 'repair'
                  ? '修复计划已生成。'
                : '已执行。',
        busy: false,
      })
      await this.refresh(this.generation, true)
      return { kind: 'handled', intent: result.intent }
    } catch (error) {
      this.patch({ error: error instanceof Error ? error.message : '控制解析失败', busy: false })
      // The durable receipt survives a lost response; reconcile it instead of
      // assuming the command did not run.
      await this.refresh(this.generation, true)
      return { kind: 'handled', intent: 'none' }
    } finally {
      this.controlInFlight = false
    }
  }

  /**
   * One typed node edit (`/task-plan/nodes`): add/replace/remove/restore/
   * dependencies. Each attempt carries its own command ID bound to the
   * current base version: if a lost response actually landed, the retry hits
   * a version conflict and refreshes instead of applying the edit twice.
   */
  async editNode(action: PlanNodeEditWire['action'], payload: {
    nodeId?: string
    node?: PlanNodeEditWire['node']
    /** `dependencies` action only: the full parent set of `nodeId`. */
    dependsOn?: string[]
    restoreVersion?: number
    confirmImpact?: boolean
  }) {
    const view = this.state.view
    const binding = view?.binding
    const draftVersion = view?.draft?.draft.row_version
    if (binding === null || binding === undefined || !binding.current_draft_id || draftVersion === undefined || draftVersion === null) {
      this.patch({ error: '当前没有可编辑的计划草稿。' })
      return
    }
    if (this.generating) { this.patch({ error: '生成进行中；请等待完成或取消后再编辑。' }); return }
    const command = `cmd_plan_node_${action}_${payload.nodeId ?? payload.restoreVersion ?? 'x'}_${draftVersion}_${crypto.randomUUID().replace(/-/g, '').slice(0, 8)}`
    const body: PlanNodeEditWire = {
      command_id: command,
      binding_id: binding.planning_binding_id,
      expected_version: draftVersion,
      action,
      confirm_impact: payload.confirmImpact ?? false,
      ...(payload.nodeId !== undefined ? { node_id: payload.nodeId } : {}),
      ...(payload.node !== undefined ? { node: payload.node } : {}),
      ...(action === 'dependencies' && payload.dependsOn !== undefined ? { depends_on: payload.dependsOn } : {}),
      ...(payload.restoreVersion !== undefined ? { restore_version: payload.restoreVersion } : {}),
    }
    this.patch({ busy: true, error: null })
    try {
      const result = await this.client.taskPlanNodeEdit(this.workspace, this.session, body)
      if (!this.alive(this.generation)) return
      if (result.status === 'succeeded' && result.result_draft_version !== null) {
        this.editHistory = [...this.editHistory.slice(-19), draftVersion]
      }
      await this.refresh(this.generation, true)
    } catch (error) {
      const conflict = error instanceof ApiError && (error.status === 409 || error.status === 400)
      this.patch({
        error: conflict
          ? `${error instanceof Error ? error.message : '保存失败'}；已刷新为服务端当前版本，请重试。`
          : error instanceof Error ? error.message : '保存失败',
      })
      if (conflict) await this.refresh(this.generation, true)
    } finally {
      this.patch({ busy: false })
    }
  }

  /** Delivery repair: keep non-result references, replace the `result` deliverable. */
  async editDeliveries(requiredOutputs: Array<{ node_id: string; output_slot: string }>) {
    const view = this.state.view
    const binding = view?.binding
    const draft = view?.draft?.draft
    const metadata = view?.version?.node_metadata
    if (!binding || !draft || !metadata) { this.patch({ error: '当前没有可编辑的计划草稿。' }); return }
    if (this.generating) { this.patch({ error: '生成进行中；请等待完成或取消后再编辑。' }); return }
    const command = `cmd_plan_delivery_${draft.row_version}_${crypto.randomUUID().replace(/-/g, '').slice(0, 8)}`
    this.patch({ busy: true, error: null })
    try {
      await this.client.taskPlanEdit(this.workspace, this.session, {
        command_id: command,
        binding_id: binding.planning_binding_id,
        expected_version: draft.row_version,
        source: { ...draft.source, required_outputs: requiredOutputs },
        metadata,
      })
      this.editHistory = [...this.editHistory.slice(-19), draft.row_version]
      await this.refresh(this.generation, true)
    } catch (error) {
      this.patch({ error: error instanceof Error ? error.message : '保存失败' })
    } finally {
      this.patch({ busy: false })
    }
  }

  /** Undo the last accepted edit by restoring its preceding draft version. */
  async undoEdit() {
    const version = this.editHistory.at(-1)
    if (version === undefined) { this.patch({ error: '没有可撤回的编辑。' }); return }
    await this.editNode('restore', { restoreVersion: version })
    if (this.state.error === null) this.editHistory = this.editHistory.slice(0, -1)
  }
}

function commandIdFor(operation: 'generate' | 'revise'): string {
  const random = crypto.randomUUID().replace(/-/g, '').slice(0, 12)
  return `cmd_task_plan_${operation}_${random}`
}
