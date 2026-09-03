import type { NodeViewWire } from '../api/types'
import { StatusDot } from '../components/StatusDot'
import type { RevisionNodeInfo } from './lib/graph'

/**
 * Single-node (Direct) runs render as a linear card, not a canvas
 * (roadmap §8.3, design decision). Same information density as a graph node:
 * status dot + label, mono node_id, model, approval badge, attempt.
 */
export function DirectNodeCard({
  nodeView,
  revisionNode,
  selected,
  onSelect,
}: {
  nodeView: NodeViewWire | null
  revisionNode: RevisionNodeInfo
  selected: boolean
  onSelect: () => void
}) {
  const status = nodeView?.node.status ?? 'queued'
  const attempt = nodeView?.node.attempt ?? 0
  const approvalPending = nodeView?.approval_pending ?? false

  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
      className={`flex w-full items-center gap-3 rounded-[10px] border bg-raised px-4 py-3 text-left transition-colors duration-150 ${
        selected ? 'border-accent' : 'border-subtle hover:border-accent'
      }`}
    >
      <StatusDot status={status} className="text-sm" />
      <span className="font-mono text-xs text-secondary">{revisionNode.node_id}</span>
      {revisionNode.model !== null && (
        <span className="text-xs text-secondary">{revisionNode.model}</span>
      )}
      {attempt > 1 && (
        <span className="rounded-[8px] border border-subtle px-1.5 py-0.5 font-mono text-xs text-secondary">
          尝试 {attempt}
        </span>
      )}
      {approvalPending && (
        <span className="rounded-[8px] border border-blocked px-1.5 py-0.5 text-xs text-blocked">
          待审批
        </span>
      )}
      {revisionNode.objective !== null && (
        <span className="ml-auto min-w-0 flex-1 truncate text-right font-serif text-sm text-secondary">
          {revisionNode.objective}
        </span>
      )}
    </button>
  )
}
