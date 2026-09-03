import type { SessionWire, TaskRunWire } from '../api/types'
import { EmptyState } from '../components/EmptyState'
import { formatTimestamp } from './lib/labels'

/**
 * Left nav: sessions (from the sync store) expand to their tasks
 * (fetched per session, merged with live store tasks by the caller).
 * Selection is a plain button list — fully keyboard reachable.
 */
export function SessionNav({
  sessions,
  expandedSessionId,
  tasksBySession,
  selectedTaskId,
  onToggleSession,
  onSelectTask,
}: {
  sessions: SessionWire[]
  expandedSessionId: string | null
  tasksBySession: Record<string, TaskRunWire[] | undefined>
  selectedTaskId: string | null
  onToggleSession: (sessionId: string) => void
  onSelectTask: (task: TaskRunWire) => void
}) {
  return (
    <nav aria-label="会话与任务" className="flex h-full flex-col overflow-y-auto">
      <h2 className="px-4 pt-4 pb-2 text-xs font-medium tracking-wide text-secondary">会话</h2>
      {sessions.length === 0 ? (
        <EmptyState title="暂无会话" hint="在 CLI 中开始一次对话后，会话会出现在这里。" />
      ) : (
        <ul className="flex flex-col gap-0.5 px-2 pb-4">
          {sessions.map((session) => {
            const expanded = session.session_id === expandedSessionId
            const tasks = tasksBySession[session.session_id]
            return (
              <li key={session.session_id}>
                <button
                  type="button"
                  aria-expanded={expanded}
                  onClick={() => onToggleSession(session.session_id)}
                  className="flex w-full items-baseline gap-2 rounded-[8px] px-2 py-1.5 text-left text-sm transition-colors duration-150 hover:bg-base"
                >
                  <span aria-hidden="true" className="text-xs text-secondary">
                    {expanded ? '▾' : '▸'}
                  </span>
                  <span className="font-mono text-xs">{session.session_id}</span>
                  <span className="ml-auto text-xs text-secondary">
                    {formatTimestamp(session.created_at)}
                  </span>
                </button>
                {expanded && (
                  <ul className="mt-0.5 ml-5 flex flex-col gap-0.5 border-l border-subtle pl-2">
                    {tasks === undefined ? (
                      <li className="px-2 py-1 text-xs text-secondary">加载中…</li>
                    ) : tasks.length === 0 ? (
                      <li className="px-2 py-1 text-xs text-secondary">暂无任务</li>
                    ) : (
                      tasks.map((task) => (
                        <li key={task.task_run_id}>
                          <button
                            type="button"
                            aria-current={task.task_run_id === selectedTaskId ? 'true' : undefined}
                            onClick={() => onSelectTask(task)}
                            className={`w-full rounded-[8px] px-2 py-1.5 text-left font-mono text-xs transition-colors duration-150 hover:bg-base ${
                              task.task_run_id === selectedTaskId
                                ? 'bg-base text-accent'
                                : 'text-primary'
                            }`}
                          >
                            {task.task_run_id}
                          </button>
                        </li>
                      ))
                    )}
                  </ul>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </nav>
  )
}
