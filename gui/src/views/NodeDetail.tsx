import { useEffect, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { AgentRunObservationWire, NodeViewWire } from '../api/types'
import { ArtifactList } from '../components/ArtifactList'
import { StatusDot } from '../components/StatusDot'
import type { RevisionNodeInfo } from './lib/graph'
import { formatTimestamp, shortId } from './lib/labels'

function usageLine(observation: AgentRunObservationWire): string | null {
  const metrics = observation.terminal_metrics
  if (metrics === null) return null
  const parts: string[] = []
  if (metrics.usage.availability === 'available') {
    const usage = metrics.usage
    const tokens = [
      usage.input_tokens !== null ? `输入 ${usage.input_tokens}` : null,
      usage.output_tokens !== null ? `输出 ${usage.output_tokens}` : null,
      usage.total_tokens !== null ? `共 ${usage.total_tokens}` : null,
    ].filter((part) => part !== null)
    if (tokens.length > 0) parts.push(`tokens ${tokens.join(' / ')}`)
  }
  if (
    metrics.cost.availability === 'available' &&
    metrics.cost.amount_minor !== null &&
    metrics.cost.currency !== null
  ) {
    parts.push(`成本 ${metrics.cost.currency} ${(metrics.cost.amount_minor / 100).toFixed(2)}`)
  }
  parts.push(`模型请求 ${metrics.model_attempts}`, `工具调用 ${metrics.tool_calls}`)
  return parts.join(' · ')
}

/**
 * Node detail block below the graph / under the Direct card. Read-only:
 * status, attempt, agent_run_id, effective cap, output bindings, artifacts,
 * and a lazily fetched agent-run metrics line when an AgentRun exists.
 */
export function NodeDetail({
  nodeView,
  revisionNode,
  client,
}: {
  nodeView: NodeViewWire
  revisionNode: RevisionNodeInfo | null
  client: ApiClient
}) {
  const { node } = nodeView
  const [metrics, setMetrics] = useState<string | null>(null)

  useEffect(() => {
    setMetrics(null)
    if (node.agent_run_id === null) return
    let cancelled = false
    client
      .getAgentRun(node.agent_run_id)
      .then((observation) => {
        if (!cancelled) setMetrics(usageLine(observation))
      })
      .catch(() => {
        // Metrics are best-effort detail; the node facts above stay rendered.
      })
    return () => {
      cancelled = true
    }
  }, [client, node.agent_run_id])

  return (
    <section
      aria-label={`节点 ${node.node_id} 详情`}
      className="rounded-[10px] border border-subtle bg-raised p-4 text-sm"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <StatusDot status={node.status} />
        <span className="font-mono text-xs text-secondary">{node.node_id}</span>
        {node.attempt > 1 && (
          <span className="rounded-[8px] border border-subtle px-1.5 py-0.5 font-mono text-xs text-secondary">
            尝试 {node.attempt}
          </span>
        )}
        {nodeView.approval_pending && (
          <span className="rounded-[8px] border border-blocked px-1.5 py-0.5 text-xs text-blocked">
            待审批
          </span>
        )}
        {revisionNode?.access_mode != null && (
          <span className="text-xs text-secondary">
            {revisionNode.access_mode === 'write' ? '可写入' : '只读'}
          </span>
        )}
      </div>

      <dl className="mt-3 flex flex-col gap-1.5 text-xs">
        {revisionNode?.model != null && (
          <div className="flex gap-2">
            <dt className="w-24 shrink-0 text-secondary">模型</dt>
            <dd className="font-mono">{revisionNode.model}</dd>
          </div>
        )}
        <div className="flex gap-2">
          <dt className="w-24 shrink-0 text-secondary">agent_run</dt>
          <dd className="font-mono">
            {node.agent_run_id !== null ? shortId(node.agent_run_id) : '—'}
          </dd>
        </div>
        <div className="flex gap-2">
          <dt className="w-24 shrink-0 text-secondary">请求上限</dt>
          <dd className="font-mono">{node.effective_node_generation_request_cap ?? '—'}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="w-24 shrink-0 text-secondary">开始 / 完成</dt>
          <dd className="font-mono">
            {formatTimestamp(node.started_at)} / {formatTimestamp(node.completed_at)}
          </dd>
        </div>
        {metrics !== null && (
          <div className="flex gap-2">
            <dt className="w-24 shrink-0 text-secondary">运行指标</dt>
            <dd className="font-mono">{metrics}</dd>
          </div>
        )}
      </dl>

      {revisionNode?.objective != null && (
        <p className="mt-3 border-t border-subtle pt-3 font-serif text-sm leading-relaxed">
          {revisionNode.objective}
        </p>
      )}

      {nodeView.output_bindings.length > 0 && (
        <div className="mt-3">
          <h4 className="text-xs font-medium tracking-wide text-secondary">输出绑定</h4>
          <ul className="mt-1 flex flex-col gap-0.5 font-mono text-xs">
            {nodeView.output_bindings.map((binding) => (
              <li key={binding.name}>
                {binding.name} → {shortId(binding.artifact_id)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {nodeView.artifacts.length > 0 && (
        <div className="mt-3">
          <h4 className="mb-2 text-xs font-medium tracking-wide text-secondary">Artifacts</h4>
          <ArtifactList artifacts={nodeView.artifacts} />
        </div>
      )}
    </section>
  )
}
