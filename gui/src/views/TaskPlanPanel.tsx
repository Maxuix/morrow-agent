import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import type { ApiClient } from '../api/client'
import type { SyncStore } from '../state/sync'
import { useRunView } from '../state/runView'
import type {
  SessionOwnershipWire,
  TaskPlanCandidateWire,
  TaskPlanFrozenPlanWire,
  TaskPlanViewWire,
  WorkflowDraftDiagnosticWire,
} from '../api/types'
import type { TaskPlanState, TaskPlanStore } from '../state/taskPlan'
import {
  blockerText,
  latestOperation,
  planningDiagnosticText,
  taskPlanPhase,
  taskPlanPhaseLabel,
  taskPlanStatusHint,
} from './lib/taskPlan'
import { buildGraphLayout, parseRevision } from './lib/graph'
import { WORKFLOW_STATUS_LABELS, shortId } from './lib/labels'
import { nodeStatusText } from './lib/execution'
import { PlanReviewWorkspace } from './PlanReviewWorkspace'
import { RunGraph } from './RunGraph'

/**
 * Right-side task-plan panel: the single Chat surface for generate → review →
 * edit → explicitly start → run status. The current plan review (draft graph
 * + node editor) is the main view; during execution without a pending
 * modification candidate the frozen run topology leads instead, with node
 * status overlaid. All facts come from the server projection; the panel never
 * fabricates DAG or run state.
 */
export function TaskPlanPanel({ store, client, syncStore, connection, showRun, onPreferRun, onReturnToRoot, active = true }: {
  store: TaskPlanStore
  client: ApiClient
  syncStore: SyncStore
  connection: string
  /** Frozen-run topology leads the panel (scenario rules resolved by the caller). */
  showRun: boolean
  onPreferRun: (preferRun: boolean) => void
  onReturnToRoot?: (() => void) | null
  /** Inactive Inspector tabs stay mounted but must not start run-view reads. */
  active?: boolean
}) {
  const state = useSyncExternalStore(store.subscribe, store.getState)
  const { view } = state
  const phase = taskPlanPhase(view)
  const operation = view === null ? null : latestOperation(view)
  // After a reconnect, reconcile once from the authoritative view; the event
  // tail then resumes at the current sequence.
  const previousConnection = useRef(connection)
  useEffect(() => {
    if (!active) return
    if (previousConnection.current !== 'live' && connection === 'live') void store.refresh()
    previousConnection.current = connection
  }, [active, connection, store])

  const draftDiagnostics: WorkflowDraftDiagnosticWire[] = view?.draft?.draft.diagnostics ?? []
  const runView = useRunView(client, syncStore, view?.run?.workflow_run_id ?? null, active)
  const showDraft = view !== null && view.draft !== null && !showRun
  const [reviewFullscreen, setReviewFullscreen] = useState(false)

  // An isolated workflow-node conversation owns no planning binding of its own:
  // it shows the owning run and the frozen plan version it was admitted with,
  // never the root session's current (possibly regenerated) draft.
  if (view !== null && view.ownership?.role === 'node') {
    return (
      <section className="flex h-full flex-col overflow-y-auto" aria-label="任务计划">
        <h2 className="px-4 pt-4 pb-2 text-xs font-medium tracking-wide text-secondary">任务计划</h2>
        <div className="flex flex-col gap-3 px-4 pb-4 text-sm">
          {state.loadError !== null && (
            <p role="alert" className="text-xs text-failed">{state.loadError}{' '}
              <button type="button" className="editor-button" onClick={() => void store.refresh()}>重试</button>
            </p>
          )}
          <ExecutionOwnershipCard ownership={view.ownership} plan={view.plan} onReturnToRoot={onReturnToRoot} />
        </div>
      </section>
    )
  }
  return (
    <section className="flex h-full flex-col overflow-y-auto" aria-label="任务计划">
      <h2 className="px-4 pt-4 pb-2 text-xs font-medium tracking-wide text-secondary">任务计划</h2>
      <div className="flex flex-col gap-3 px-4 pb-4 text-sm">
        {state.loadError !== null && (
          <p role="alert" className="text-xs text-failed">{state.loadError}{' '}
            <button type="button" className="editor-button" onClick={() => void store.refresh()}>重试</button>
          </p>
        )}
        <PlanStatusLine phase={phase} view={view} operation={operation} />
        {phase === 'generating' && (
          <button type="button" className="editor-button" disabled={state.busy}
            onClick={() => void store.cancelGeneration()}>
            取消生成
          </button>
        )}
        {showRun && view !== null && view.run !== null && (
          <FrozenRunTopology
            run={view.run}
            runView={runView}
            draftAvailable={view.draft !== null}
            onShowDraft={() => onPreferRun(false)}
          />
        )}
        {showDraft && view !== null && (
          <div className="flex flex-col gap-3">
            {view.run !== null && (
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <button type="button" className="editor-button" onClick={() => onPreferRun(true)}>
                  查看运行图
                </button>

              </div>
            )}
            <PlanReviewWorkspace
              store={store}
              state={state}
              connection={connection}
              phase={phase}
              fullscreen={reviewFullscreen}
              onToggleFullscreen={() => setReviewFullscreen((value) => !value)}
            />
          </div>
        )}
        {view !== null && view.draft === null && view.binding !== null && (
          <p className="text-xs text-secondary">当前计划还没有草稿；重新发送任务描述即可生成。</p>
        )}
        {(phase === 'error' || phase === 'invalid' || phase === 'needs_input') && (
          <p className="text-xs text-failed">
            {phase === 'error' && operation !== null
              ? planningDiagnosticText(operation.diagnostics) ||
                '计划未生成（已取消或未产出）；重新发送任务描述即可重新生成。'
              : blockerText(view?.execution.blockers ?? [])}
          </p>
        )}
        {draftDiagnostics.length > 0 && (
          <ul className="space-y-1 rounded-[8px] border border-subtle p-2 text-xs" aria-label="计划校验结果">
            {draftDiagnostics.map((item, index) => (
              <li key={index} className={item.severity === 'error' ? 'text-failed' : 'text-blocked'}>
                {item.severity === 'error' ? '错误' : '警告'} · {item.code}
                {item.node_id !== null ? ` · 节点 ${item.node_id}` : ''}：{item.message}
              </li>
            ))}
          </ul>
        )}
        {state.error !== null && <p role="alert" className="text-xs text-failed">{state.error}</p>}

        {connection !== 'live' && (
          <p role="status" className="text-xs text-blocked">连接中断，正在重连。</p>
        )}
        {view !== null && view.candidate !== null && view.candidate.origin === 'replan' && (
          <PlanAdjustmentCard candidate={view.candidate} />
        )}
        {view !== null && view.run !== null && (
          <details><summary className="cursor-pointer text-xs text-secondary">执行详情</summary><RunStatusCard view={view} phase={phase} /></details>
        )}
        {view !== null && view.run !== null && view.allowed_actions.includes('repair') && (
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className="editor-button border-accent text-accent" disabled={state.busy || connection !== 'live'}
              onClick={() => void store.prepareRepair()}>生成修复计划</button>

          </div>
        )}
        {(phase === 'running' || phase === 'pausing' || phase === 'paused') && view !== null && (
          <RunControlActions view={view} store={store} state={state} connection={connection} />
        )}
        {operation?.usage !== undefined && (
          <details className="text-xs text-secondary"><summary>生成用量</summary><p className="font-mono">
            {operation.usage.attempts} 次规划请求 ·{' '}
            {operation.usage.availability === 'available' && operation.usage.total_tokens !== null
              ? `${operation.usage.total_tokens} tokens${operation.usage.cost_amount_minor !== null ? ` · ${operation.usage.cost_amount_minor}${operation.usage.cost_currency ?? ''}` : ''}`
              : '用量不可用'}
          </p></details>
        )}
      </div>
    </section>
  )
}

