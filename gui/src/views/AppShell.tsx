import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'
import type { ApiClient } from '../api/client'
import type {
  ArtifactWire,
  RunViewWire,
  SessionWire,
  TaskRunWire,
  WorkflowRunWire,
} from '../api/types'
import type { SyncStore } from '../state/sync'
import { budgetDisplay } from './lib/budget'
import { ApprovalsBar } from './ApprovalsBar'
import { ContextBar } from './ContextBar'
import { ConnectionBanner } from './ConnectionBanner'
import { EditorShell } from './EditorShell'
import { PatchEditor } from './PatchEditor'
import { SessionNav } from './SessionNav'
import { TaskWorkspace } from './TaskWorkspace'
import { TopBar, type Theme } from './TopBar'
import { WorkflowPanel } from './WorkflowPanel'

/**
 * The three-column observer shell. All data comes from the sync store
 * (sessions, runs, approvals) or on-demand GETs (tasks, artifacts, run view);
 * nothing here mutates Core state.
 */
export function AppShell({
  client,
  store,
  workspaceId,
  theme,
  onThemeChange,
}: {
  client: ApiClient
  store: SyncStore
  workspaceId: string | null
  theme: Theme
  onThemeChange: (theme: Theme) => void
}) {
  const state = useSyncExternalStore(
    useCallback((listener: () => void) => store.subscribe(listener), [store]),
    useCallback(() => store.getState(), [store]),
  )

  const [expandedSessionId, setExpandedSessionId] = useState<string | null>(null)
  const [fetchedTasks, setFetchedTasks] = useState<Record<string, TaskRunWire[] | undefined>>({})
  const [selectedTask, setSelectedTask] = useState<TaskRunWire | null>(null)
  const [artifacts, setArtifacts] = useState<ArtifactWire[] | null>(null)
  const [openRunView, setOpenRunView] = useState<RunViewWire | null>(null)
  const [activeView, setActiveView] = useState<'observe' | 'edit'>('observe')
  // The edit-pending flow replaces the observer columns: pause → edit Future
  // nodes → preview diff + risk → confirm → continuation child.
  const [patchContext, setPatchContext] = useState<{
    run: WorkflowRunWire
    view: RunViewWire
  } | null>(null)

  const sessions: SessionWire[] = [...state.sessions.values()].sort((a, b) =>
    b.created_at.localeCompare(a.created_at),
  )

  // Live `task.created` events land in the store; merge them over the
  // per-session fetch cache so new tasks appear without a reload.
  const tasksBySession: Record<string, TaskRunWire[] | undefined> = { ...fetchedTasks }
  for (const task of state.tasks.values()) {
    const cached = tasksBySession[task.session_id]
    if (cached === undefined) continue
    const index = cached.findIndex((item) => item.task_run_id === task.task_run_id)
    if (index < 0) {
      tasksBySession[task.session_id] = [task, ...cached]
    } else if (cached[index]?.row_version !== task.row_version) {
      const next = [...cached]
      next[index] = task
      tasksBySession[task.session_id] = next
    }
  }

  const toggleSession = (sessionId: string) => {
    setExpandedSessionId((current) => (current === sessionId ? null : sessionId))
    if (fetchedTasks[sessionId] !== undefined) return
    client
      .listTasks(sessionId, { limit: 100 })
      .then((page) => {
        setFetchedTasks((cache) => ({
          ...cache,
          [sessionId]: [...page.tasks].sort((a, b) => b.created_at.localeCompare(a.created_at)),
        }))
      })
      .catch(() => {
        // Leave the slot uncached so the next expand retries.
      })
  }

  const selectTask = (task: TaskRunWire) => {
    setSelectedTask(task)
    setArtifacts(null)
    setOpenRunView(null)
    setPatchContext(null)
    client
      .listArtifacts({ task_run_id: task.task_run_id, limit: 100 })
      .then((page) => setArtifacts(page.artifacts))
      .catch(() => setArtifacts([]))
  }

  // The selected task may itself be updated by live events.
  useEffect(() => {
    if (selectedTask === null) return
    const fresh = state.tasks.get(selectedTask.task_run_id)
    if (fresh !== undefined && fresh.row_version !== selectedTask.row_version) {
      setSelectedTask(fresh)
    }
  }, [state.tasks, selectedTask])

  const runs = [...state.workflowRuns.values()].filter(
    (projection) => projection.run.root_task_run_id === selectedTask?.task_run_id,
  )
  const pendingApprovals = [...state.pendingApprovals.values()]
  const openBudget =
    openRunView !== null
      ? budgetDisplay(
          openRunView.agent_generation_request_count,
          openRunView.run.budget_snapshot.max_agent_generation_requests,
          openRunView.lineage_agent_generation_request_count,
        )
      : null

  return (
    <div className="flex h-screen flex-col">
      <TopBar
        workspaceId={workspaceId}
        connection={state.connection}
        pendingApprovals={pendingApprovals.length}
        theme={theme}
        onThemeChange={onThemeChange}
        activeView={activeView}
        onViewChange={setActiveView}
      />
      <ConnectionBanner connection={state.connection} onRetry={() => store.retry()} />
      <ContextBar client={client} taskId={selectedTask?.task_run_id ?? null}
        cursor={state.cursor} connected={state.connection === 'live'} />

      {activeView === 'edit' ? (
        <EditorShell client={client} />
      ) : patchContext !== null && workspaceId !== null ? (
        <PatchEditor
          client={client}
          run={patchContext.run}
          view={patchContext.view}
          workspaceId={workspaceId}
          onClose={() => setPatchContext(null)}
        />
      ) : (
      <main className="grid min-h-0 flex-1 grid-cols-[260px_minmax(0,1fr)_minmax(320px,420px)]">
        <div className="min-h-0 border-r border-subtle">
          <SessionNav
            sessions={sessions}
            expandedSessionId={expandedSessionId}
            tasksBySession={tasksBySession}
            selectedTaskId={selectedTask?.task_run_id ?? null}
            onToggleSession={toggleSession}
            onSelectTask={selectTask}
          />
        </div>
        <div className="min-h-0 border-r border-subtle">
          <TaskWorkspace
            client={client}
            task={selectedTask}
            artifacts={artifacts}
            terminalOutcome={openRunView?.terminal_outcome ?? null}
          />
        </div>
        <div className="min-h-0">
          {selectedTask === null ? (
            <WorkflowPanelPlaceholder />
          ) : (
            <WorkflowPanel
              key={selectedTask.task_run_id}
              client={client}
              store={store}
              runs={runs}
              onRunViewChange={setOpenRunView}
              onEditPending={(run, view) => setPatchContext({ run, view })}
            />
          )}
        </div>
      </main>

      )}

      {activeView === 'observe' && patchContext === null && <ApprovalsBar
        client={client}
        run={openRunView?.run ?? null}
        budget={openBudget}
        pendingApprovals={pendingApprovals}
      />}
    </div>
  )
}

function WorkflowPanelPlaceholder() {
  return (
    <section className="flex h-full flex-col" aria-label="工作流">
      <h2 className="px-4 pt-4 pb-2 text-xs font-medium tracking-wide text-secondary">工作流</h2>
      <p className="px-4 py-10 text-center text-sm text-secondary">选择任务后显示工作流状态。</p>
    </section>
  )
}
