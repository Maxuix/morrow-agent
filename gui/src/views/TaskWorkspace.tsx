import { useState } from 'react'
import type { ArtifactWire, TaskOutcomeWire, TaskRunWire } from '../api/types'
import { ArtifactList } from '../components/ArtifactList'
import { EmptyState } from '../components/EmptyState'
import { formatTimestamp, TASK_STATUS_LABELS } from './lib/labels'

const PURPOSE_LABELS: Record<TaskRunWire['purpose'], string> = {
  user: '用户任务',
  workflow_node: '工作流节点',
}

type Tab = 'task' | 'artifacts'

/**
 * Center workspace: the selected task's facts plus its artifacts. There is no
 * chat-history endpoint, so this surface is deliberately Task + Artifacts —
 * no fake chat view.
 */
export function TaskWorkspace({
  task,
  artifacts,
  terminalOutcome,
}: {
  task: TaskRunWire | null
  artifacts: ArtifactWire[] | null
  terminalOutcome: TaskOutcomeWire | null
}) {
  const [tab, setTab] = useState<Tab>('task')

  if (task === null) {
    return (
      <section className="flex h-full flex-col overflow-y-auto">
        <EmptyState title="选择左侧的任务以查看详情" />
      </section>
    )
  }

  return (
    <section className="flex h-full flex-col overflow-hidden" aria-label="任务详情">
      <div role="tablist" aria-label="任务视图" className="flex gap-1 border-b border-subtle px-4 pt-3">
        {(
          [
            ['task', '任务'],
            ['artifacts', 'Artifacts'],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={tab === value}
            onClick={() => setTab(value)}
            className={`rounded-t-[8px] px-3 py-1.5 text-sm transition-colors duration-150 ${
              tab === value
                ? 'border border-b-raised border-subtle bg-raised text-accent'
                : 'text-secondary hover:text-primary'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <div role="tabpanel" className="min-h-0 flex-1 overflow-y-auto p-4">
        {tab === 'task' ? (
          <dl className="flex flex-col gap-3 text-sm">
            <div className="flex items-baseline gap-2">
              <dt className="w-20 shrink-0 text-secondary">状态</dt>
              <dd>{TASK_STATUS_LABELS[task.status]}</dd>
            </div>
            <div className="flex items-baseline gap-2">
              <dt className="w-20 shrink-0 text-secondary">类型</dt>
              <dd>{PURPOSE_LABELS[task.purpose]}</dd>
            </div>
            <div className="flex items-baseline gap-2">
              <dt className="w-20 shrink-0 text-secondary">尝试次数</dt>
              <dd className="font-mono">{task.attempt}</dd>
            </div>
            <div className="flex items-baseline gap-2">
              <dt className="w-20 shrink-0 text-secondary">创建时间</dt>
              <dd className="font-mono text-xs">{formatTimestamp(task.created_at)}</dd>
            </div>
            <div className="flex items-baseline gap-2">
              <dt className="w-20 shrink-0 text-secondary">更新时间</dt>
              <dd className="font-mono text-xs">{formatTimestamp(task.updated_at)}</dd>
            </div>
            <div className="flex items-baseline gap-2">
              <dt className="w-20 shrink-0 text-secondary">关闭时间</dt>
              <dd className="font-mono text-xs">{formatTimestamp(task.closed_at)}</dd>
            </div>
            {terminalOutcome !== null && (
              <div className="mt-2 rounded-[10px] border border-subtle bg-raised p-4">
                <h3 className="text-xs font-medium tracking-wide text-secondary">最终结论</h3>
                <p className="mt-2 font-serif text-sm leading-relaxed whitespace-pre-wrap">
                  {terminalOutcome.summary}
                </p>
                {terminalOutcome.unresolved_items.length > 0 && (
                  <div className="mt-3">
                    <h4 className="text-xs text-secondary">未解决项</h4>
                    <ul className="mt-1 list-disc pl-5 text-xs text-secondary">
                      {terminalOutcome.unresolved_items.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </dl>
        ) : artifacts === null ? (
          <EmptyState title="加载中…" />
        ) : (
          <ArtifactList artifacts={artifacts} emptyTitle="该任务暂无 Artifact" />
        )}
      </div>
    </section>
  )
}
