import { useEffect, useMemo, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { ApprovalWire, WorkflowRunWire } from '../api/types'
import { StatusDot } from '../components/StatusDot'
import { ApprovalDialog } from './ApprovalDialog'
import type { BudgetDisplay } from './lib/budget'
import { formatTimestamp, shortId } from './lib/labels'

/** Drop approvals this client already resolved; the store event also removes them. */
export function filterResolvedApprovals<T extends { approval_id: string }>(
  approvals: T[],
  resolvedIds: ReadonlySet<string>,
): T[] {
  return approvals.filter((approval) => !resolvedIds.has(approval.approval_id))
}

/**
 * Bottom status bar plus the pending-approval strip above it.
 *
 * Each pending approval offers a 处理 button that opens the full §8.5
 * resolution dialog (ApprovalDialog). After a successful resolution the entry
 * is filtered out immediately, and the sync store drops it for good once the
 * `approval.resolved` event lands.
 */
export function ApprovalsBar({
  client,
  run,
  budget,
  pendingApprovals,
}: {
  client: ApiClient
  run: WorkflowRunWire | null
  budget: BudgetDisplay | null
  pendingApprovals: ApprovalWire[]
}) {
  const [openApprovalId, setOpenApprovalId] = useState<string | null>(null)
  const [resolvedIds, setResolvedIds] = useState<ReadonlySet<string>>(new Set())

  const visibleApprovals = useMemo(
    () => filterResolvedApprovals(pendingApprovals, resolvedIds),
    [pendingApprovals, resolvedIds],
  )
  const openApproval =
    pendingApprovals.find((approval) => approval.approval_id === openApprovalId) ?? null

  // A resolution by another client removes the entry; close the dialog then.
  useEffect(() => {
    if (openApprovalId !== null && openApproval === null) {
      setOpenApprovalId(null)
    }
  }, [openApprovalId, openApproval])

  return (
    <>
      {visibleApprovals.length > 0 && (
        <section
          aria-label="待审批"
          className="max-h-48 overflow-y-auto border-t border-subtle bg-raised px-4 py-3"
        >
          <h2 className="text-xs font-medium tracking-wide text-secondary">
            待审批（{visibleApprovals.length}）
          </h2>
          <ul className="mt-2 flex flex-col gap-2">
            {visibleApprovals.map((approval) => (
              <li
                key={approval.approval_id}
                className="rounded-[10px] border border-blocked bg-base p-3"
              >
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
                  <span className="font-mono text-xs text-accent">{approval.tool_name}</span>
                  <span className="text-xs text-secondary">{approval.requested_scope}</span>
                  <button
                    type="button"
                    aria-haspopup="dialog"
                    onClick={() => setOpenApprovalId(approval.approval_id)}
                    className="ml-auto rounded-[8px] border border-accent px-2.5 py-1 text-xs text-accent transition-colors duration-150 hover:bg-base"
                  >
                    处理
                  </button>
                  <span className="font-mono text-xs text-secondary">
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

      {openApproval !== null && (
        <ApprovalDialog
          approval={openApproval}
          client={client}
          onClose={() => setOpenApprovalId(null)}
          onResolved={() =>
            setResolvedIds((ids) => new Set([...ids, openApproval.approval_id]))
          }
        />
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
          待审批 {visibleApprovals.length}
        </span>
      </footer>
    </>
  )
}