/** Read-only frozen run topology with live node status overlaid; the graph
 *  never changes after start even when the draft is edited later. */
function FrozenRunTopology({ run, runView, draftAvailable, onShowDraft }: {
  run: NonNullable<TaskPlanViewWire['run']>
  runView: import('../api/types').RunViewWire | null
  draftAvailable: boolean
  onShowDraft: () => void
}) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const layout = useMemo(
    () => (runView === null ? null : buildGraphLayout(parseRevision(runView.revision), runView.nodes)),
    [runView],
  )
  const selectedNode = useMemo(
    () =>
      selectedNodeId === null
        ? null
        : runView?.nodes.find((item) => item.node.node_id === selectedNodeId) ?? null,
    [runView, selectedNodeId],
  )
  return (
    <section className="flex flex-col gap-2" aria-label="运行拓扑（冻结）">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded-[8px] border border-subtle px-1.5 py-0.5 text-secondary">
          运行图 · 只读
        </span>
        {draftAvailable && (
          <button type="button" className="editor-button" onClick={onShowDraft}>查看当前草稿</button>
        )}

      </div>
      {runView === null || layout === null ? (
        <p className="rounded-[10px] border border-subtle bg-raised p-3 text-xs text-secondary">运行详情加载中…</p>
      ) : (
        <RunGraph layout={layout} onSelectNode={setSelectedNodeId} />
      )}
      {selectedNodeId !== null && (
        <p className="text-xs text-secondary">
          节点 {selectedNodeId}
          {selectedNode !== null
            ? ` · 状态 ${nodeStatusText(
                WORKFLOW_STATUS_LABELS[selectedNode.node.status] ?? selectedNode.node.status,
                selectedNode.execution,
              )}`
            : ''}
          {run.active_node_ids.includes(selectedNodeId) ? ' · 仍在活动' : ''}；完整运行详情见下方运行记录。
        </p>
      )}
    </section>
  )
}

