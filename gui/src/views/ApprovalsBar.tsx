import type { ApprovalWire, WorkflowRunWire } from '../api/types'
import { StatusDot } from '../components/StatusDot'
import type { BudgetDisplay } from './lib/budget'
import { formatTimestamp, shortId } from './lib/labels'

/**
 * Bottom status bar plus the pending-approval strip above it.
 *
 * The approval surface is display-only in this subplan: tool name, requested
 * scope, bounded mono preview lines, expiry, and an explicit pointer that
 * resolution happens in the CLI (approval resolution lands in Subplan 6).
 */
export function ApprovalsBar({
  run,
  budget,
  pendingApprovals,
}: {
  run: WorkflowRunWire | null
  budget: BudgetDisplay | null
  pendingApprovals: ApprovalWire[]
}) {
  return (
    <>
      {pendingApprovals.length > 0 && (
        <section
          aria-label="待审批"
          className="max-h-48 overflow-y-auto border-t border-subtle bg-raised px-4 py-3"
        >
          <h2 className="text-xs font-medium tracking-wide text-secondary">
            待审批（{pendingApprovals.length}）— 在 CLI 中处理
          </h2>
          <ul className="mt-2 flex flex-col gap-2">
            {pendingApprovals.map((approval) => (
              <li
                key={approval.approval_id}
                className="rounded-[10px] border border-blocked bg-base p-3"
              >
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
                  <span className="font-mono text-xs text-accent">{approval.tool_name}</span>
                  <span className="text-xs text-secondary">{approval.requested_scope}</span>
                  <span className="ml-auto font-mono text-xs text-secondary">
                    {shortId(approval.approval_id)} · 过期 {formatTimestamp(approval.expires_at)}
                  </span>
                </div>
                {approval.preview.length > 0 && (
                  <pre className="mt-2 overflow-x-auto font-mono text-xs text-secondary">
                    {approval.preview.join('\n')}
                  </pre>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <footer className="flex items-center gap-4 border-t border-subtle bg-raised px-4 py-2 text-xs">
        {run === null ? (
          <span className="text-secondary">未选择运行</span>
        ) : (
          <>
            <StatusDot status={run.status} />
            <span className="font-mono text-secondary">{shortId(run.workflow_run_id)}</span>
            {budget !== null && (
              <span className="font-mono text-secondary">
                模型请求 {budget.current}
                {budget.lineage !== null && ` · ${budget.lineage}`}
              </span>
            )}
          </>
        )}
        <span aria-live="polite" className="ml-auto text-secondary">
          待审批 {pendingApprovals.length}
        </span>
      </footer>
    </>
  )
}
