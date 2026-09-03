import { useEffect, useMemo, useRef, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { RunViewWire } from '../api/types'
import { EmptyState } from '../components/EmptyState'
import { StatusDot } from '../components/StatusDot'
import type { SyncStore, WorkflowRunProjection } from '../state/sync'
import { budgetDisplay, type BudgetDisplay } from './lib/budget'
import { buildGraphLayout, isDirectRun, parseRevision } from './lib/graph'
import { RUN_RELATION_LABELS, shortId } from './lib/labels'
import { DirectNodeCard } from './DirectNodeCard'
import { NodeDetail } from './NodeDetail'
import { RunGraph } from './RunGraph'

/**
 * Cheap change signature for a run projection: the run row version plus the
 * per-node row versions the store patches in place. Any status/node event
 * that touched the run moves this value, which schedules a view refetch.
 */
export function runProjectionSignature(projection: WorkflowRunProjection | undefined): string {
  if (projection === undefined) return 'absent'
  const nodes = projection.view?.nodes.map((item) => item.node.row_version).join(',') ?? 'noview'
  return `${projection.run.row_version}:${projection.run.status}:${nodes}`
}

const REFETCH_DEBOUNCE_MS = 150

/**
 * Right panel: run selector (with lineage labels), run header, budget, and
 * the graph — Direct runs render as a linear card, multi-node runs as a
 * read-only React Flow canvas. The open run view is refetched (debounced)
 * whenever the sync store reports a change for that run; no polling loops.
 */
export function WorkflowPanel({
  client,
  store,
  runs,
  onRunViewChange,
}: {
  client: ApiClient
  store: SyncStore
  runs: WorkflowRunProjection[]
  onRunViewChange: (view: RunViewWire | null) => void
}) {
  const [chosenRunId, setChosenRunId] = useState<string | null>(null)
  const [runView, setRunView] = useState<RunViewWire | null>(null)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const fetchedSignature = useRef<string>('')

  const orderedRuns = useMemo(
    () =>
      [...runs].sort((a, b) =>
        (b.run.started_at ?? b.run.admission_deadline_at).localeCompare(
          a.run.started_at ?? a.run.admission_deadline_at,
        ),
      ),
    [runs],
  )
  const selectedRun =
    orderedRuns.find((item) => item.run.workflow_run_id === chosenRunId) ?? orderedRuns[0] ?? null
  const selectedRunId = selectedRun?.run.workflow_run_id ?? null

  // Fetch the run view on selection change, then keep it fresh off store
  // events: a moved signature schedules a debounced refetch.
  useEffect(() => {
    if (selectedRunId === null) {
      setRunView(null)
      onRunViewChange(null)
      return
    }
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const refetch = () => {
      const signature = runProjectionSignature(store.getState().workflowRuns.get(selectedRunId))
      client
        .getRunView(selectedRunId)
        .then((view) => {
          if (cancelled) return
          fetchedSignature.current = signature
          setRunView(view)
          onRunViewChange(view)
        })
        .catch(() => {
          // A transient failure leaves the previous view on screen; the next
          // store event retries via the same signature check.
        })
    }
    refetch()

    const unsubscribe = store.subscribe(() => {
      const signature = runProjectionSignature(store.getState().workflowRuns.get(selectedRunId))
      if (signature === fetchedSignature.current || timer !== null) return
      timer = setTimeout(() => {
        timer = null
        refetch()
      }, REFETCH_DEBOUNCE_MS)
    })
    return () => {
      cancelled = true
      if (timer !== null) clearTimeout(timer)
      unsubscribe()
    }
  }, [client, store, selectedRunId, onRunViewChange])

  if (selectedRun === null) {
    return (
      <section className="flex h-full flex-col overflow-y-auto" aria-label="工作流">
        <EmptyState title="该任务暂无运行记录" hint="运行工作流后，这里会显示节点图与状态。" />
      </section>
    )
  }

  const graph = runView !== null ? parseRevision(runView.revision) : null
  const budget: BudgetDisplay = budgetDisplay(
    runView?.agent_generation_request_count ?? 0,
    selectedRun.run.budget_snapshot.max_agent_generation_requests,
    runView?.lineage_agent_generation_request_count ?? 0,
  )
  const direct = graph !== null && isDirectRun(graph)
  const layout = graph !== null && !direct ? buildGraphLayout(graph, runView?.nodes ?? []) : null
  const effectiveSelectedNodeId =
    selectedNodeId ?? (direct && graph !== null ? graph.nodes[0]?.node_id : null) ?? null
  const selectedNodeView =
    runView?.nodes.find((item) => item.node.node_id === effectiveSelectedNodeId) ?? null
  const selectedRevisionNode =
    graph?.nodes.find((node) => node.node_id === effectiveSelectedNodeId) ?? null

  return (
    <section className="flex h-full flex-col overflow-y-auto" aria-label="工作流">
      <h2 className="px-4 pt-4 pb-2 text-xs font-medium tracking-wide text-secondary">工作流</h2>

      {orderedRuns.length > 1 && (
        <ul aria-label="运行列表" className="flex flex-col gap-0.5 px-3 pb-2">
          {orderedRuns.map((item) => (
            <li key={item.run.workflow_run_id}>
              <button
                type="button"
                aria-current={
                  item.run.workflow_run_id === selectedRun.run.workflow_run_id ? 'true' : undefined
                }
                onClick={() => {
                  setChosenRunId(item.run.workflow_run_id)
                  setSelectedNodeId(null)
                }}
                className={`flex w-full items-center gap-2 rounded-[8px] px-2 py-1.5 text-left text-xs transition-colors duration-150 hover:bg-base ${
                  item.run.workflow_run_id === selectedRun.run.workflow_run_id
                    ? 'bg-base text-accent'
                    : 'text-primary'
                }`}
              >
                <StatusDot status={item.run.status} />
                <span className="font-mono">{shortId(item.run.workflow_run_id)}</span>
                <span className="ml-auto text-secondary">
                  {RUN_RELATION_LABELS[item.run.run_relation]}
                  {item.run.parent_run_id !== null && ` ← ${shortId(item.run.parent_run_id)}`}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="flex flex-col gap-3 px-4 pb-4">
        <header className="rounded-[10px] border border-subtle bg-raised p-3">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="font-serif text-base font-medium">
              {graph?.name ?? '未命名工作流'}
            </span>
            <StatusDot status={selectedRun.run.status} className="text-xs" />
            <span className="font-mono text-xs text-secondary">
              {shortId(selectedRun.run.workflow_revision_id)}
            </span>
            {selectedRun.run.pause_requested && (
              <span className="rounded-[8px] border border-paused px-1.5 py-0.5 text-xs text-paused">
                已请求暂停
              </span>
            )}
          </div>
          <div className="mt-2 font-mono text-xs text-secondary">
            预算 {budget.current}
            {budget.lineage !== null && ` · ${budget.lineage}`}
          </div>
          {selectedRun.run.result_status !== null && (
            <div className="mt-1 text-xs text-secondary">
              结果：{selectedRun.run.result_status === 'succeeded' ? '成功' : '需要修订'}
            </div>
          )}
        </header>

        {runView !== null && runView.inherited_artifacts.length > 0 && (
          <div className="rounded-[10px] border border-subtle bg-raised p-3 text-xs">
            <h3 className="font-medium tracking-wide text-secondary">
              继承的 Artifact（{runView.inherited_artifacts.length}）
            </h3>
            <ul className="mt-1 flex flex-col gap-0.5 font-mono text-secondary">
              {runView.inherited_artifacts.map((item) => (
                <li key={`${item.source_node_run_id}:${item.output_slot}`}>
                  {item.source_node_id}.{item.output_slot} ← {shortId(item.source_workflow_run_id)}
                </li>
              ))}
            </ul>
          </div>
        )}

        {graph === null ? (
          <EmptyState title="运行详情加载中…" />
        ) : direct ? (
          <DirectNodeCard
            nodeView={selectedNodeView}
            revisionNode={graph.nodes[0] as (typeof graph.nodes)[number]}
            selected={effectiveSelectedNodeId === graph.nodes[0]?.node_id}
            onSelect={() => setSelectedNodeId(graph.nodes[0]?.node_id ?? null)}
          />
        ) : (
          layout !== null && (
            <RunGraph
              layout={layout}
              selectedNodeId={effectiveSelectedNodeId}
              onSelectNode={setSelectedNodeId}
            />
          )
        )}

        {effectiveSelectedNodeId !== null &&
          (selectedNodeView !== null ? (
            <NodeDetail
              nodeView={selectedNodeView}
              revisionNode={selectedRevisionNode}
              client={client}
            />
          ) : (
            <div className="rounded-[10px] border border-subtle bg-raised p-4 text-sm text-secondary">
              节点 <span className="font-mono text-xs">{effectiveSelectedNodeId}</span>{' '}
              尚未创建运行记录。
            </div>
          ))}
      </div>
    </section>
  )
}