export function RunStatusCard({ view, phase }: { view: TaskPlanViewWire; phase: ReturnType<typeof taskPlanPhase> }) {
  const run = view.run
  if (run === null) return null
  return (
    <section className="rounded-[10px] border border-subtle bg-raised p-3 text-xs" aria-label="当前运行">
      <p>运行 {run.workflow_run_id.slice(-8)} · 状态 {WORKFLOW_STATUS_LABELS[run.status] ?? run.status}
        {run.pause_requested === true && run.status !== 'paused' ? ' · 已请求暂停' : ''}
        {run.result_status !== null && (run.result_status === 'succeeded' ? ' · 成功' : ' · 需要修订')}</p>
      {(phase === 'pausing' || run.status === 'draining') && (
        <p role="status" className="mt-1 text-blocked">正在暂停…</p>
      )}
      {view.candidate !== null && view.candidate.origin !== 'replan' && (
        <p className="mt-1 text-secondary">
          修改摘要：已执行锁定 {view.candidate.past_node_ids.length} 个
          {view.candidate.added_node_ids !== undefined && view.candidate.added_node_ids.length > 0 ? ` · 新增 ${view.candidate.added_node_ids.join(', ')}` : ''}
          {view.candidate.removed_node_ids !== undefined && view.candidate.removed_node_ids.length > 0 ? ` · 删除 ${view.candidate.removed_node_ids.join(', ')}` : ''}
          {view.candidate.stable ? ' · 已暂停' : ' · 正在暂停'}
        </p>
      )}
      <p className="mt-1 text-secondary">进行中节点：{run.active_node_ids.length > 0 ? run.active_node_ids.join(', ') : '无'}</p>
      {run.nodes.length > 0 && (
        <ul className="mt-1 space-y-0.5" aria-label="节点状态">
          {run.nodes.map((node) => (
            <li key={node.node_id}>
              {node.node_id} · {nodeStatusText(
                WORKFLOW_STATUS_LABELS[node.status] ?? node.status,
                node.execution,
              )}
              {node.inherited === true ? ' · 继承自上一运行' : ''}
              {run.active_node_ids.includes(node.node_id) ? ' · 仍在活动' : ''}
            </li>
          ))}
        </ul>
      )}
      <details><summary>运行用量</summary><p className="mt-1 font-mono text-secondary">
        {run.agent_generation_request_count ?? 0} 次模型请求（累计 {run.lineage_agent_generation_request_count ?? 0}）
      </p></details>

    </section>
  )
}

function RunControlActions({ view, store, state, connection }: {
  view: TaskPlanViewWire
  store: TaskPlanStore
  state: TaskPlanState
  connection: string
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {view.allowed_actions.includes('pause') && (
        <button type="button" className="editor-button" disabled={state.busy || connection !== 'live'}
          onClick={() => void store.pause()}>暂停后续准入</button>
      )}
      {view.allowed_actions.includes('change') && (
        <button type="button" className="editor-button border-accent text-accent" disabled={state.busy || connection !== 'live'}
          onClick={() => void store.prepareChange()}>修改后续计划</button>
      )}
      {view.allowed_actions.includes('save_candidate') && (
        <button type="button" className="editor-button" disabled={state.busy || connection !== 'live'}
          onClick={() => void store.applyChange('save_candidate')}>只保存修改</button>
      )}
      {view.allowed_actions.includes('accept_change') && (
        <button type="button" className="editor-button border-accent text-accent" disabled={state.busy || connection !== 'live' || view.candidate?.stable !== true}
          onClick={() => void store.applyChange('accept_change')}>采纳并继续</button>
      )}
      {view.allowed_actions.includes('reject_change') && (
        <button type="button" className="editor-button" disabled={state.busy || connection !== 'live'}
          onClick={() => void store.applyChange('reject_change')}>放弃修改</button>
      )}
      {view.allowed_actions.includes('resume') && (
        <button type="button" className="editor-button" disabled={state.busy || connection !== 'live'}
          onClick={() => void store.resumeRun()}>继续原计划</button>
      )}
      {view.candidate !== null && view.candidate.stable !== true && (
        <span className="text-xs text-blocked">正在暂停；可起草修改，但要等暂停落定后才能采纳继续。</span>
      )}
      {connection !== 'live' && <span className="text-xs text-blocked">连接中断，暂不能暂停或修改。</span>}
    </div>
  )
}

