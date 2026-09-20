import { Suspense, useSyncExternalStore } from 'react'
import { TaskPlanPanel } from '../TaskPlanPanel'
import { WorkflowPanel } from '../WorkflowPanel'
import type { InspectorBodyProps } from './InspectorBodyProps'
import type { WorkflowRunProjection } from '../../state/sync'

function WorkflowUnavailable({ message }: { message: string }) {
  return <section className="flex h-full flex-col overflow-y-auto" aria-label="计划与工作流" data-inspector-kind="workflow">
    <h2 className="px-4 pt-4 pb-2 text-xs font-medium tracking-wide text-secondary">计划与工作流</h2>
    <p className="px-4 pb-4 text-sm text-secondary">{message}</p>
  </section>
}

/**
 * The one mounted workflow body. Root sessions let TaskPlanPanel lead with
 * the editable/frozen plan graph; node sessions let WorkflowPanel lead with
 * the owning run graph while the plan panel stays an ownership summary.
 */
export function PlanWorkflowInspector({ target, active, client, syncStore, planStore, connection, runs, showRun = false, onPreferRun, onReturnToRoot, onWorkflowEdit, focusNodeId }: InspectorBodyProps) {
  if (!client || !syncStore || !planStore || connection === undefined) {
    return <WorkflowUnavailable message="计划与工作流需要在 Chat 会话中打开。" />
  }
  return <PlanWorkflowContent
    target={target}
    active={active}
    client={client}
    syncStore={syncStore}
    planStore={planStore}
    connection={connection}
    runs={runs}
    showRun={showRun}
    onPreferRun={onPreferRun}
    onReturnToRoot={onReturnToRoot}
    onWorkflowEdit={onWorkflowEdit}
    focusNodeId={focusNodeId}
  />
}

function PlanWorkflowContent({ target, active, client, syncStore, planStore, connection, runs, showRun, onPreferRun, onReturnToRoot, onWorkflowEdit, focusNodeId }: InspectorBodyProps & {
  client: NonNullable<InspectorBodyProps['client']>
  syncStore: NonNullable<InspectorBodyProps['syncStore']>
  planStore: NonNullable<InspectorBodyProps['planStore']>
  connection: string
}) {
  const planState = useSyncExternalStore(planStore.subscribe, planStore.getState)
  const ownership = planState.view?.ownership
  const displayRun = showRun && planState.view?.run !== null && planState.view?.run !== undefined
  const targetRunId = target?.workflowRunId ?? (ownership?.role === 'node' ? ownership.workflow_run_id : null)
  const targetRun = targetRunId === null
    ? undefined
    : syncStore.getState().workflowRuns.get(targetRunId)
  const effectiveRuns: WorkflowRunProjection[] = targetRun !== undefined && !(runs ?? []).some(
    run => run.run.workflow_run_id === targetRunId,
  )
    ? [...(runs ?? []), targetRun]
    : runs ?? []
  return <section className="flex h-full flex-col overflow-y-auto" aria-label="计划与工作流" data-inspector-kind="workflow">
    <TaskPlanPanel
      store={planStore}
      client={client}
      syncStore={syncStore}
      connection={connection}
      showRun={displayRun ?? false}
      onPreferRun={onPreferRun ?? (() => {})}
      onReturnToRoot={onReturnToRoot}
      active={active}
    />
    <div className="border-t border-subtle">
      <Suspense fallback={<p className="p-3 text-sm text-secondary">正在加载运行图…</p>}>
        <WorkflowPanel
          client={client}
          store={syncStore}
          runs={effectiveRuns}
          onRunViewChange={() => {}}
          onEditPending={onWorkflowEdit}
          focusNodeId={focusNodeId ?? (ownership?.role === 'node' ? ownership.node_id : null)}
          hideGraph={ownership?.role !== 'node'}
          initialRunId={targetRunId}
          active={active}
        />
      </Suspense>
    </div>
    {!active && <span className="sr-only">计划与工作流已保留在后台</span>}
  </section>
}