export function PlanAdjustmentCard({ candidate }: { candidate: TaskPlanCandidateWire }) {
  const added = candidate.added_node_ids ?? []
  const removed = candidate.removed_node_ids ?? []
  const changed = candidate.changed_node_ids ?? []
  return (
    <article className="rounded-[10px] border border-accent p-3 text-xs" aria-label="计划调整">
      <h3 className="text-sm font-medium">计划调整</h3>
      <p className="mt-1 text-secondary">
        {candidate.reason ?? '系统建议调整尚未开始的工作'}
      </p>
      <p className="mt-1">
        已执行锁定 {candidate.past_node_ids.length} 个
        {added.length > 0 ? ` · 新增 ${added.join(', ')}` : ''}
        {removed.length > 0 ? ` · 删除 ${removed.join(', ')}` : ''}
        {changed.length > 0 ? ` · 任务变更 ${changed.join(', ')}` : ''}
      </p>
      <p className="mt-1 text-secondary">
        {candidate.stable ? '待确认' : '暂停完成后可应用修改。'}
      </p>
    </article>
  )
}

function PlanStatusLine({ phase, view, operation }: {
  phase: ReturnType<typeof taskPlanPhase>
  view: TaskPlanViewWire | null
  operation: TaskPlanViewWire['operations'][number] | null
}) {
  const label = taskPlanPhaseLabel(phase)
  return (
    <div className="rounded-[10px] border border-subtle bg-raised p-3">
      <p className="text-sm font-medium">{label}</p>
      {taskPlanStatusHint(phase, view, operation) && <p className="mt-1 text-xs text-secondary">{taskPlanStatusHint(phase, view, operation)}</p>}

    </div>
  )
}

const ISSUE_HINTS: Record<string, string> = {
  frozen_draft_missing: '执行时冻结的计划草稿已不可读；运行定义与 DAG 仍见下方运行详情。',
}

/**
 * Ownership banner for an isolated workflow-node session: which task and node
 * it executes, the owning run, and the plan version that run was admitted
 * with. Control actions stay in the root conversation and the run details
 * below; this card is informational by design.
 */
export function ExecutionOwnershipCard({ ownership, plan, onReturnToRoot }: {
  ownership: SessionOwnershipWire
  plan: TaskPlanFrozenPlanWire | null
  onReturnToRoot?: (() => void) | null
}) {
  return (
    <article className="rounded-[10px] border border-accent bg-raised p-3 text-xs" aria-label="执行归属">
      <p className="text-sm font-medium">
        工作流执行节点{ownership.node_id ? ` · ${ownership.node_id}` : ''}
        {ownership.node_attempt !== null && ownership.node_attempt > 1 ? `（第 ${ownership.node_attempt} 次尝试）` : ''}
      </p>
      <p className="mt-1">
        节点状态 {ownership.node_status ? WORKFLOW_STATUS_LABELS[ownership.node_status] ?? ownership.node_status : '未知'}
        {ownership.workflow_run_id !== null && ownership.workflow_status !== null && (
          <> · 所属运行 {shortId(ownership.workflow_run_id)} · {WORKFLOW_STATUS_LABELS[ownership.workflow_status] ?? ownership.workflow_status}</>
        )}
      </p>
      {plan?.mode === 'frozen' && (
        <p className="mt-1">
          执行依据：冻结计划 v{plan.draft_version ?? '—'}
          {plan.origin === 'repair' ? '（修复计划）' : ''}
        </p>
      )}
      {plan?.mode === 'frozen' && plan.source !== null && plan.source.nodes.length > 0 && (
        <ul className="mt-1 space-y-0.5" aria-label="冻结计划节点">
          {plan.source.nodes.map((node) => (
            <li key={node.node_id}>
              {node.node_id} · {node.task_contract.objective}
            </li>
          ))}
        </ul>
      )}
      {plan?.mode === 'direct' && (
        <p className="mt-1">已发布工作流</p>
      )}
      {ownership.issues.length > 0 && (
        <ul role="alert" className="mt-1 space-y-0.5 text-failed" aria-label="归属提示">
          {ownership.issues.map((issue) => (
            <li key={issue}>{ISSUE_HINTS[issue] ?? `归属信息不完整：${issue}`}</li>
          ))}
        </ul>
      )}

      {onReturnToRoot && ownership.root_session_id !== null && (
        <button type="button" className="editor-button mt-2" onClick={onReturnToRoot}>返回主对话</button>
      )}
    </article>
  )
}
